"""Tests for eval/llm_judge.py. Cohen's kappa and calibration logic."""

from __future__ import annotations

import pytest

from backend.eval.llm_judge import (
    CalibrationReport,
    cohens_kappa,
)


class TestCohensKappa:
    def test_perfect_agreement(self):
        a = [0, 1, 2, 3, 0, 1]
        b = [0, 1, 2, 3, 0, 1]
        assert cohens_kappa(a, b) == 1.0

    def test_complete_disagreement(self):
        # A always gives 0, B always gives 1. p_e = 0, p_o = 0, so kappa = 0.0.
        # (Not negative, because by chance you'd also expect 0 agreement.)
        a = [0, 0, 0, 0, 0]
        b = [1, 1, 1, 1, 1]
        k = cohens_kappa(a, b)
        assert k <= 0.0

    def test_typical_agreement_range(self):
        # Moderate agreement scenario
        a = [3, 3, 2, 1, 0, 3, 2, 2]
        b = [3, 2, 2, 1, 0, 3, 3, 2]
        k = cohens_kappa(a, b)
        assert 0.0 < k < 1.0

    def test_empty_lists_raise(self):
        with pytest.raises(ValueError):
            cohens_kappa([], [])

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            cohens_kappa([1, 2], [1])

    def test_all_same_category_returns_one(self):
        # Everyone gave score 0; p_e = 1.0 -> special case returns 1.0
        a = [0, 0, 0]
        b = [0, 0, 0]
        assert cohens_kappa(a, b) == 1.0

    def test_kappa_is_symmetric(self):
        a = [3, 2, 1, 0, 2, 3]
        b = [3, 3, 1, 0, 2, 2]
        assert abs(cohens_kappa(a, b) - cohens_kappa(b, a)) < 1e-9


class TestCalibrationReport:
    def _report(self, kappa: float) -> CalibrationReport:
        return CalibrationReport(
            kappa=kappa,
            n_samples=30,
            human_scores=[1] * 30,
            judge_scores=[1] * 30,
            agreement_pct=1.0,
            interpretation="perfect",
        )

    def test_passes_threshold_above_04(self):
        assert self._report(0.6).passes_threshold() is True

    def test_fails_threshold_below_04(self):
        assert self._report(0.3).passes_threshold() is False

    def test_markdown_contains_kappa(self):
        md = self._report(0.65).to_markdown()
        assert "0.650" in md or "0.65" in md

    def test_markdown_contains_warning_when_low(self):
        md = self._report(0.25).to_markdown()
        assert "below" in md.lower() or "0.4" in md
