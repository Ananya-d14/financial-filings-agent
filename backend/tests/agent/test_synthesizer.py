"""Tests for the synthesizer: evidence rendering and Answer assembly."""

from __future__ import annotations

from typing import Any

import pytest

from backend.agent.llm import LLMResponse
from backend.agent.schemas import (
    Answer,
    Citation,
    Claim,
    Plan,
    SubTask,
    ToolName,
)
from backend.agent.synthesizer import (
    _render_evidence_summary,
    _render_plan_summary,
    synthesize,
)


def _citation() -> Citation:
    return Citation(
        filing_id="f1",
        accession_number="0000-1",
        ticker="NVDA",
        form="10-K",
        fiscal_year=2024,
        section="Item 7",
        char_offset_start=0,
        char_offset_end=100,
    )


def _make_plan() -> Plan:
    return Plan(
        query="x",
        sub_tasks=[
            SubTask(
                description="Get NVDA revenue",
                intended_tool=ToolName.XBRL_SQL,
                intended_inputs={},
            ),
            SubTask(
                description="Calculate growth",
                intended_tool=ToolName.CALCULATOR,
                intended_inputs={},
            ),
        ],
    )


# ===========================================================================
# Plan rendering
# ===========================================================================


class TestRenderPlanSummary:
    def test_basic(self):
        out = _render_plan_summary(_make_plan())
        assert "1." in out and "2." in out
        assert "xbrl_sql" in out
        assert "calculator" in out

    def test_empty_plan(self):
        out = _render_plan_summary(Plan(query="x", sub_tasks=[]))
        assert "(no plan)" in out


# ===========================================================================
# Evidence rendering
# ===========================================================================


class TestRenderEvidenceSummary:
    def test_xbrl_rows_rendered(self):
        results = [
            {
                "tool": "xbrl_sql",
                "output": {
                    "rows": [
                        {
                            "ticker": "NVDA", "canonical_concept": "revenue",
                            "fiscal_year": 2024, "fiscal_period": "FY",
                            "value": 60_900_000_000.0, "unit": "USD",
                            "accession_number": "0000-1",
                        }
                    ],
                },
            }
        ]
        out = _render_evidence_summary(results)
        assert "NVDA" in out
        assert "60900000000" in out or "60_900_000_000" in out or "6.09" in out

    def test_filing_retriever_rendered(self):
        results = [
            {
                "tool": "filing_retriever",
                "output": {
                    "results": [
                        {
                            "ticker": "NVDA", "form": "10-K", "fiscal_year": 2024,
                            "section": "Item 7", "filing_id": "f1",
                            "char_offset_start": 100, "char_offset_end": 300,
                            "text": "Revenue grew significantly during fiscal 2024.",
                        }
                    ]
                },
            }
        ]
        out = _render_evidence_summary(results)
        assert "Revenue grew significantly" in out
        assert "f1" in out
        assert "100-300" in out

    def test_calculator_rendered(self):
        results = [
            {
                "tool": "calculator",
                "output": {"result": 50.0, "formula": "(150 - 100) / 100 * 100 = 50.0%"},
            }
        ]
        out = _render_evidence_summary(results)
        assert "50.0" in out
        assert "formula" in out.lower()

    def test_filing_diff_rendered(self):
        results = [
            {
                "tool": "filing_diff",
                "output": {
                    "summary": "NVDA Item 1A: 2023 -> 2024. 2 additions, 0 removals",
                    "additions": ["AI export controls add new risk."],
                    "removals": [],
                    "common_count": 5,
                },
            }
        ]
        out = _render_evidence_summary(results)
        assert "AI export controls" in out
        assert "2 additions" in out

    def test_unknown_tool(self):
        results = [{"tool": "weird_tool", "output": {"x": 1}}]
        out = _render_evidence_summary(results)
        assert "weird_tool" in out

    def test_none_output(self):
        results = [{"tool": "xbrl_sql", "output": None}]
        out = _render_evidence_summary(results)
        assert "(no output)" in out

    def test_truncates_long_chunks(self):
        long_text = "x" * 5000
        results = [
            {
                "tool": "filing_retriever",
                "output": {
                    "results": [
                        {
                            "ticker": "NVDA", "form": "10-K", "fiscal_year": 2024,
                            "section": "Item 7", "filing_id": "f1",
                            "char_offset_start": 0, "char_offset_end": 100,
                            "text": long_text,
                        }
                    ]
                },
            }
        ]
        out = _render_evidence_summary(results, max_chunk_chars=200)
        # Roughly: 200 chars of x + some boilerplate, NOT 5000
        assert len(out) < 1000


# ===========================================================================
# Full synthesize() with stubbed LLM
# ===========================================================================


class _StubLLM:
    def __init__(self, answer: Answer) -> None:
        self.answer = answer

    async def chat_json(self, **kwargs):
        return self.answer, LLMResponse(
            text="", model="stub", provider="stub", used_fallback=False
        )


@pytest.mark.asyncio
class TestSynthesize:
    async def test_returns_answer_with_trace_id_filled(self, monkeypatch):
        ans = Answer(
            query="x",
            markdown="A",
            claims=[Claim(text="x", is_numeric=False)],
            trace_id="",   # synthesizer should fill this
            iterations=0,
            used_tools=[],
        )
        stub = _StubLLM(ans)

        from backend.agent import synthesizer as syn_mod
        monkeypatch.setattr(syn_mod, "get_llm", lambda: stub)

        out = await synthesize(
            query="x",
            plan=_make_plan(),
            sub_task_results=[],
            trace_id="trace-xyz",
        )
        assert out.trace_id == "trace-xyz"
        assert out.iterations >= 1
        # used_tools populated from the plan
        assert ToolName.XBRL_SQL in out.used_tools

    async def test_used_tools_deduplicated(self, monkeypatch):
        ans = Answer(
            query="x",
            markdown="A",
            claims=[],
            trace_id="",
            iterations=0,
            used_tools=[],
        )
        stub = _StubLLM(ans)

        from backend.agent import synthesizer as syn_mod
        monkeypatch.setattr(syn_mod, "get_llm", lambda: stub)

        # Plan with two XBRL_SQL sub-tasks -> should appear once in used_tools
        plan = Plan(
            query="x",
            sub_tasks=[
                SubTask(description="a", intended_tool=ToolName.XBRL_SQL, intended_inputs={}),
                SubTask(description="b", intended_tool=ToolName.XBRL_SQL, intended_inputs={}),
            ],
        )
        out = await synthesize(query="x", plan=plan, sub_task_results=[])
        assert out.used_tools.count(ToolName.XBRL_SQL) == 1
