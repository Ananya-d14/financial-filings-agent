"""Smoke tests for the tool dispatcher / registry.

We verify:
  - calculator_tool returns a CalculatorOutput
  - the TOOL_REGISTRY exposes every ToolName the agent will route to
  - list_tools() returns metadata for the UI / debugging
  - Pydantic schemas are stable (round-trip JSON)

The async DB-touching tools (filing_retriever_tool, xbrl_sql_tool,
filing_diff_tool, citation_verifier_tool) are tested indirectly via the
underlying modules' own tests. End-to-end integration with a live DB lands
in agent graph.
"""

from __future__ import annotations

import pytest

from backend.agent.calculator import CalculatorOp
from backend.agent.schemas import Citation, Claim, ToolName
from backend.agent.tool_schemas import (
    CalculatorInput,
    CalculatorOutput,
    CitationVerifyInput,
    FilingDiffInput,
    FilingRetrieverInput,
    RetrievalResultDTO,
    XBRLFactDTO,
    XBRLSQLInput,
    XBRLSQLOutput,
)
from backend.agent.tools import (
    TOOL_REGISTRY,
    calculator_tool,
    list_tools,
)


# ===========================================================================
# Calculator tool, synchronous, no DB
# ===========================================================================


class TestCalculatorTool:
    def test_basic_add(self):
        req = CalculatorInput(operation=CalculatorOp.ADD, operands=[3, 4])
        out = calculator_tool(req)
        assert isinstance(out, CalculatorOutput)
        assert out.result == 7

    def test_yoy_growth(self):
        req = CalculatorInput(operation=CalculatorOp.YOY_GROWTH, operands=[150, 100])
        out = calculator_tool(req)
        assert out.result == 50.0
        assert "%" in out.formula


# ===========================================================================
# Tool registry
# ===========================================================================


class TestToolRegistry:
    def test_registry_has_all_tool_names(self):
        registered = set(TOOL_REGISTRY.keys())
        # Every ToolName the planner can emit must have a callable
        expected = {
            ToolName.CALCULATOR,
            ToolName.HYBRID_RETRIEVER,
            ToolName.FILING_RETRIEVER,
            ToolName.XBRL_SQL,
            ToolName.FILING_DIFF,
        }
        assert expected.issubset(registered)

    def test_each_entry_is_callable(self):
        for name, fn in TOOL_REGISTRY.items():
            assert callable(fn), f"{name} is not callable"

    def test_list_tools_returns_metadata(self):
        tools = list_tools()
        names = {t["name"] for t in tools}
        assert {"calculator", "xbrl_sql", "filing_retriever", "filing_diff", "citation_verifier"}.issubset(names)
        # Each tool must have a non-empty description
        for t in tools:
            assert t["description"]


# ===========================================================================
# Schema round-trips
# ===========================================================================


class TestSchemaRoundTrips:
    def test_filing_retriever_input(self):
        req = FilingRetrieverInput(
            query="Tesla China risk",
            top_k=5,
            ticker=["TSLA"],
            fiscal_year=[2024],
            form=["10-K"],
        )
        roundtrip = FilingRetrieverInput.model_validate_json(req.model_dump_json())
        assert roundtrip == req

    def test_xbrl_sql_input(self):
        req = XBRLSQLInput(
            canonical_concept="revenue",
            tickers=["MSFT", "AAPL"],
            fiscal_years=[2022, 2023, 2024],
            form="10-K",
        )
        roundtrip = XBRLSQLInput.model_validate_json(req.model_dump_json())
        assert roundtrip == req

    def test_filing_diff_input(self):
        req = FilingDiffInput(
            ticker="NVDA", section="Item 1A", year_a=2023, year_b=2024
        )
        roundtrip = FilingDiffInput.model_validate_json(req.model_dump_json())
        assert roundtrip == req

    def test_xbrl_fact_dto(self):
        dto = XBRLFactDTO(
            ticker="MSFT", cik=789019,
            canonical_concept="revenue", fiscal_year=2023, fiscal_period="FY",
            form="10-K", period_start="2022-07-01", period_end="2023-06-30",
            value=211915000000.0, unit="USD",
            accession_number="0000950170-23-027948",
        )
        roundtrip = XBRLFactDTO.model_validate_json(dto.model_dump_json())
        assert roundtrip == dto

    def test_retrieval_result_dto(self):
        dto = RetrievalResultDTO(
            chunk_id="c1", filing_id="f1", ticker="NVDA", fiscal_year=2024,
            form="10-K", section="Item 7", item_label="MD&A",
            char_offset_start=100, char_offset_end=300,
            text="Revenue grew strongly.",
            rrf_score=0.04, rerank_score=2.5, bm25_rank=1, dense_rank=2,
        )
        roundtrip = RetrievalResultDTO.model_validate_json(dto.model_dump_json())
        assert roundtrip == dto

    def test_citation_verify_input(self):
        cit = Citation(
            filing_id="f1", accession_number="0000-1", ticker="MSFT",
            form="10-K", fiscal_year=2023, section="Item 7",
            char_offset_start=0, char_offset_end=100,
        )
        claim = Claim(text="Revenue was $211.9B", is_numeric=True, numeric_value=211.9e9)
        req = CitationVerifyInput(claim=claim, citation=cit)
        roundtrip = CitationVerifyInput.model_validate_json(req.model_dump_json())
        assert roundtrip == req


# ===========================================================================
# Validation, bad inputs are rejected at the schema layer
# ===========================================================================


class TestInputValidation:
    def test_top_k_must_be_positive(self):
        with pytest.raises(Exception):
            FilingRetrieverInput(query="x", top_k=0)

    def test_tolerance_pct_negative_rejected(self):
        cit = Citation(
            filing_id="f1", accession_number="0000-1", ticker="MSFT",
            form="10-K", fiscal_year=2023, section="Item 7",
            char_offset_start=0, char_offset_end=100,
        )
        claim = Claim(text="x", is_numeric=False)
        with pytest.raises(Exception):
            CitationVerifyInput(claim=claim, citation=cit, tolerance_pct=-1.0)

    def test_semantic_threshold_above_one_rejected(self):
        cit = Citation(
            filing_id="f1", accession_number="0000-1", ticker="MSFT",
            form="10-K", fiscal_year=2023, section="Item 7",
            char_offset_start=0, char_offset_end=100,
        )
        claim = Claim(text="x", is_numeric=False)
        with pytest.raises(Exception):
            CitationVerifyInput(claim=claim, citation=cit, semantic_threshold=1.5)
