"""Per-configuration pipeline runner for the ablation study.

Five configurations tested in increasing complexity:

  1. Vanilla RAG     . BM25 only, direct synthesis (no agentic planning).
  2. + Hybrid        . BM25 + dense vectors + RRF + reranker.
  3. + XBRL tools    . Hybrid + structured xbrl_sql for numeric questions.
  4. + Agentic       . Full LangGraph planner + all tools, no reflection.
  5. + Reflection    . Full system with up to 3 reflection iterations.

Configs 1-3 bypass the agent graph and run a simplified straight-line
pipeline. Configs 4-5 use `run_graph_loop()` with reflection toggled.
This matches the brief's ablation spec and produces a fair comparison.

Rate-limit awareness
--------------------
The Groq free tier caps at ~30 RPM. With 100 questions × 5 configs = 500
inference calls, each run needs backoff. The runner sleeps between questions
if needed. Set `min_gap_ms` to throttle; 0 disables it.

RAGAS integration
-----------------
If `ragas_enabled=True` and ragas is installed, RAGAS metrics are computed
per question using the retrieved context. Requires the `GROQ_API_KEY` to
be set (RAGAS makes LLM calls under the hood).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from backend.agent.schemas import Answer, Claim, ToolName
from backend.eval.metrics import (
    FailureMode,
    QuestionResult,
    citation_faithfulness_score,
    classify_failure,
    numerical_accuracy,
)
from backend.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Ablation configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalConfig:
    name: str
    use_dense: bool         # False = BM25-only
    use_reranker: bool
    use_xbrl: bool          # allow xbrl_sql tool
    use_agentic: bool       # use the LangGraph planner
    use_reflection: bool    # enable reflection loop


ABLATION_CONFIGS: list[EvalConfig] = [
    EvalConfig("Vanilla RAG",         use_dense=False, use_reranker=False, use_xbrl=False, use_agentic=False, use_reflection=False),
    EvalConfig("+ Hybrid retrieval",  use_dense=True,  use_reranker=True,  use_xbrl=False, use_agentic=False, use_reflection=False),
    EvalConfig("+ XBRL tools",        use_dense=True,  use_reranker=True,  use_xbrl=True,  use_agentic=False, use_reflection=False),
    EvalConfig("+ Agentic planning",  use_dense=True,  use_reranker=True,  use_xbrl=True,  use_agentic=True,  use_reflection=False),
    EvalConfig("+ Reflection loop",   use_dense=True,  use_reranker=True,  use_xbrl=True,  use_agentic=True,  use_reflection=True),
]


# ---------------------------------------------------------------------------
# Simplified straight-line pipelines (configs 1-3)
# ---------------------------------------------------------------------------


async def _run_vanilla_rag(
    question: str,
    ticker_filters: list[str] | None,
    year_filters: list[int] | None,
    use_dense: bool,
    use_reranker: bool,
    use_xbrl: bool,
) -> tuple[Answer | None, list[str]]:
    """Direct retrieval + synthesis pipeline, no planning loop."""
    from backend.agent.llm import get_llm
    from backend.agent.prompts import SYNTHESIZER_SYSTEM, SYNTHESIZER_USER_TEMPLATE
    from backend.agent.schemas import Answer, Plan, SubTask, ToolName
    from backend.agent.synthesizer import _render_evidence_summary
    from backend.agent.tool_schemas import FilingRetrieverInput, XBRLSQLInput
    from backend.agent.tools import filing_retriever_tool, xbrl_sql_tool

    tool_errors: list[str] = []
    results: list[dict[str, Any]] = []

    # Retrieval
    try:
        req = FilingRetrieverInput(
            query=question,
            top_k=20,
            rerank=use_reranker,
            rerank_top_k=10,
            ticker=ticker_filters,
            fiscal_year=year_filters,
        )
        from backend.retrieval.hybrid_retriever import _bm25_search, _dense_search_sync, _rrf_merge, _enrich_chunks, RetrievalResult
        from backend.db.session import get_session

        if use_dense:
            retrieved = await filing_retriever_tool(req)
        else:
            # BM25 only
            from backend.db.session import get_session

            filters: dict[str, Any] = {}
            if ticker_filters:
                filters["ticker"] = ticker_filters
            if year_filters:
                filters["fiscal_year"] = year_filters

            async with get_session() as session:
                bm25_rows = await _bm25_search(question, top_k=20, filters=filters, session=session)

            # Wrap into the same output shape as filing_retriever_tool
            from backend.agent.tool_schemas import FilingRetrieverOutput, RetrievalResultDTO

            dtos = []
            for i, row in enumerate(bm25_rows[:10]):
                dtos.append(
                    RetrievalResultDTO(
                        chunk_id=str(row.get("chunk_id", "")),
                        filing_id=str(row.get("filing_id", "")),
                        ticker=row.get("ticker", ""),
                        fiscal_year=row.get("fiscal_year", 0),
                        form=row.get("form", ""),
                        section=row.get("section", ""),
                        item_label=row.get("item_label"),
                        char_offset_start=row.get("char_offset_start", 0),
                        char_offset_end=row.get("char_offset_end", 0),
                        text=row.get("text", ""),
                        rrf_score=float(row.get("score", 0)),
                    )
                )
            retrieved = FilingRetrieverOutput(query=question, n_results=len(dtos), results=dtos)

        results.append({"tool": "filing_retriever", "output": retrieved.model_dump()})
    except Exception as exc:
        log.error("runner.retrieval_error", error=str(exc))
        tool_errors.append(f"retrieval: {exc}")

    # XBRL for numeric questions (optional)
    if use_xbrl and not tool_errors:
        try:
            xbrl_req = XBRLSQLInput(
                canonical_concept="revenue",
                tickers=ticker_filters,
                fiscal_years=year_filters,
            )
            xbrl_out = await xbrl_sql_tool(xbrl_req)
            if xbrl_out.rows:
                results.append({"tool": "xbrl_sql", "output": xbrl_out.model_dump()})
        except Exception as exc:
            # XBRL failure is non-fatal, proceed with retrieval only
            log.debug("runner.xbrl_skipped", error=str(exc))

    # Synthesis
    plan = Plan(
        query=question,
        sub_tasks=[SubTask(description="Direct retrieval", intended_tool=ToolName.FILING_RETRIEVER, intended_inputs={})],
    )
    evidence_summary = _render_evidence_summary(results)
    plan_summary = "Direct retrieval (non-agentic baseline)"

    try:
        llm = get_llm()
        answer, _ = await llm.chat_json(
            messages=[
                {"role": "system", "content": SYNTHESIZER_SYSTEM},
                {"role": "user", "content": SYNTHESIZER_USER_TEMPLATE.format(
                    query=question,
                    plan_summary=plan_summary,
                    evidence_summary=evidence_summary,
                )},
            ],
            schema=Answer,
            model_role="primary",
            temperature=0.0,
            max_tokens=2048,
        )
        # Fill in required fields
        answer = answer.model_copy(update={
            "query": question,
            "trace_id": "",
            "iterations": 1,
            "used_tools": [ToolName.FILING_RETRIEVER],
        })
        return answer, tool_errors
    except Exception as exc:
        log.error("runner.synthesis_error", error=str(exc))
        return None, tool_errors + [f"synthesis: {exc}"]


async def _run_agentic(
    question: str,
    ticker_filters: list[str] | None,
    year_filters: list[int] | None,
    use_reflection: bool,
) -> tuple[Answer | None, list[str]]:
    """Full agent graph. Reflection toggled via MAX_ITERATIONS patch."""
    from backend.agent import reflector as refl_mod
    from backend.agent.graph import run_graph_loop

    original_max = refl_mod.MAX_ITERATIONS
    if not use_reflection:
        refl_mod.MAX_ITERATIONS = 1  # type: ignore[attr-defined]

    try:
        result = await run_graph_loop(query=question)
        errors = []
        if result.state.get("error"):
            errors.append(result.state["error"])
        return result.answer, errors
    except Exception as exc:
        return None, [str(exc)]
    finally:
        refl_mod.MAX_ITERATIONS = original_max  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Single-question evaluator
# ---------------------------------------------------------------------------


async def evaluate_question(
    question_item: dict[str, Any],
    config: EvalConfig,
    run_judge: bool = False,
) -> QuestionResult:
    """Run one question through the specified config pipeline; return scored result."""
    qid = question_item["id"]
    tier = question_item["tier"]
    question = question_item["question"]
    gold_answer = question_item.get("gold_answer")
    gold_numeric = question_item.get("gold_numeric")
    ticker_filters = question_item.get("ticker_filters")
    year_filters = question_item.get("year_filters")

    result = QuestionResult(
        question_id=qid,
        tier=tier,
        question=question,
        config=config.name,
    )

    start = time.monotonic()

    if config.use_agentic:
        answer, tool_errors = await _run_agentic(
            question=question,
            ticker_filters=ticker_filters,
            year_filters=year_filters,
            use_reflection=config.use_reflection,
        )
    else:
        answer, tool_errors = await _run_vanilla_rag(
            question=question,
            ticker_filters=ticker_filters,
            year_filters=year_filters,
            use_dense=config.use_dense,
            use_reranker=config.use_reranker,
            use_xbrl=config.use_xbrl,
        )

    result.latency_ms = int((time.monotonic() - start) * 1000)
    result.tool_errors = tool_errors
    result.answer = answer

    if answer is not None:
        result.input_tokens = 0  # populated from LLM resp if wired
        result.output_tokens = 0
        result.citation_faithfulness = citation_faithfulness_score(answer)
        used_tools = [t.value for t in answer.used_tools]
    else:
        used_tools = []

    # Numerical accuracy
    num_match, num_err = numerical_accuracy(result, gold_numeric)
    result.numerical_match = num_match
    result.numerical_error_pct = num_err

    # Failure taxonomy
    result.failure_mode = classify_failure(
        question=question_item,
        answer=answer,
        numerical_match=num_match,
        tool_errors=tool_errors,
        used_tools=used_tools,
    )

    # Optional LLM judge
    if run_judge and answer is not None and gold_answer:
        from backend.eval.llm_judge import judge_answer
        try:
            judge = await judge_answer(
                question=question,
                gold_answer=gold_answer,
                model_answer=answer.markdown,
            )
            result.judge_score = judge.score
        except Exception as exc:
            log.warning("runner.judge_error", error=str(exc))

    log.info(
        "runner.question_done",
        id=qid,
        config=config.name,
        latency_ms=result.latency_ms,
        numerical_match=result.numerical_match,
        failure=result.failure_mode.value,
    )
    return result
