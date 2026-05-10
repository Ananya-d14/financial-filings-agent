"""Tests for the reflector: deterministic checks + refined-plan path."""

from __future__ import annotations

from typing import Any

import pytest

from backend.agent.citation_verifier import CitationVerifyResult, VerifyMethod
from backend.agent.reflector import (
    MAX_ITERATIONS,
    check_numeric_claims_have_citations,
    check_plan_completeness,
    check_citations_verify,
    reflect,
)
from backend.agent.schemas import (
    Answer,
    Citation,
    Claim,
    Plan,
    SubTask,
    ToolName,
)


def _make_citation(filing_id: str = "f1") -> Citation:
    return Citation(
        filing_id=filing_id,
        accession_number="0000-1",
        ticker="NVDA",
        form="10-K",
        fiscal_year=2024,
        section="Item 7",
        char_offset_start=0,
        char_offset_end=100,
    )


def _make_plan(n_tasks: int = 1) -> Plan:
    return Plan(
        query="x",
        sub_tasks=[
            SubTask(
                description=f"task {i}",
                intended_tool=ToolName.XBRL_SQL,
                intended_inputs={"canonical_concept": "revenue"},
            )
            for i in range(n_tasks)
        ],
    )


def _make_answer(claims: list[Claim] | None = None) -> Answer:
    return Answer(
        query="x",
        markdown="answer",
        claims=claims or [],
        trace_id="trace-1",
        iterations=1,
        used_tools=[ToolName.XBRL_SQL],
    )


# ===========================================================================
# Deterministic checks
# ===========================================================================


class TestNumericClaimChecks:
    def test_numeric_claim_with_citation_passes(self):
        ans = _make_answer([
            Claim(text="$96.9B", is_numeric=True, numeric_value=96.9e9, citations=[_make_citation()])
        ])
        assert check_numeric_claims_have_citations(ans) == []

    def test_numeric_claim_no_citation_fails(self):
        ans = _make_answer([
            Claim(text="$96.9B", is_numeric=True, numeric_value=96.9e9, citations=[])
        ])
        failures = check_numeric_claims_have_citations(ans)
        assert len(failures) == 1

    def test_narrative_claim_no_citation_passes(self):
        # Narrative claims aren't required to have citations at this layer.
        ans = _make_answer([Claim(text="Apple is a tech firm.", is_numeric=False)])
        assert check_numeric_claims_have_citations(ans) == []


class TestPlanCompleteness:
    def test_complete(self):
        plan = _make_plan(2)
        results = [{"output": {"rows": [1]}}, {"output": {"rows": [2]}}]
        assert check_plan_completeness(plan, results) == []

    def test_too_few_results(self):
        plan = _make_plan(3)
        results = [{"output": {"rows": [1]}}]  # only 1 of 3
        failures = check_plan_completeness(plan, results)
        assert any("only 1/3 sub-tasks" in f for f in failures)

    def test_empty_result(self):
        plan = _make_plan(1)
        results = [{"output": None}]
        failures = check_plan_completeness(plan, results)
        assert any("returned empty" in f for f in failures)


# ===========================================================================
# Citation verification (with stubbed citation_verifier)
# ===========================================================================


@pytest.mark.asyncio
class TestCitationVerify:
    async def test_all_valid(self, monkeypatch):
        async def fake_verify(claim, citation, session, tolerance_pct=0.5,
                              semantic_threshold=0.55, embedder=None):
            return CitationVerifyResult(
                verified=True,
                method=VerifyMethod.NUMERIC_EXACT,
                confidence=1.0,
            )

        from backend.agent import reflector as r_mod
        monkeypatch.setattr(r_mod, "verify_citation", fake_verify)

        ans = _make_answer([
            Claim(text="x", is_numeric=True, numeric_value=1, citations=[_make_citation()])
        ])
        failures = await check_citations_verify(ans, session=None)  # session unused by stub
        assert failures == []

    async def test_one_invalid(self, monkeypatch):
        async def fake_verify(claim, citation, session, tolerance_pct=0.5,
                              semantic_threshold=0.55, embedder=None):
            return CitationVerifyResult(
                verified=False,
                method=VerifyMethod.NUMERIC_MISMATCH,
                confidence=0.0,
                issues=["target not found"],
            )

        from backend.agent import reflector as r_mod
        monkeypatch.setattr(r_mod, "verify_citation", fake_verify)

        ans = _make_answer([
            Claim(text="x", is_numeric=True, numeric_value=1, citations=[_make_citation()])
        ])
        failures = await check_citations_verify(ans, session=None)
        assert len(failures) == 1


# ===========================================================================
# Full reflect(): combines all checks + refinement path
# ===========================================================================


class _StubLLM:
    def __init__(self, refined_plan: Plan) -> None:
        self.refined = refined_plan
        self.calls = 0

    async def chat_json(self, **kwargs):
        self.calls += 1
        from backend.agent.llm import LLMResponse
        return self.refined, LLMResponse(text="", model="stub", provider="stub", used_fallback=False)


@pytest.mark.asyncio
class TestReflect:
    async def test_passes_when_all_clean(self, monkeypatch):
        async def fake_verify(*a, **k):
            return CitationVerifyResult(
                verified=True, method=VerifyMethod.NUMERIC_EXACT, confidence=1.0
            )

        from backend.agent import reflector as r_mod
        monkeypatch.setattr(r_mod, "verify_citation", fake_verify)

        plan = _make_plan(1)
        results = [{"output": {"rows": [1]}}]
        ans = _make_answer([
            Claim(text="x", is_numeric=True, numeric_value=1, citations=[_make_citation()])
        ])

        verdict = await reflect(
            query="x", plan=plan, sub_task_results=results,
            answer=ans, iteration=0, session=None,
        )
        assert verdict.is_complete is True

    async def test_emits_refined_plan_on_failure(self, monkeypatch):
        # Hard failure: numeric claim missing citations triggers refinement.
        # (Citation verification failures are soft, see test_soft_citation_failures.)
        refined = _make_plan(2)
        stub = _StubLLM(refined)

        from backend.agent import reflector as r_mod
        monkeypatch.setattr(r_mod, "get_llm", lambda: stub)

        plan = _make_plan(1)
        results = [{"output": {"rows": [1]}}]
        ans = _make_answer([
            Claim(text="x", is_numeric=True, numeric_value=1, citations=[])  # no citations
        ])

        verdict = await reflect(
            query="x", plan=plan, sub_task_results=results,
            answer=ans, iteration=0, session=None,
        )
        assert verdict.is_complete is False
        assert verdict.refined_plan is not None
        assert verdict.refined_plan.sub_tasks
        assert stub.calls == 1

    async def test_no_refinement_at_iteration_cap(self, monkeypatch):
        plan = _make_plan(1)
        results = [{"output": {"rows": [1]}}]
        ans = _make_answer([
            Claim(text="x", is_numeric=True, numeric_value=1, citations=[])  # hard fail
        ])

        verdict = await reflect(
            query="x", plan=plan, sub_task_results=results,
            answer=ans, iteration=MAX_ITERATIONS - 1, session=None,
        )
        assert verdict.is_complete is False
        assert verdict.refined_plan is None

    async def test_refine_failure_handled_gracefully(self, monkeypatch):
        class _BrokenLLM:
            async def chat_json(self, **kwargs):
                raise RuntimeError("LLM blew up")

        from backend.agent import reflector as r_mod
        monkeypatch.setattr(r_mod, "get_llm", lambda: _BrokenLLM())

        plan = _make_plan(1)
        results = [{"output": {"rows": [1]}}]
        ans = _make_answer([
            Claim(text="x", is_numeric=True, numeric_value=1, citations=[])  # hard fail
        ])
        verdict = await reflect(
            query="x", plan=plan, sub_task_results=results,
            answer=ans, iteration=0, session=None,
        )
        assert verdict.is_complete is False
        assert verdict.refined_plan is None  # refine failed; we proceed without one

    async def test_pre_synth_runs_only_plan_check(self, monkeypatch):
        """When answer is None, only plan-completeness check runs (no citation verify)."""
        plan = _make_plan(1)
        results = [{"output": {"rows": [1]}}]
        verdict = await reflect(
            query="x", plan=plan, sub_task_results=results,
            answer=None, iteration=0, session=None,
        )
        # Plan complete and no answer to verify -> passes
        assert verdict.is_complete is True
