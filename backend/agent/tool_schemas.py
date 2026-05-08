"""Pydantic input/output schemas for the five agent tools.

Kept separate from `agent/schemas.py` (which holds agent-state types like
`Citation`, `Plan`, `Answer`) to avoid bloating either file.

Every tool's input is a typed BaseModel that the LLM can target via JSON
mode. Outputs are also typed, the agent's reflection pass can introspect
results without inferring shape.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.agent.calculator import CalculatorOp, CalculatorRequest, CalculatorResult
from backend.agent.schemas import Citation, Claim


# ---------------------------------------------------------------------------
# FilingRetrieverTool
# ---------------------------------------------------------------------------


class FilingRetrieverInput(BaseModel):
    """Hybrid BM25 + dense retrieval over filing chunks."""

    query: str
    top_k: int = Field(default=10, ge=1, le=50)
    rerank: bool = True
    rerank_top_k: int = Field(default=10, ge=1, le=50)
    ticker: list[str] | None = None
    fiscal_year: list[int] | None = None
    form: list[str] | None = None
    section: str | None = None


class RetrievalResultDTO(BaseModel):
    """Serialisable retrieval result, mirrors hybrid_retriever.RetrievalResult."""

    chunk_id: str
    filing_id: str
    ticker: str
    fiscal_year: int
    form: str
    section: str
    item_label: str | None = None
    char_offset_start: int
    char_offset_end: int
    text: str
    rrf_score: float
    rerank_score: float | None = None
    bm25_rank: int | None = None
    dense_rank: int | None = None


class FilingRetrieverOutput(BaseModel):
    query: str
    n_results: int
    results: list[RetrievalResultDTO]


# ---------------------------------------------------------------------------
# XBRLSQLTool
# ---------------------------------------------------------------------------


class XBRLSQLInput(BaseModel):
    """Structured XBRL fact lookup, the tool of choice for any GAAP number."""

    canonical_concept: str       # e.g. 'revenue', 'net_income', 'capex'
    tickers: list[str] | None = None
    fiscal_years: list[int] | None = None
    fiscal_period: str | None = None  # 'FY', 'Q1', 'Q2', 'Q3'; None = all
    form: Literal["10-K", "10-Q"] = "10-K"


class XBRLFactDTO(BaseModel):
    ticker: str
    cik: int
    canonical_concept: str
    fiscal_year: int
    fiscal_period: str
    form: str
    period_start: str | None
    period_end: str
    value: float
    unit: str
    accession_number: str


class XBRLSQLOutput(BaseModel):
    canonical_concept: str
    n_rows: int
    rows: list[XBRLFactDTO]


# ---------------------------------------------------------------------------
# CalculatorTool, re-export from calculator.py for a single import surface
# ---------------------------------------------------------------------------


CalculatorInput = CalculatorRequest
CalculatorOutput = CalculatorResult
__all_calc__ = ("CalculatorInput", "CalculatorOutput", "CalculatorOp")


# ---------------------------------------------------------------------------
# CitationVerifierTool
# ---------------------------------------------------------------------------


class CitationVerifyInput(BaseModel):
    claim: Claim
    citation: Citation
    tolerance_pct: float = Field(default=0.5, ge=0.0)
    semantic_threshold: float = Field(default=0.55, ge=0.0, le=1.0)


# CitationVerifyResult is defined in citation_verifier.py and re-exported below
from backend.agent.citation_verifier import CitationVerifyResult  # noqa: E402

CitationVerifyOutput = CitationVerifyResult


# ---------------------------------------------------------------------------
# FilingDiffTool
# ---------------------------------------------------------------------------


class FilingDiffInput(BaseModel):
    ticker: str
    section: str           # e.g. "Item 1A"
    year_a: int
    year_b: int
    form: Literal["10-K", "10-Q"] = "10-K"


class FilingDiffOutput(BaseModel):
    ticker: str
    section: str
    year_a: int
    year_b: int
    summary: str
    additions: list[str]
    removals: list[str]
    common_count: int
    section_a_filing_id: str | None = None
    section_b_filing_id: str | None = None
