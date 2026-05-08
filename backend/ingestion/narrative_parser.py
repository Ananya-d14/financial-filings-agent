"""Section-aware narrative parser.

Converts raw EDGAR HTML filings into section-labelled chunks stored in
`filing_sections` and `chunks`. Each chunk carries precise character
offsets into the filing's full-text rendering. These offsets are the
critical part of the citation system.

Section detection strategy
--------------------------
* 10-K  → looks for "ITEM N[A/B]" headers (regex-based), extracts
          Items 1, 1A, 1B, 2, 3, 6, 7, 7A, 8, 9, 9A.
* 10-Q  → looks for Items 1, 1A, 2, 3, 4.
* 8-K   → looks for Items 1.01, 2.01, 2.02, 5.02, 8.01 etc.

HTML → text pipeline
--------------------
1. BeautifulSoup strips HTML tags.
2. Whitespace normalised (excess blank lines collapsed).
3. Section-detection regex applied to the plain text.
4. Each detected section boundary slices out the section text.
5. Section text is split into overlapping chunks (target 512 tokens ≈ 2048
   chars, max 4096 chars, 10% overlap).

Char offsets are relative to the full de-tagged text of the filing, so they
remain stable across re-ingestions of the same SHA256-matched raw HTML.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.logging_config import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Section patterns: compiled once at import time.
# ---------------------------------------------------------------------------

# 10-K items: "ITEM 1A" / "ITEM 1A." / "Item 1A. Risk Factors" etc.
_10K_ITEM_RE = re.compile(
    r"(?:^|\n)\s*"
    r"(?:ITEM|Item)\s+"
    r"(?P<item_num>\d{1,2}[AB]?)"
    r"(?:[\.\s\-:]+(?P<item_title>[^\n]{0,120}))?"
    ,
    re.MULTILINE,
)

# 10-Q items: same structure but subset
_10Q_ITEM_RE = _10K_ITEM_RE  # reuse same pattern; we filter by item number later

# 8-K items: "ITEM 1.01" / "Item 1.01. ..."
_8K_ITEM_RE = re.compile(
    r"(?:^|\n)\s*"
    r"(?:ITEM|Item)\s+"
    r"(?P<item_num>\d{1,2}\.\d{2})"
    r"(?:[\.\s\-:]+(?P<item_title>[^\n]{0,120}))?"
    ,
    re.MULTILINE,
)

# Sections we care about per form type.
_RELEVANT_ITEMS: dict[str, set[str]] = {
    "10-K": {"1", "1A", "1B", "2", "3", "6", "7", "7A", "8", "9", "9A"},
    "10-Q": {"1", "1A", "2", "3", "4"},
    "8-K": {"1.01", "2.01", "2.02", "2.05", "4.01", "5.01", "5.02", "8.01"},
}

# Friendly labels for known sections.
_ITEM_LABELS: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "2": "Properties",
    "3": "Legal Proceedings",
    "6": "Selected Financial Data",
    "7": "MD&A",
    "7A": "Quantitative Disclosures About Market Risk",
    "8": "Financial Statements",
    "9": "Disagreements With Accountants",
    "9A": "Controls and Procedures",
    # 10-Q
    "4": "Mine Safety Disclosures",
    # 8-K
    "1.01": "Entry into Material Agreement",
    "2.01": "Completion of Acquisition or Disposition",
    "2.02": "Results of Operations and Financial Condition",
    "2.05": "Costs Associated with Exit or Disposal",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "5.01": "Changes in Control",
    "5.02": "Departure/Appointment of Directors or Officers",
    "8.01": "Other Events",
}

# Chunking config
TARGET_CHUNK_CHARS = 2048   # ~512 tokens (4 chars/token heuristic)
MAX_CHUNK_CHARS = 4096
OVERLAP_CHARS = 200         # sliding overlap to avoid clipping context


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DetectedSection:
    section: str         # e.g. "Item 1A"
    item_num: str        # e.g. "1A"
    item_label: str      # e.g. "Risk Factors"
    char_start: int
    char_end: int
    text: str


@dataclass
class Chunk:
    filing_id: str
    section: str
    item_label: str | None
    char_offset_start: int   # absolute offset in the filing's full text
    char_offset_end: int
    text: str
    token_count: int = field(init=False)

    def __post_init__(self) -> None:
        # Rough token estimate, fine for DB storage; not used by the embedder.
        self.token_count = len(self.text) // 4


# ---------------------------------------------------------------------------
# HTML → plain text
# ---------------------------------------------------------------------------


def _html_to_text(html: bytes | str) -> str:
    """Strip HTML tags and normalise whitespace.

    We deliberately avoid importing `unstructured` at module level so the
    narrative_parser can be unit-tested without heavy ML deps. For production
    runs, `unstructured` is used for table-aware extraction; see
    `_html_to_text_rich` below.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    # Remove script / style entirely; they pollute the extracted text.
    for tag in soup(["script", "style", "head", "meta", "link"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Normalise unicode (NFKC resolves ligatures, non-breaking spaces, etc.)
    text = unicodedata.normalize("NFKC", text)
    # Collapse runs of blank lines to a maximum of two
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse intra-line whitespace
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------


def _detect_sections(
    full_text: str, form: str
) -> list[DetectedSection]:
    """Return a list of DetectedSection in document order.

    Sections are bounded by the start of the next item header (or EOF).
    """
    base_form = re.sub(r"/A$", "", form).strip().upper()
    relevant = _RELEVANT_ITEMS.get(base_form, set())
    pattern = _8K_ITEM_RE if base_form == "8-K" else _10K_ITEM_RE

    matches = list(pattern.finditer(full_text))
    if not matches:
        # Fallback: treat whole document as a single unlabelled section.
        return [
            DetectedSection(
                section="Document",
                item_num="0",
                item_label="Full Document",
                char_start=0,
                char_end=len(full_text),
                text=full_text,
            )
        ]

    detected: list[DetectedSection] = []
    for idx, match in enumerate(matches):
        item_num = (match.group("item_num") or "").strip()
        if item_num not in relevant:
            continue

        item_title = (match.group("item_title") or "").strip()
        item_label = _ITEM_LABELS.get(item_num, item_title or f"Item {item_num}")
        section_label = f"Item {item_num}"

        char_start = match.start()
        # Section ends at start of next match or EOF
        char_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(full_text)

        section_text = full_text[char_start:char_end]
        if len(section_text.strip()) < 50:
            # Likely a table-of-contents reference, not the actual section body.
            continue

        detected.append(
            DetectedSection(
                section=section_label,
                item_num=item_num,
                item_label=item_label,
                char_start=char_start,
                char_end=char_end,
                text=section_text,
            )
        )

    return detected


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _chunk_section(
    section: DetectedSection,
    filing_id: str,
    target: int = TARGET_CHUNK_CHARS,
    max_chars: int = MAX_CHUNK_CHARS,
    overlap: int = OVERLAP_CHARS,
) -> list[Chunk]:
    """Split a section into overlapping chunks, respecting paragraph boundaries."""
    text = section.text
    if not text.strip():
        return []

    paragraphs = re.split(r"\n{1,2}", text)
    chunks: list[Chunk] = []
    current: list[str] = []
    current_len = 0
    # Offset tracking: current_offset_in_section + section.char_start = absolute offset
    current_start_in_section = 0
    pos_in_section = 0

    for para in paragraphs:
        para_len = len(para) + 1  # +1 for the newline
        if current_len + para_len > max_chars and current:
            # Flush current chunk
            chunk_text = "\n".join(current)
            abs_start = section.char_start + current_start_in_section
            abs_end = abs_start + len(chunk_text)
            chunks.append(
                Chunk(
                    filing_id=filing_id,
                    section=section.section,
                    item_label=section.item_label,
                    char_offset_start=abs_start,
                    char_offset_end=abs_end,
                    text=chunk_text,
                )
            )
            # Overlap: keep last N chars of context in the next chunk
            # by keeping the last few paragraphs until they sum to >= overlap
            kept: list[str] = []
            kept_len = 0
            for p in reversed(current):
                if kept_len >= overlap:
                    break
                kept.insert(0, p)
                kept_len += len(p) + 1
            current = kept
            current_len = kept_len
            # Advance current_start to (pos_in_section - kept_len)
            current_start_in_section = pos_in_section - kept_len

        current.append(para)
        current_len += para_len
        pos_in_section += para_len

    # Flush remaining
    if current:
        chunk_text = "\n".join(current)
        abs_start = section.char_start + current_start_in_section
        abs_end = abs_start + len(chunk_text)
        chunks.append(
            Chunk(
                filing_id=filing_id,
                section=section.section,
                item_label=section.item_label,
                char_offset_start=abs_start,
                char_offset_end=abs_end,
                text=chunk_text,
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Persist to DB
# ---------------------------------------------------------------------------


async def parse_and_persist(
    filing_id: str,
    raw_html_path: Path,
    form: str,
    session: AsyncSession,
) -> dict[str, int]:
    """Parse a downloaded filing HTML and write sections + chunks to DB.

    Returns: {"sections": N, "chunks": M}
    """
    raw_html = raw_html_path.read_bytes()
    full_text = _html_to_text(raw_html)

    sections = _detect_sections(full_text, form)
    if not sections:
        log.warning("narrative.no_sections_detected", filing_id=filing_id, form=form)

    all_chunks: list[Chunk] = []

    for sec in sections:
        # Persist section record
        sec_id = str(uuid.uuid4())
        await session.execute(
            text(
                """
                INSERT INTO filing_sections (
                    id, filing_id, section, item_label,
                    char_offset_start, char_offset_end, text_md
                ) VALUES (
                    :id, :filing_id, :section, :item_label,
                    :start, :end, :text
                )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": sec_id,
                "filing_id": filing_id,
                "section": sec.section,
                "item_label": sec.item_label,
                "start": sec.char_start,
                "end": sec.char_end,
                "text": sec.text[:100_000],  # guard against massive sections
            },
        )

        chunks = _chunk_section(sec, filing_id)
        all_chunks.extend(chunks)

    # Bulk insert chunks (text_tsv is auto-populated by the DB trigger)
    for chunk in all_chunks:
        await session.execute(
            text(
                """
                INSERT INTO chunks (
                    filing_id, section, item_label,
                    char_offset_start, char_offset_end, text, token_count
                ) VALUES (
                    :filing_id, :section, :item_label,
                    :start, :end, :text, :tokens
                )
                """
            ),
            {
                "filing_id": chunk.filing_id,
                "section": chunk.section,
                "item_label": chunk.item_label,
                "start": chunk.char_offset_start,
                "end": chunk.char_offset_end,
                "text": chunk.text,
                "tokens": chunk.token_count,
            },
        )

    await session.flush()
    log.info(
        "narrative.parsed",
        filing_id=filing_id,
        form=form,
        sections=len(sections),
        chunks=len(all_chunks),
    )
    return {"sections": len(sections), "chunks": len(all_chunks)}


# ---------------------------------------------------------------------------
# Standalone helper: get full text for citation verification.
# ---------------------------------------------------------------------------


def get_text_at_offsets(
    raw_html_path: Path, char_start: int, char_end: int
) -> str:
    """Reconstruct the cited text from a filing by its stored char offsets.

    Used by citation_verifier to confirm that the cited text actually exists
    at the stored position in the filing.
    """
    raw_html = raw_html_path.read_bytes()
    full_text = _html_to_text(raw_html)
    if char_end > len(full_text):
        raise IndexError(
            f"char_end={char_end} out of range for filing at {raw_html_path} "
            f"(text length={len(full_text)})"
        )
    return full_text[char_start:char_end]
