"""Tests for backend.agent.filing_diff, paragraph splitting and diff logic.

DB-touching paths use a fake session; pure helpers (`_split_paragraphs`,
`_diff_paragraphs`) are tested directly.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.agent.filing_diff import (
    DiffResult,
    SectionVersion,
    _diff_paragraphs,
    _fetch_section_version,
    _split_paragraphs,
    diff_sections,
)


# ===========================================================================
# Helpers
# ===========================================================================


class _FakeRow:
    def __init__(self, *vals: Any) -> None:
        self._vals = vals

    def __getitem__(self, idx: int) -> Any:
        return self._vals[idx]


class _FakeResult:
    def __init__(self, row: _FakeRow | None) -> None:
        self._row = row

    def fetchone(self) -> _FakeRow | None:
        return self._row


class _FakeSession:
    def __init__(self, queue: list[_FakeRow | None]) -> None:
        self._q = list(queue)
        self.calls: list[Any] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _FakeResult:
        self.calls.append((str(statement), params))
        return _FakeResult(self._q.pop(0) if self._q else None)


# ===========================================================================
# Paragraph splitter
# ===========================================================================


class TestSplitParagraphs:
    def test_basic_split(self):
        text = "First paragraph here, longer than twenty.\n\nSecond paragraph also long enough."
        paras = _split_paragraphs(text)
        assert len(paras) == 2
        assert paras[0].startswith("First")
        assert paras[1].startswith("Second")

    def test_drops_short_lines(self):
        text = "Item 1A\n\nThis is a substantive paragraph with content."
        paras = _split_paragraphs(text)
        # "Item 1A" is too short, should be dropped
        assert all("Item 1A" != p for p in paras)
        assert len(paras) == 1

    def test_dedupes_repeated_paragraphs(self):
        text = "Risk factor about competition is significant in our market.\n\n" \
               "Risk factor about competition is significant in our market."
        paras = _split_paragraphs(text)
        assert len(paras) == 1

    def test_empty_input(self):
        assert _split_paragraphs("") == []
        assert _split_paragraphs("   ") == []


# ===========================================================================
# Paragraph diff
# ===========================================================================


class TestDiffParagraphs:
    def test_identical(self):
        a = ["paragraph one is the same", "paragraph two is the same"]
        b = ["paragraph one is the same", "paragraph two is the same"]
        adds, rems, common = _diff_paragraphs(a, b)
        assert adds == []
        assert rems == []
        assert common == 2

    def test_pure_addition(self):
        a = ["existing paragraph stays"]
        b = ["existing paragraph stays", "new paragraph added"]
        adds, rems, common = _diff_paragraphs(a, b)
        assert adds == ["new paragraph added"]
        assert rems == []
        assert common == 1

    def test_pure_removal(self):
        a = ["paragraph one", "paragraph two will be removed"]
        b = ["paragraph one"]
        adds, rems, common = _diff_paragraphs(a, b)
        assert adds == []
        assert "paragraph two will be removed" in rems
        assert common == 1

    def test_replacement(self):
        a = ["original wording"]
        b = ["new wording"]
        adds, rems, common = _diff_paragraphs(a, b)
        assert adds == ["new wording"]
        assert rems == ["original wording"]
        assert common == 0

    def test_empty_inputs(self):
        adds, rems, common = _diff_paragraphs([], [])
        assert adds == [] and rems == [] and common == 0


# ===========================================================================
# diff_sections, uses fake session
# ===========================================================================


_SAMPLE_PARAS_2023 = (
    "Competition in our market is intense and growing year over year.\n\n"
    "Supply chain disruptions in semiconductors caused delays in 2023.\n\n"
    "Foreign exchange volatility affected revenue from Asian markets."
)
_SAMPLE_PARAS_2024 = (
    "Competition in our market is intense and growing year over year.\n\n"
    "AI-related export controls represent a new and material risk in 2024.\n\n"
    "Foreign exchange volatility affected revenue from Asian markets."
)


def _section_row(filing_id: str, year: int, text: str) -> _FakeRow:
    return _FakeRow(
        filing_id,
        f"0000123456-{year}-000001",
        year,
        0,
        len(text),
        text,
    )


@pytest.mark.asyncio
class TestDiffSections:
    async def test_both_present_with_changes(self):
        session = _FakeSession(
            [
                _section_row("filing-2023", 2023, _SAMPLE_PARAS_2023),
                _section_row("filing-2024", 2024, _SAMPLE_PARAS_2024),
            ]
        )
        result = await diff_sections(
            session, ticker="NVDA", section="Item 1A", year_a=2023, year_b=2024
        )
        assert isinstance(result, DiffResult)
        assert result.section_a is not None
        assert result.section_b is not None
        assert any("AI" in a for a in result.additions)
        assert any("supply chain" in r.lower() for r in result.removals)
        assert result.common_count >= 1

    async def test_year_a_missing(self):
        session = _FakeSession(
            [
                None,
                _section_row("filing-2024", 2024, _SAMPLE_PARAS_2024),
            ]
        )
        result = await diff_sections(
            session, ticker="NVDA", section="Item 1A", year_a=2023, year_b=2024
        )
        assert result.section_a is None
        assert result.section_b is not None
        assert len(result.additions) > 0  # year_b paragraphs all count as additions
        assert "appears only in 2024" in result.summary

    async def test_year_b_missing(self):
        session = _FakeSession(
            [
                _section_row("filing-2023", 2023, _SAMPLE_PARAS_2023),
                None,
            ]
        )
        result = await diff_sections(
            session, ticker="NVDA", section="Item 1A", year_a=2023, year_b=2024
        )
        assert result.section_a is not None
        assert result.section_b is None
        assert len(result.removals) > 0
        assert "removed in 2024" in result.summary

    async def test_both_missing(self):
        session = _FakeSession([None, None])
        result = await diff_sections(
            session, ticker="NVDA", section="Item 1A", year_a=2023, year_b=2024
        )
        assert result.section_a is None
        assert result.section_b is None
        assert "No filings found" in result.summary

    async def test_summary_format(self):
        session = _FakeSession(
            [
                _section_row("a", 2023, _SAMPLE_PARAS_2023),
                _section_row("b", 2024, _SAMPLE_PARAS_2024),
            ]
        )
        result = await diff_sections(
            session, ticker="NVDA", section="Item 1A", year_a=2023, year_b=2024
        )
        assert "NVDA Item 1A" in result.summary
        assert "2023 -> 2024" in result.summary

    async def test_fetch_section_version_no_row(self):
        """Direct call to _fetch_section_version with empty queue -> None."""
        session = _FakeSession([None])
        result = await _fetch_section_version(
            session, ticker="NVDA", section="Item 1A", fiscal_year=2023, form="10-K"
        )
        assert result is None

    async def test_fetch_section_version_returns_section(self):
        session = _FakeSession([_section_row("f1", 2023, "x" * 100)])
        result = await _fetch_section_version(
            session, ticker="NVDA", section="Item 1A", fiscal_year=2023, form="10-K"
        )
        assert isinstance(result, SectionVersion)
        assert result.fiscal_year == 2023
