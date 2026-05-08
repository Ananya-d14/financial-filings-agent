"""End-to-end tests for the agent graph using the plain-Python loop.

The full LangGraph integration is exercised by `build_graph()` in production;
for tests we use `run_graph_loop()` which is behaviourally identical and
doesn't require the langgraph package to be installed.

We mock out the LLM (planner / synthesizer / refiner) and the tool
implementations so the test runs in <1s with no external dependencies.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.agent.citation_verifier import CitationVerifyResult, VerifyMethod
from backend.agent.graph import (
    GraphRunResult,
    planner_node,
    reflector_node,
    route_after_reflection,
    run_graph_loop,
    synthesizer_node,
    tools_node,
)
from backend.agent.schemas import (
    Answer,
    Citation,
    Claim,
    Plan,
    ReflectionVerdict,
    SubTask,
    ToolName,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _citation(**overrides: Any) -> Citation:
    base = dict(
        filing_id="filing-1",
        accession_number="0000-1",
        ticker="NVDA",
        form="10-K",
        fiscal_year=2024,
        section="Item 7",
        char_offset_start=0,
        char_offset_end=100,
    )
    base.update(overrides)
    return Citation(**base)  # type: ignore[arg-type]


def _make_plan() -> Plan:
    return Plan(
        query="What was NVDA's FY2024 revenue?",
        sub_tasks=[
            SubTask(
                description="Get NVDA revenue",
                intended_tool=ToolName.XBRL_SQL,
                intended_inputs={
                    "canonical_concept": "revenue",
                    "tickers": ["NVDA"],
                    "fiscal_years": [2024],
                },
            )
        ],
    )


def _make_answer() -> Answer:
    return Answer(
        query="What was NVDA's FY2024 revenue?",
        markdown="NVIDIA reported FY2024 revenue of $60.9B.",
        claims=[
            Claim(
                text="NVIDIA reported FY2024 revenue of $60.9 billion.",
                is_numeric=True,
                numeric_value=60_900_000_000,
                numeric_unit="USD",
                citations=[_citation()],
            )
        ],
        trace_id="trace-test",
        iterations=1,
        used_tools=[ToolName.XBRL_SQL],
    )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class TestRouteAfterReflection:
    def test_complete_with_answer_ends(self):
        state = {"reflection": ReflectionVerdict(is_complete=True), "answer": _make_answer(), "iteration": 1}
        assert route_after_reflection(state) == "end"  # type: ignore[arg-type]

    def test_complete_no_answer_synthesises(self):
        state = {"reflection": ReflectionVerdict(is_complete=True), "answer": None, "iteration": 1}
        assert route_after_reflection(state) == "synthesize"  # type: ignore[arg-type]

    def test_incomplete_with_refined_plan_loops_to_planner(self):
        state = {
            "reflection": ReflectionVerdict(
                is_complete=False, refined_plan=_make_plan()
            ),
            "answer": None,
            "iteration": 1,
        }
        assert route_after_reflection(state) == "planner"  # type: ignore[arg-type]

    def test_incomplete_no_refinement_proceeds(self):
        state = {
            "reflection": ReflectionVerdict(is_complete=False),
            "answer": None,
            "iteration": 1,
        }
        # No refined plan → synthesize anyway with what we have
        assert route_after_reflection(state) == "synthesize"  # type: ignore[arg-type]

    def test_iteration_cap_with_answer_ends(self):
        state = {
            "reflection": ReflectionVerdict(is_complete=False, refined_plan=_make_plan()),
            "answer": _make_answer(),
            "iteration": 5,
        }
        assert route_after_reflection(state) == "end"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Individual node tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPlannerNode:
    async def test_calls_plan_query_when_no_refinement(self, monkeypatch):
        from backend.agent import graph as g

        plan = _make_plan()
        monkeypatch.setattr(g, "plan_query", AsyncMock(return_value=plan))

        state = await planner_node({"query": "x", "iteration": 0})  # type: ignore[arg-type]
        assert state["plan"] == plan

    async def test_uses_refined_plan_on_subsequent_iterations(self, monkeypatch):
        from backend.agent import graph as g

        # If plan_query is called, the test will fail loudly, refined plan should be used directly.
        async def _fail(*_a, **_kw):
            raise AssertionError("plan_query should not be called when refinement exists")

        monkeypatch.setattr(g, "plan_query", _fail)

        refined = _make_plan()
        state: dict[str, Any] = {
            "query": "x",
            "iteration": 1,
            "reflection": ReflectionVerdict(is_complete=False, refined_plan=refined),
        }
        out = await planner_node(state)  # type: ignore[arg-type]
        assert out["plan"] is refined


@pytest.mark.asyncio
class TestToolsNode:
    async def test_runs_each_subtask(self, monkeypatch):
        from backend.agent import graph as g

        async def fake_run_tool(tool, inputs):
            return {"rows": [{"value": 60.9e9}]}

        monkeypatch.setattr(g, "_run_tool", fake_run_tool)

        plan = _make_plan()
        state: dict[str, Any] = {"query": "x", "plan": plan, "iteration": 0}
        out = await tools_node(state)  # type: ignore[arg-type]
        assert len(out["sub_task_results"]) == 1
        assert out["sub_task_results"][0]["tool"] == "xbrl_sql"

    async def test_handles_tool_error(self, monkeypatch):
        from backend.agent import graph as g

        async def fake_run_tool(tool, inputs):
            raise RuntimeError("boom")

        monkeypatch.setattr(g, "_run_tool", fake_run_tool)

        plan = _make_plan()
        state: dict[str, Any] = {"query": "x", "plan": plan, "iteration": 0}
        out = await tools_node(state)  # type: ignore[arg-type]
        assert out["sub_task_results"][0]["error"] == "boom"
        assert out["sub_task_results"][0]["output"] is None

    async def test_no_plan_returns_error(self):
        out = await tools_node({"query": "x", "plan": None})  # type: ignore[arg-type]
        assert out["error"] == "no_plan"


@pytest.mark.asyncio
class TestSynthesizerNode:
    async def test_calls_synthesize(self, monkeypatch):
        from backend.agent import graph as g

        ans = _make_answer()
        monkeypatch.setattr(g, "synthesize", AsyncMock(return_value=ans))

        state = {
            "query": "x",
            "plan": _make_plan(),
            "sub_task_results": [],
            "trace_id": "t",
        }
        out = await synthesizer_node(state)  # type: ignore[arg-type]
        assert out["answer"] is ans

    async def test_no_plan_sets_error(self):
        out = await synthesizer_node({"query": "x", "plan": None})  # type: ignore[arg-type]
        assert out["error"] == "no_plan_at_synth"


# ---------------------------------------------------------------------------
# Full loop, happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunGraphLoop:
    async def test_happy_path_single_iteration(self, monkeypatch):
        """Plan once, run tools, reflect, synthesize, reflect → done."""
        from backend.agent import graph as g

        plan = _make_plan()
        ans = _make_answer()

        monkeypatch.setattr(g, "plan_query", AsyncMock(return_value=plan))

        async def fake_run_tool(tool, inputs):
            return {"rows": [{"ticker": "NVDA", "value": 60.9e9}]}

        monkeypatch.setattr(g, "_run_tool", fake_run_tool)
        monkeypatch.setattr(g, "synthesize", AsyncMock(return_value=ans))

        # Reflector: pass on both passes
        async def fake_reflect(query, plan, sub_task_results, answer, iteration, session):
            return ReflectionVerdict(is_complete=True)

        monkeypatch.setattr(g, "reflect", fake_reflect)

        # Stub get_session to a no-op async context
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_session():
            yield None

        from backend.db import session as session_mod
        monkeypatch.setattr(session_mod, "get_session", fake_session)

        result = await run_graph_loop("What was NVDA's FY2024 revenue?")
        assert isinstance(result, GraphRunResult)
        assert result.answer is ans

    async def test_loop_with_refinement_then_pass(self, monkeypatch):
        """First pass fails with refined plan; second pass succeeds."""
        from backend.agent import graph as g

        plan_v1 = _make_plan()
        plan_v2 = _make_plan()
        plan_v2.sub_tasks[0].description = "refined"
        ans = _make_answer()

        plan_query_mock = AsyncMock(return_value=plan_v1)
        monkeypatch.setattr(g, "plan_query", plan_query_mock)

        async def fake_run_tool(tool, inputs):
            return {"rows": []}  # empty → would fail check_plan_completeness

        monkeypatch.setattr(g, "_run_tool", fake_run_tool)
        monkeypatch.setattr(g, "synthesize", AsyncMock(return_value=ans))

        # First reflect: post-tools, returns refined plan
        # Second reflect: post-tools (rerun), returns complete
        # Third reflect: post-synth, returns complete
        verdicts = iter(
            [
                ReflectionVerdict(is_complete=False, refined_plan=plan_v2),
                ReflectionVerdict(is_complete=True),
                ReflectionVerdict(is_complete=True),
            ]
        )

        async def fake_reflect(query, plan, sub_task_results, answer, iteration, session):
            return next(verdicts)

        monkeypatch.setattr(g, "reflect", fake_reflect)

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_session():
            yield None

        from backend.db import session as session_mod
        monkeypatch.setattr(session_mod, "get_session", fake_session)

        result = await run_graph_loop("x")
        assert result.answer is ans
        # plan_query called once initially; second iteration reused the refined plan
        assert plan_query_mock.call_count == 1
