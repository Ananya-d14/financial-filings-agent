"""FilingDiffTool, year-over-year section diff.

Given a ticker, section label, and two fiscal years, fetches the matching
sections from `filing_sections` and returns a structured diff:
  - additions: paragraphs new in year_b that weren't in year_a
  - removals:  paragraphs in year_a that disappeared in year_b
  - common:    paragraphs that appear in both (truncated for size)

Useful for Tier-2 questions like "what changed in Tesla's risk factors
between 2023 and 2024?".

Implementation
--------------
1. Pull both sections via SQL.
2. Split each into paragraphs (\\n\\n boundaries).
3. Use difflib.SequenceMatcher to find common subsequences.
4. Anything in year_b but not year_a → addition; vice versa → removal.

A simple paragraph-level diff is intentional, sentence-level diffing is
noisy on legal boilerplate, and chunk-level diffing misses context. The
agent receives lists of full paragraphs it can quote with citations.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.logging_config import get_logger

log = get_logger(__name__)


@dataclass
class SectionVersion:
    filing_id: str
    accession_number: str
    fiscal_year: int
    char_offset_start: int
    char_offset_end: int
    text: str


@dataclass
class DiffResult:
    ticker: str
    section: str
    year_a: int
    year_b: int
    section_a: SectionVersion | None
    section_b: SectionVersion | None
    additions: list[str]
    removals: list[str]
    common_count: int
    summary: str


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines, strip each paragraph, drop empties and dupes-near-empty."""
    parts = [p.strip() for p in text.split("\n\n")]
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if len(p) < 20:  # drop noise like single-word headings
            continue
        # Deduplicate within a single section (TOC-style repeats)
        key = p[:200]
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _diff_paragraphs(a: list[str], b: list[str]) -> tuple[list[str], list[str], int]:
    """Compute additions/removals using SequenceMatcher.

    Returns: (additions, removals, common_count)
    """
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    additions: list[str] = []
    removals: list[str] = []
    common = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            common += i2 - i1
        elif tag == "delete":
            removals.extend(a[i1:i2])
        elif tag == "insert":
            additions.extend(b[j1:j2])
        elif tag == "replace":
            removals.extend(a[i1:i2])
            additions.extend(b[j1:j2])

    return additions, removals, common


async def _fetch_section_version(
    session: AsyncSession,
    ticker: str,
    section: str,
    fiscal_year: int,
    form: str,
) -> SectionVersion | None:
    row = (
        await session.execute(
            text(
                """
                SELECT
                    fs.filing_id::text,
                    f.accession_number,
                    f.fiscal_year,
                    fs.char_offset_start,
                    fs.char_offset_end,
                    fs.text_md
                FROM filing_sections fs
                JOIN filings f    ON f.id  = fs.filing_id
                JOIN companies co ON co.cik = f.cik
                WHERE co.ticker = :ticker
                  AND fs.section = :section
                  AND f.fiscal_year = :year
                  AND f.form = :form
                ORDER BY f.filed_date DESC
                LIMIT 1
                """
            ),
            {
                "ticker": ticker.upper(),
                "section": section,
                "year": fiscal_year,
                "form": form,
            },
        )
    ).fetchone()

    if row is None:
        return None
    return SectionVersion(
        filing_id=row[0],
        accession_number=row[1],
        fiscal_year=row[2],
        char_offset_start=row[3],
        char_offset_end=row[4],
        text=row[5] or "",
    )


async def diff_sections(
    session: AsyncSession,
    ticker: str,
    section: str,
    year_a: int,
    year_b: int,
    form: str = "10-K",
) -> DiffResult:
    """Compute a paragraph-level YoY diff for a named section."""
    sec_a = await _fetch_section_version(session, ticker, section, year_a, form)
    sec_b = await _fetch_section_version(session, ticker, section, year_b, form)

    if sec_a is None and sec_b is None:
        return DiffResult(
            ticker=ticker, section=section, year_a=year_a, year_b=year_b,
            section_a=None, section_b=None,
            additions=[], removals=[], common_count=0,
            summary=f"No filings found for {ticker} {section} in {year_a} or {year_b}",
        )

    if sec_a is None:
        paragraphs_b = _split_paragraphs(sec_b.text) if sec_b else []
        return DiffResult(
            ticker=ticker, section=section, year_a=year_a, year_b=year_b,
            section_a=None, section_b=sec_b,
            additions=paragraphs_b, removals=[], common_count=0,
            summary=f"{ticker} {section} appears only in {year_b} (n/a in {year_a})",
        )

    if sec_b is None:
        paragraphs_a = _split_paragraphs(sec_a.text)
        return DiffResult(
            ticker=ticker, section=section, year_a=year_a, year_b=year_b,
            section_a=sec_a, section_b=None,
            additions=[], removals=paragraphs_a, common_count=0,
            summary=f"{ticker} {section} appears only in {year_a} (removed in {year_b})",
        )

    paragraphs_a = _split_paragraphs(sec_a.text)
    paragraphs_b = _split_paragraphs(sec_b.text)
    additions, removals, common = _diff_paragraphs(paragraphs_a, paragraphs_b)

    summary = (
        f"{ticker} {section}: {year_a} → {year_b}. "
        f"{len(additions)} additions, {len(removals)} removals, "
        f"{common} unchanged paragraphs"
    )

    log.debug(
        "filing_diff.done",
        ticker=ticker,
        section=section,
        year_a=year_a,
        year_b=year_b,
        additions=len(additions),
        removals=len(removals),
        common=common,
    )

    return DiffResult(
        ticker=ticker, section=section, year_a=year_a, year_b=year_b,
        section_a=sec_a, section_b=sec_b,
        additions=additions, removals=removals, common_count=common,
        summary=summary,
    )
