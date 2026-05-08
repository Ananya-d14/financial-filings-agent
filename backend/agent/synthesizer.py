"""Synthesizer, turns evidence into the final cited Answer.

Takes:
  * the user's original query
  * the executed plan
  * sub-task results (XBRL rows, retrieved chunks, calculator outputs)

Emits:
  * a markdown answer body (detailed, tables for comparisons)
  * a structured `claims` list, each carrying the citations needed for
    programmatic verification by the reflector

Constraint: numbers in the markdown MUST trace to evidence values; the
reflector's citation_verifier runs on every claim and will fail the answer
if a number isn't backed by a real filing.
"""

from __future__ import annotations

import json
from typing import Any

from backend.agent.llm import get_llm
from backend.agent.prompts import SYNTHESIZER_SYSTEM, SYNTHESIZER_USER_TEMPLATE
from backend.agent.schemas import Answer, Plan
from backend.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers, render the plan and evidence into compact prompts
# ---------------------------------------------------------------------------


def _render_plan_summary(plan: Plan) -> str:
    lines = []
    for i, task in enumerate(plan.sub_tasks, 1):
        lines.append(f"{i}. [{task.intended_tool.value}] {task.description}")
    return "\n".join(lines) if lines else "(no plan)"


def _render_evidence_summary(
    sub_task_results: list[dict[str, Any]],
    max_chunk_chars: int = 800,
) -> str:
    """Produce a compact text rendering of all tool outputs.

    For retrieval results we truncate chunk text to keep prompts under control.
    XBRL rows and calculator outputs are small enough to render in full.
    """
    sections: list[str] = []
    for i, result in enumerate(sub_task_results, 1):
        tool = result.get("tool", "unknown")
        output = result.get("output")
        sections.append(f"--- Step {i}: {tool} ---")

        if output is None:
            sections.append("(no output)")
            continue

        if tool == "filing_retriever":
            results = output.get("results", []) if isinstance(output, dict) else []
            for j, r in enumerate(results[:8], 1):
                text = r.get("text", "")
                trunc = text[:max_chunk_chars]
                sections.append(
                    f"  [{j}] {r.get('ticker')} {r.get('form')} {r.get('fiscal_year')} "
                    f"{r.get('section')} (filing_id={r.get('filing_id')}, "
                    f"offsets={r.get('char_offset_start')}-{r.get('char_offset_end')})\n"
                    f"      {trunc}"
                )

        elif tool == "xbrl_sql":
            rows = output.get("rows", []) if isinstance(output, dict) else []
            for r in rows[:30]:
                sections.append(
                    f"  {r.get('ticker')} {r.get('canonical_concept')} "
                    f"FY{r.get('fiscal_year')} {r.get('fiscal_period')}: "
                    f"{r.get('value')} {r.get('unit')} "
                    f"(accession={r.get('accession_number')})"
                )

        elif tool == "calculator":
            sections.append(f"  result: {output.get('result')}")
            sections.append(f"  formula: {output.get('formula')}")

        elif tool == "filing_diff":
            sections.append(f"  summary: {output.get('summary', '')}")
            adds = output.get("additions", [])
            for a in adds[:5]:
                sections.append(f"  + {a[:max_chunk_chars]}")
            rems = output.get("removals", [])
            for r in rems[:5]:
                sections.append(f"  - {r[:max_chunk_chars]}")

        else:
            sections.append(f"  {json.dumps(output, default=str)[:max_chunk_chars]}")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def synthesize(
    query: str,
    plan: Plan,
    sub_task_results: list[dict[str, Any]],
    trace_id: str = "",
) -> Answer:
    """Generate the final Answer from plan + evidence."""
    plan_summary = _render_plan_summary(plan)
    evidence_summary = _render_evidence_summary(sub_task_results)

    llm = get_llm()
    answer, resp = await llm.chat_json(
        messages=[
            {"role": "system", "content": SYNTHESIZER_SYSTEM},
            {
                "role": "user",
                "content": SYNTHESIZER_USER_TEMPLATE.format(
                    query=query,
                    plan_summary=plan_summary,
                    evidence_summary=evidence_summary,
                ),
            },
        ],
        schema=Answer,
        model_role="primary",
        temperature=0.0,
        max_tokens=2048,
    )

    # The model may not set trace_id / iteration / used_tools, fill them in.
    answer = answer.model_copy(
        update={
            "trace_id": trace_id or answer.trace_id or "",
            "iterations": answer.iterations or 1,
            "used_tools": list({t.intended_tool for t in plan.sub_tasks}),
        }
    )

    log.info(
        "synthesizer.answer_emitted",
        n_claims=len(answer.claims),
        n_citations=sum(len(c.citations) for c in answer.claims),
        provider=resp.provider,
        used_fallback=resp.used_fallback,
    )
    return answer
