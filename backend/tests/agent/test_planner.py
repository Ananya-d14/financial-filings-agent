"""Tests for the planner. Tier-1 routing heuristic and full plan_query call."""

from __future__ import annotations

from typing import Any

import pytest

from backend.agent import llm as llm_module
from backend.agent.llm import LLMResponse
from backend.agent.planner import _is_likely_tier_1, plan_query
from backend.agent.schemas import Plan, SubTask, ToolName


class _StubLLM:
    """Records calls; returns canned JSON responses."""

    def __init__(self, response_obj: Any) -> None:
        self.response_obj = response_obj
        self.calls: list[dict[str, Any]] = []

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: Any,
        model_role: str = "primary",
        **kwargs: Any,
    ) -> tuple[Any, LLMResponse]:
        self.calls.append({"role": model_role, "messages": messages})
        return self.response_obj, LLMResponse(
            text="", model="stub", provider="stub", used_fallback=False
        )


# ===========================================================================
# Tier-1 heuristic
# ===========================================================================


class TestTier1Detector:
    def test_simple_lookup_is_tier_1(self):
        assert _is_likely_tier_1("What was NVDA's FY2024 revenue?") is True

    def test_comparison_is_not_tier_1(self):
        assert _is_likely_tier_1("Compare MSFT and AAPL revenue 2020-2024") is False

    def test_multi_ticker_is_not_tier_1(self):
        assert _is_likely_tier_1("How much did MSFT and AAPL earn?") is False

    def test_growth_question_is_not_tier_1(self):
        assert _is_likely_tier_1("What was Tesla's revenue growth in 2023?") is False

    def test_long_query_is_not_tier_1(self):
        long_q = "Tell me about the various risk factors and operational details for NVDA"
        assert _is_likely_tier_1(long_q) is False

    def test_cagr_question_not_tier_1(self):
        assert _is_likely_tier_1("What is NVDA's revenue CAGR?") is False

    def test_versus_keyword(self):
        assert _is_likely_tier_1("MSFT vs GOOGL revenue") is False

    def test_no_ticker_and_no_keyword(self):
        # Pure narrative question without single-fact pattern
        assert _is_likely_tier_1("Tell me about AI risks") is False


# ===========================================================================
# plan_query, full path with stubbed LLM
# ===========================================================================


@pytest.mark.asyncio
class TestPlanQuery:
    async def test_returns_plan_from_llm(self, monkeypatch):
        plan = Plan(
            query="What was NVDA's FY2024 revenue?",
            sub_tasks=[
                SubTask(
                    description="Look up NVDA FY2024 revenue from XBRL",
                    intended_tool=ToolName.XBRL_SQL,
                    intended_inputs={
                        "canonical_concept": "revenue",
                        "tickers": ["NVDA"],
                        "fiscal_years": [2024],
                    },
                )
            ],
            rationale="Single-fact lookup",
        )
        stub = _StubLLM(plan)
        monkeypatch.setattr(llm_module, "get_llm", lambda: stub)

        from backend.agent import planner as planner_mod
        monkeypatch.setattr(planner_mod, "get_llm", lambda: stub)

        result = await plan_query("What was NVDA's FY2024 revenue?")
        assert result.sub_tasks[0].intended_tool == ToolName.XBRL_SQL
        # Routed to cheap model
        assert stub.calls[0]["role"] == "cheap"

    async def test_complex_query_routed_to_primary(self, monkeypatch):
        plan = Plan(
            query="Compare MSFT, GOOGL, AAPL gross margins 2020-2024",
            sub_tasks=[
                SubTask(
                    description="Get gross profit",
                    intended_tool=ToolName.XBRL_SQL,
                    intended_inputs={"canonical_concept": "gross_profit"},
                ),
                SubTask(
                    description="Get revenue",
                    intended_tool=ToolName.XBRL_SQL,
                    intended_inputs={"canonical_concept": "revenue"},
                ),
                SubTask(
                    description="Compute margin",
                    intended_tool=ToolName.CALCULATOR,
                    intended_inputs={"operation": "margin", "operands": [1, 2]},
                ),
            ],
            rationale="Multi-company comparison",
        )
        stub = _StubLLM(plan)

        from backend.agent import planner as planner_mod
        monkeypatch.setattr(planner_mod, "get_llm", lambda: stub)

        result = await plan_query("Compare MSFT, GOOGL, AAPL gross margins 2020-2024")
        assert len(result.sub_tasks) == 3
        assert stub.calls[0]["role"] == "primary"

    async def test_empty_plan_falls_back_to_filing_retriever(self, monkeypatch):
        empty_plan = Plan(query="x", sub_tasks=[])
        stub = _StubLLM(empty_plan)

        from backend.agent import planner as planner_mod
        monkeypatch.setattr(planner_mod, "get_llm", lambda: stub)

        result = await plan_query("Some weird question with no clear path")
        assert len(result.sub_tasks) == 1
        assert result.sub_tasks[0].intended_tool == ToolName.FILING_RETRIEVER
