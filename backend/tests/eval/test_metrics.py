"""Tests for eval/metrics.py, deterministic functions only (no LLM, no DB)."""

from __future__ import annotations

import pytest

from backend.agent.schemas import Answer, Citation, Claim, ToolName
from backend.eval.metrics import (
    EvalSummary,
    FailureMode,
    QuestionResult,
    TierSummary,
    aggregate_results,
    citation_faithfulness_score,
    classify_failure,
    compute_latency_stats,
    format_ablation_row,
    numerical_accuracy,
    render_ablation_table,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cit() -> Citation:
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


def _answer(claims=None) -> Answer:
    return Answer(
        query="q",
        markdown="A",
        claims=claims or [],
        trace_id="t",
        iterations=1,
        used_tools=[ToolName.XBRL_SQL],
    )


def _result(
    qid="q1", tier=1, config="X",
    answer=None, latency_ms=500,
    numerical_match=None, citation_faithfulness=1.0,
    failure_mode=FailureMode.CORRECT,
) -> QuestionResult:
    r = QuestionResult(question_id=qid, tier=tier, question="q", config=config)
    r.answer = answer
    r.latency_ms = latency_ms
    r.numerical_match = numerical_match
    r.citation_faithfulness = citation_faithfulness
    r.failure_mode = failure_mode
    return r


# ---------------------------------------------------------------------------
# numerical_accuracy
# ---------------------------------------------------------------------------


class TestNumericalAccuracy:
    def test_exact_match(self):
        ans = _answer([Claim(text="x", is_numeric=True, numeric_value=96.995e9)])
        r = _result(answer=ans)
        matched, err = numerical_accuracy(r, gold_numeric=96.995e9)
        assert matched is True
        assert err is not None and err < 0.001

    def test_within_tolerance(self):
        ans = _answer([Claim(text="x", is_numeric=True, numeric_value=97.0e9)])
        r = _result(answer=ans)
        matched, err = numerical_accuracy(r, gold_numeric=96.995e9, tolerance_pct=0.5)
        assert matched is True

    def test_outside_tolerance(self):
        ans = _answer([Claim(text="x", is_numeric=True, numeric_value=100.0e9)])
        r = _result(answer=ans)
        matched, err = numerical_accuracy(r, gold_numeric=96.995e9, tolerance_pct=0.5)
        assert matched is False
        assert err is not None and err > 0.5

    def test_no_gold_numeric_returns_none(self):
        r = _result(answer=_answer())
        matched, err = numerical_accuracy(r, gold_numeric=None)
        assert matched is None
        assert err is None

    def test_no_answer_returns_false(self):
        r = _result(answer=None)
        matched, err = numerical_accuracy(r, gold_numeric=100.0)
        assert matched is False
        assert err is None

    def test_no_numeric_claims_returns_false(self):
        ans = _answer([Claim(text="narrative", is_numeric=False)])
        r = _result(answer=ans)
        matched, err = numerical_accuracy(r, gold_numeric=100.0)
        assert matched is False

    def test_any_matching_claim_wins(self):
        # Has a wrong claim AND a correct claim, should return True
        ans = _answer([
            Claim(text="wrong", is_numeric=True, numeric_value=1.0),
            Claim(text="right", is_numeric=True, numeric_value=100.0),
        ])
        r = _result(answer=ans)
        matched, err = numerical_accuracy(r, gold_numeric=100.0, tolerance_pct=0.5)
        assert matched is True


# ---------------------------------------------------------------------------
# citation_faithfulness_score
# ---------------------------------------------------------------------------


class TestCitationFaithfulness:
    def test_all_numeric_cited(self):
        ans = _answer([
            Claim(text="x", is_numeric=True, numeric_value=1, citations=[_cit()]),
        ])
        assert citation_faithfulness_score(ans) == 1.0

    def test_numeric_uncited(self):
        ans = _answer([
            Claim(text="x", is_numeric=True, numeric_value=1, citations=[]),
        ])
        assert citation_faithfulness_score(ans) == 0.0

    def test_half_cited(self):
        ans = _answer([
            Claim(text="x", is_numeric=True, numeric_value=1, citations=[_cit()]),
            Claim(text="y", is_numeric=True, numeric_value=2, citations=[]),
        ])
        assert citation_faithfulness_score(ans) == 0.5

    def test_no_numeric_claims_is_one(self):
        ans = _answer([Claim(text="narrative", is_numeric=False)])
        assert citation_faithfulness_score(ans) == 1.0


# ---------------------------------------------------------------------------
# classify_failure
# ---------------------------------------------------------------------------


class TestClassifyFailure:
    def _q(self, gold_numeric=None):
        return {"gold_numeric": gold_numeric, "question": "q"}

    def test_correct_returns_correct(self):
        assert classify_failure(
            question=self._q(gold_numeric=100.0),
            answer=_answer(),
            numerical_match=True,
            tool_errors=[],
            used_tools=["xbrl_sql"],
        ) == FailureMode.CORRECT

    def test_no_answer_is_planning_failure(self):
        assert classify_failure(
            question=self._q(),
            answer=None,
            numerical_match=None,
            tool_errors=[],
            used_tools=[],
        ) == FailureMode.PLANNING_FAILURE

    def test_tool_error_categorised(self):
        assert classify_failure(
            question=self._q(gold_numeric=100.0),
            answer=_answer(),
            numerical_match=False,
            tool_errors=["xbrl_sql: db error"],
            used_tools=["xbrl_sql"],
        ) == FailureMode.TOOL_ERROR

    def test_numeric_without_xbrl_is_table_parsing(self):
        # Numeric question answered without xbrl_sql tool
        assert classify_failure(
            question=self._q(gold_numeric=100.0),
            answer=_answer(),
            numerical_match=False,
            tool_errors=[],
            used_tools=["filing_retriever"],
        ) == FailureMode.TABLE_PARSING_ERROR

    def test_hallucination_when_no_numeric_claims(self):
        ans = _answer([Claim(text="some text", is_numeric=False)])
        assert classify_failure(
            question=self._q(gold_numeric=100.0),
            answer=ans,
            numerical_match=False,
            tool_errors=[],
            used_tools=["xbrl_sql"],
        ) == FailureMode.HALLUCINATION

    def test_qualitative_correct(self):
        assert classify_failure(
            question=self._q(gold_numeric=None),
            answer=_answer([Claim(text="narrative claim", is_numeric=False)]),
            numerical_match=None,
            tool_errors=[],
            used_tools=["filing_retriever"],
        ) == FailureMode.CORRECT

    def test_empty_tools_is_planning_failure(self):
        # Agent produced an answer but didn't invoke any tool, planning failure.
        assert classify_failure(
            question=self._q(gold_numeric=None),
            answer=_answer(),
            numerical_match=None,
            tool_errors=[],
            used_tools=[],
        ) == FailureMode.PLANNING_FAILURE


# ---------------------------------------------------------------------------
# aggregate_results + latency
# ---------------------------------------------------------------------------


class TestAggregateResults:
    def _make_results(self):
        return [
            _result("q1", tier=1, latency_ms=200, numerical_match=True,
                    citation_faithfulness=1.0, failure_mode=FailureMode.CORRECT),
            _result("q2", tier=1, latency_ms=300, numerical_match=False,
                    citation_faithfulness=0.5, failure_mode=FailureMode.HALLUCINATION),
            _result("q3", tier=2, latency_ms=1000, numerical_match=None,
                    citation_faithfulness=1.0, failure_mode=FailureMode.CORRECT),
            _result("q4", tier=2, latency_ms=800, numerical_match=None,
                    citation_faithfulness=0.0, failure_mode=FailureMode.RETRIEVAL_MISS),
        ]

    def test_summary_has_correct_n(self):
        summary = aggregate_results(self._make_results(), "TestConfig")
        assert summary.n_questions == 4

    def test_tier_accuracy(self):
        summary = aggregate_results(self._make_results(), "X")
        t1 = summary.by_tier[1]
        assert t1.accuracy == 0.5  # 1 correct out of 2 numeric

    def test_overall_accuracy(self):
        summary = aggregate_results(self._make_results(), "X")
        assert summary.overall_accuracy == 0.5  # 1 correct out of 2 numeric

    def test_faithfulness_averaged(self):
        summary = aggregate_results(self._make_results(), "X")
        assert abs(summary.overall_faithfulness - 0.625) < 0.001

    def test_cost_always_zero(self):
        summary = aggregate_results(self._make_results(), "X")
        assert summary.mean_cost_usd == 0.0

    def test_empty_results(self):
        summary = aggregate_results([], "X")
        assert summary.n_questions == 0
        assert summary.overall_accuracy == 0.0


class TestLatencyStats:
    def test_basic(self):
        results = [_result(latency_ms=v) for v in [100, 200, 300, 400, 1000]]
        stats = compute_latency_stats(results)
        assert stats["p50_ms"] == 300.0
        assert stats["p95_ms"] == 1000.0

    def test_empty(self):
        stats = compute_latency_stats([])
        assert stats["p50_ms"] == 0.0
        assert stats["p95_ms"] == 0.0


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


class TestTableRendering:
    def _summary(self, config: str, acc: float = 0.75) -> EvalSummary:
        return EvalSummary(
            config=config,
            n_questions=20,
            by_tier={
                1: TierSummary(1, 5, acc, 1.0, 200, 400, 10, 5),
                2: TierSummary(2, 5, acc - 0.1, 0.9, 300, 500, 12, 6),
                3: TierSummary(3, 5, acc - 0.2, 0.8, 500, 800, 15, 8),
                4: TierSummary(4, 5, acc - 0.3, 0.7, 800, 1200, 20, 10),
            },
            overall_accuracy=acc,
            overall_faithfulness=0.9,
            p50_ms=400,
            p95_ms=800,
        )

    def test_format_row_contains_config_name(self):
        row = format_ablation_row(self._summary("Vanilla RAG"))
        assert "Vanilla RAG" in row

    def test_format_row_contains_accuracy(self):
        row = format_ablation_row(self._summary("X", acc=0.75))
        assert "75%" in row

    def test_render_table_has_header(self):
        table = render_ablation_table([self._summary("A"), self._summary("B")])
        assert "Configuration" in table
        assert "Tier 1 Acc" in table
        assert "A" in table
        assert "B" in table

    def test_render_empty_summaries(self):
        table = render_ablation_table([])
        assert "Configuration" in table  # header always present


# ---------------------------------------------------------------------------
# Benchmark questions JSONL format validation
# ---------------------------------------------------------------------------


def test_benchmark_questions_valid():
    """All stub questions must parse and have required fields."""
    import json
    from pathlib import Path

    path = Path(__file__).parent.parent.parent / "eval" / "benchmark_questions.jsonl"
    assert path.exists(), "benchmark_questions.jsonl not found"

    required = {"id", "tier", "question", "ticker_filters", "year_filters", "tags"}
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        q = json.loads(line)
        missing = required - set(q.keys())
        assert not missing, f"Question {q.get('id')} missing fields: {missing}"
        assert q["tier"] in (1, 2, 3, 4), f"Invalid tier: {q['tier']}"
        assert q["id"] not in ids, f"Duplicate id: {q['id']}"
        ids.add(q["id"])

    # Verify we have all 4 tiers represented
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    tiers_present = {q["tier"] for q in lines}
    assert tiers_present == {1, 2, 3, 4}, f"Missing tiers: {tiers_present}"
