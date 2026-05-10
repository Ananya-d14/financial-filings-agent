"""Unit tests for narrative_parser, section detection and chunking.

No DB, no network. Feeds synthetic HTML straight to the parser functions.
"""

from __future__ import annotations

import textwrap

import pytest

from backend.ingestion.narrative_parser import (
    Chunk,
    DetectedSection,
    _chunk_section,
    _detect_sections,
    _html_to_text,
)


# ---------------------------------------------------------------------------
# HTML -> text
# ---------------------------------------------------------------------------


def test_html_to_text_strips_tags() -> None:
    html = b"<html><body><p>Hello <b>World</b></p></body></html>"
    text = _html_to_text(html)
    assert "Hello" in text
    assert "World" in text
    assert "<" not in text
    assert ">" not in text


def test_html_to_text_removes_scripts() -> None:
    html = b"""
    <html><body>
    <script>alert('bad')</script>
    <p>Good text</p>
    </body></html>
    """
    text = _html_to_text(html)
    assert "Good text" in text
    assert "alert" not in text


def test_html_to_text_collapses_blank_lines() -> None:
    html = b"<html><body><p>A</p>\n\n\n\n\n<p>B</p></body></html>"
    text = _html_to_text(html)
    # Max two consecutive newlines
    assert "\n\n\n" not in text


# ---------------------------------------------------------------------------
# Section detection. 10-K
# ---------------------------------------------------------------------------


def _make_10k_text() -> str:
    return textwrap.dedent("""
        ANNUAL REPORT ON FORM 10-K

        Item 1. Business

        We are a technology company founded in 1993.

        Item 1A. Risk Factors

        There are many risks. First risk is competition.

        Item 7. Management's Discussion and Analysis

        Revenue increased 30% year over year.

        Item 9A. Controls and Procedures

        Our controls are effective.
    """)


def test_detect_sections_10k() -> None:
    text = _make_10k_text()
    sections = _detect_sections(text, "10-K")
    labels = {s.item_num for s in sections}
    assert "1" in labels
    assert "1A" in labels
    assert "7" in labels
    assert "9A" in labels


def test_section_offsets_are_consistent() -> None:
    text = _make_10k_text()
    sections = _detect_sections(text, "10-K")
    for sec in sections:
        assert 0 <= sec.char_start < sec.char_end <= len(text)
        # The section text must match what the offsets point at in the source.
        assert text[sec.char_start:sec.char_end] == sec.text


def test_section_text_contains_expected_content() -> None:
    text = _make_10k_text()
    sections = _detect_sections(text, "10-K")
    sec_1a = next((s for s in sections if s.item_num == "1A"), None)
    assert sec_1a is not None
    assert "risk" in sec_1a.text.lower()


# ---------------------------------------------------------------------------
# Section detection. 8-K
# ---------------------------------------------------------------------------


def _make_8k_text() -> str:
    return textwrap.dedent("""
        FORM 8-K CURRENT REPORT

        Item 2.02. Results of Operations and Financial Condition

        Revenue for Q4 was $18.5 billion.

        Item 8.01. Other Events

        The company announced a new product line.
    """)


def test_detect_sections_8k() -> None:
    text = _make_8k_text()
    sections = _detect_sections(text, "8-K")
    labels = {s.item_num for s in sections}
    assert "2.02" in labels
    assert "8.01" in labels


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _make_section(text: str, item_num: str = "7") -> DetectedSection:
    return DetectedSection(
        section=f"Item {item_num}",
        item_num=item_num,
        item_label="MD&A",
        char_start=0,
        char_end=len(text),
        text=text,
    )


def test_short_section_produces_single_chunk() -> None:
    text = "Revenue increased. Costs decreased. Profit margin improved."
    sec = _make_section(text)
    chunks = _chunk_section(sec, filing_id="test-id", target=4096, max_chars=8192)
    assert len(chunks) == 1
    assert chunks[0].char_offset_start == 0
    assert chunks[0].char_offset_end == len(text)


def test_long_section_produces_multiple_chunks() -> None:
    # Create a section longer than max_chars
    paragraph = "Revenue increased significantly due to strong demand. " * 20
    # Build enough paragraphs to exceed MAX_CHUNK_CHARS
    text = "\n".join([paragraph] * 10)
    sec = _make_section(text)
    chunks = _chunk_section(sec, filing_id="test-id", target=512, max_chars=1024)
    assert len(chunks) > 1


def test_chunk_offsets_cover_full_section() -> None:
    paragraph = "This is a test paragraph with reasonable length. " * 10
    text = "\n".join([paragraph] * 8)
    sec = _make_section(text)
    chunks = _chunk_section(sec, filing_id="test-id", target=512, max_chars=1024)

    # All offsets must be within the section's range
    for chunk in chunks:
        assert sec.char_start <= chunk.char_offset_start
        assert chunk.char_offset_end <= sec.char_end


def test_chunk_text_not_empty() -> None:
    text = "Hello world.\n\nSecond paragraph.\n\nThird paragraph."
    sec = _make_section(text)
    chunks = _chunk_section(sec, filing_id="test-id")
    for chunk in chunks:
        assert chunk.text.strip()


def test_empty_section_returns_no_chunks() -> None:
    sec = _make_section("   ")
    chunks = _chunk_section(sec, filing_id="test-id")
    assert chunks == []


def test_chunk_token_count_is_positive() -> None:
    text = "Revenue was strong. Costs were controlled." * 50
    sec = _make_section(text)
    chunks = _chunk_section(sec, filing_id="test-id")
    for chunk in chunks:
        assert chunk.token_count > 0


# ---------------------------------------------------------------------------
# Rate-limiter unit test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limiter_respects_interval() -> None:
    import time

    from backend.ingestion.edgar_downloader import RateLimiter

    limiter = RateLimiter(max_rps=5.0)  # min 200ms between calls
    start = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    # Two acquisitions should take at least ~200ms
    assert elapsed >= 0.15, f"Rate limiter too fast: {elapsed:.3f}s"
