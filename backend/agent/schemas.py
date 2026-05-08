"""Shared Pydantic schemas for agent state, tool I/O, and final answers.

These are stable contracts used across modules, keeping them in one file
avoids circular imports between tools.py / planner.py / synthesizer.py.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Citations, critical for the entire project. Every numerical claim
# in a final answer must round-trip a Citation through the citation_verifier.
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """A pointer into the source corpus precise enough to programmatically verify."""

    filing_id: str
    accession_number: str
    ticker: str
    form: Literal["10-K", "10-Q", "8-K"]
    fiscal_year: int
    section: str
    item_label: str | None = None
    char_offset_start: int
    char_offset_end: int
    quoted_text: str | None = None  # snippet for UI display only


class Claim(BaseModel):
    """A single factual statement in the synthesised answer.

    Numerical claims MUST have at least one citation; narrative claims SHOULD.
    """

    text: str
    is_numeric: bool
    numeric_value: float | None = None
    numeric_unit: str | None = None
    citations: list[Citation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Plan, the planner agent's typed output.
# ---------------------------------------------------------------------------


class ToolName(str, Enum):
    FILING_RETRIEVER = "filing_retriever"
    XBRL_SQL = "xbrl_sql"
    HYBRID_RETRIEVER = "hybrid_retriever"
    CALCULATOR = "calculator"
    FILING_DIFF = "filing_diff"


class SubTask(BaseModel):
    description: str
    intended_tool: ToolName
    intended_inputs: dict[str, object] = Field(default_factory=dict)


class Plan(BaseModel):
    query: str
    sub_tasks: list[SubTask]
    rationale: str | None = None


# ---------------------------------------------------------------------------
# Tool calls, recorded in agent state for tracing and reflection.
# ---------------------------------------------------------------------------


class ToolCall(BaseModel):
    tool: ToolName
    inputs: dict[str, object]
    outputs: dict[str, object] | None = None
    error: str | None = None
    started_at: date | None = None
    duration_ms: int | None = None


# ---------------------------------------------------------------------------
# Reflection, gate before synthesis.
# ---------------------------------------------------------------------------


class ReflectionVerdict(BaseModel):
    is_complete: bool
    failed_checks: list[str] = Field(default_factory=list)
    refined_plan: Plan | None = None


# ---------------------------------------------------------------------------
# Final answer, what the synthesizer emits and the API returns.
# ---------------------------------------------------------------------------


class Answer(BaseModel):
    query: str
    markdown: str
    claims: list[Claim]
    trace_id: str
    iterations: int
    used_tools: list[ToolName]
    cost_usd: float | None = None
