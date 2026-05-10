"""Tool dispatcher / registry, five typed tools the agent can call.

Each tool is exposed as an async callable that takes a typed Pydantic input
and returns a typed Pydantic output. This module is the agent's single
import surface for tool execution; the LangGraph state machine routes
ToolCall instances here in agent graph.

The tools, in routing-priority order:

  1. CalculatorTool      . deterministic arithmetic
  2. CitationVerifierTool, final verification step on every claim
  3. XBRLSQLTool         . structured GAAP-tagged numbers
  4. FilingRetrieverTool . narrative chunk retrieval (hybrid BM25+dense+rerank)
  5. FilingDiffTool      . paragraph-level YoY section diff

Tools are *deterministic*: no tool calls an LLM. The Calculator never falls
back to "ask the LLM"; the CitationVerifier uses embeddings (deterministic)
plus exact numeric matching, never LLM judgement.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.calculator import calculate as _calculate
from backend.agent.citation_verifier import verify_citation
from backend.agent.filing_diff import diff_sections
from backend.agent.schemas import ToolName
from backend.agent.tool_schemas import (
    CalculatorInput,
    CalculatorOutput,
    CitationVerifyInput,
    CitationVerifyOutput,
    FilingDiffInput,
    FilingDiffOutput,
    FilingRetrieverInput,
    FilingRetrieverOutput,
    RetrievalResultDTO,
    XBRLFactDTO,
    XBRLSQLInput,
    XBRLSQLOutput,
)
from backend.db.session import get_session
from backend.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def calculator_tool(req: CalculatorInput) -> CalculatorOutput:
    """Pure-Python arithmetic. Synchronous; never blocks. No LLM calls."""
    return _calculate(req)


async def filing_retriever_tool(req: FilingRetrieverInput) -> FilingRetrieverOutput:
    from backend.retrieval.hybrid_retriever import retrieve

    filters: dict[str, Any] = {}
    if req.ticker:
        filters["ticker"] = req.ticker
    if req.fiscal_year:
        filters["fiscal_year"] = req.fiscal_year
    if req.form:
        filters["form"] = req.form
    if req.section:
        filters["section"] = req.section

    results = await retrieve(
        query=req.query,
        top_k=req.top_k,
        rerank=req.rerank,
        rerank_top_k=req.rerank_top_k,
        **filters,
    )

    dtos = [
        RetrievalResultDTO(
            chunk_id=r.chunk_id,
            filing_id=r.filing_id,
            ticker=r.ticker,
            fiscal_year=r.fiscal_year,
            form=r.form,
            section=r.section,
            item_label=r.item_label,
            char_offset_start=r.char_offset_start,
            char_offset_end=r.char_offset_end,
            text=r.text,
            rrf_score=r.rrf_score,
            rerank_score=r.rerank_score,
            bm25_rank=r.bm25_rank,
            dense_rank=r.dense_rank,
        )
        for r in results
    ]
    return FilingRetrieverOutput(query=req.query, n_results=len(dtos), results=dtos)


async def xbrl_sql_tool(req: XBRLSQLInput) -> XBRLSQLOutput:
    from backend.retrieval.sql_retriever import query_facts

    async with get_session() as session:
        rows = await query_facts(
            session=session,
            canonical_concept=req.canonical_concept,
            tickers=req.tickers,
            fiscal_years=req.fiscal_years,
            fiscal_period=req.fiscal_period,
            form=req.form,
        )

    dtos = [
        XBRLFactDTO(
            ticker=r.ticker,
            cik=r.cik,
            canonical_concept=r.canonical_concept,
            fiscal_year=r.fiscal_year,
            fiscal_period=r.fiscal_period,
            form=r.form,
            period_start=r.period_start.isoformat() if r.period_start else None,
            period_end=r.period_end.isoformat(),
            value=float(r.value),
            unit=r.unit,
            accession_number=r.accession_number,
        )
        for r in rows
    ]
    return XBRLSQLOutput(
        canonical_concept=req.canonical_concept,
        n_rows=len(dtos),
        rows=dtos,
    )


async def citation_verifier_tool(
    req: CitationVerifyInput,
    session: AsyncSession | None = None,
) -> CitationVerifyOutput:
    """Verify a citation against a claim. If `session` is None, opens its own."""
    if session is not None:
        return await verify_citation(
            claim=req.claim,
            citation=req.citation,
            session=session,
            tolerance_pct=req.tolerance_pct,
            semantic_threshold=req.semantic_threshold,
        )
    async with get_session() as new_session:
        return await verify_citation(
            claim=req.claim,
            citation=req.citation,
            session=new_session,
            tolerance_pct=req.tolerance_pct,
            semantic_threshold=req.semantic_threshold,
        )


async def filing_diff_tool(req: FilingDiffInput) -> FilingDiffOutput:
    async with get_session() as session:
        result = await diff_sections(
            session=session,
            ticker=req.ticker,
            section=req.section,
            year_a=req.year_a,
            year_b=req.year_b,
            form=req.form,
        )

    return FilingDiffOutput(
        ticker=result.ticker,
        section=result.section,
        year_a=result.year_a,
        year_b=result.year_b,
        summary=result.summary,
        additions=result.additions,
        removals=result.removals,
        common_count=result.common_count,
        section_a_filing_id=result.section_a.filing_id if result.section_a else None,
        section_b_filing_id=result.section_b.filing_id if result.section_b else None,
    )


# ---------------------------------------------------------------------------
# Registry, used by the LangGraph router in agent graph
# ---------------------------------------------------------------------------


# Maps ToolName enum -> callable. The callables vary in signature (some sync,
# some async, some take a session) so the router unwraps them appropriately.
TOOL_REGISTRY: dict[ToolName, Any] = {
    ToolName.CALCULATOR: calculator_tool,
    ToolName.HYBRID_RETRIEVER: filing_retriever_tool,
    ToolName.FILING_RETRIEVER: filing_retriever_tool,  # alias
    ToolName.XBRL_SQL: xbrl_sql_tool,
    ToolName.FILING_DIFF: filing_diff_tool,
}


def list_tools() -> list[dict[str, str]]:
    """Return a human-readable list of all registered tools (for debugging / UI)."""
    return [
        {
            "name": "calculator",
            "description": "Deterministic arithmetic. YoY growth, ratios, CAGR, mean/median, safe expression eval.",
        },
        {
            "name": "xbrl_sql",
            "description": "Structured GAAP-tagged financial numbers (revenue, net_income, capex, etc.).",
        },
        {
            "name": "filing_retriever",
            "description": "Hybrid BM25+dense retrieval over filing narrative chunks; reranked by cross-encoder.",
        },
        {
            "name": "filing_diff",
            "description": "Year-over-year paragraph-level diff of a section (e.g., Item 1A risk factors).",
        },
        {
            "name": "citation_verifier",
            "description": "Programmatic verification of (claim, citation). numeric exact-match or semantic similarity.",
        },
    ]
