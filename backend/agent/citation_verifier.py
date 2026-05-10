"""CitationVerifier, programmatic verification of every cited claim.

Given a Claim and a Citation, confirm:
  1. The citation's offsets resolve to actual text in the filing.
  2. The text supports the claim:
     - Numeric claims     -> exact / tolerance match against numbers in cited text
     - Narrative claims   -> cosine similarity ≥ threshold via BGE embeddings

This is the FINAL verification step before any answer reaches the user. Any
hallucinated citation, fabricated quote, or off-by-one offset must be caught
here. Required: 100% test coverage on this module.

Resolution strategy
-------------------
1. Try `chunks` table first, if a chunk's offsets match exactly, use its
   pre-stored `text` field (cheapest path).
2. If no match, fall back to re-parsing the filing's raw HTML via
   `narrative_parser._html_to_text` and slicing by offsets.

Numeric matching
----------------
Cited text often contains the number in mixed forms: "$96,995", "$96.995
billion", "96,995 million", "(96,995)". We extract numeric tokens, normalise
to a base-USD value, and compare against the claim's numeric_value within a
tolerance percentage (default 0.5%).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.schemas import Citation, Claim
from backend.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class VerifyMethod(str, Enum):
    NUMERIC_EXACT = "numeric_exact"
    NUMERIC_TOLERANCE = "numeric_tolerance"
    SEMANTIC_SIMILARITY = "semantic_similarity"
    OFFSET_INVALID = "offset_invalid"
    FILING_MISSING = "filing_missing"
    NUMERIC_MISMATCH = "numeric_mismatch"
    SEMANTIC_BELOW_THRESHOLD = "semantic_below_threshold"
    NO_NUMERIC_VALUE = "no_numeric_value"


class CitationVerifyResult(BaseModel):
    verified: bool
    method: VerifyMethod
    confidence: float                 # 0.0 - 1.0
    cited_text: str | None = None     # what was actually at the cited offsets
    matched_value: float | None = None
    similarity_score: float | None = None
    issues: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Numeric extraction & matching
# ---------------------------------------------------------------------------


# Captures numbers like:
#   1234           ->  1234
#   1,234,567      ->  1234567
#   1,234.56       ->  1234.56
#   12.5           ->  12.5
#   .25            ->  0.25
#   $(96.9) million ->  -96,900,000   (parens = negative; suffix can come AFTER close-paren)
#
# Strategy: capture the digits + optional inline close-paren + optional suffix.
# Detect accounting-negative by checking surrounding chars for an opening paren
# in the 5 chars before the number.
_NUMBER_RE = re.compile(
    r"(?P<num>\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
    r"(?P<paren_close>\))?"
    r"\s*"
    r"(?P<suffix>billion|million|thousand|trillion|bn|mn|tn|B|M|K|T|%)?",
    re.IGNORECASE,
)

_SUFFIX_MULTIPLIERS: dict[str, float] = {
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
    "%": 1.0,    # percent values not multiplied
    "": 1.0,
}


@dataclass
class ExtractedNumber:
    raw: str
    value: float        # post-multiplier numeric value
    is_percent: bool
    is_negative: bool


def extract_numbers(text: str) -> list[ExtractedNumber]:
    """Pull all numeric tokens from a chunk of text, applying suffix multipliers.

    The regex `\\d+(?:,\\d{3})*(?:\\.\\d+)?|\\.\\d+` guarantees a non-empty
    digits-only string in `match.group("num")`, so float() conversion cannot fail
   . no defensive try/except is necessary.
    """
    out: list[ExtractedNumber] = []
    for match in _NUMBER_RE.finditer(text):
        raw_num = match.group("num")
        base = float(raw_num.replace(",", ""))

        suffix = (match.group("suffix") or "").strip().lower()
        multiplier = _SUFFIX_MULTIPLIERS.get(suffix, 1.0)
        is_percent = suffix == "%"

        # Accounting-negative detection: opening paren in the 5 chars before
        # the number AND a close-paren captured immediately after the digits.
        num_start = match.start("num")
        before = text[max(0, num_start - 5):num_start]
        has_close_paren = match.group("paren_close") is not None
        is_negative = "(" in before and has_close_paren

        value = base * multiplier
        if is_negative:
            value = -value

        out.append(
            ExtractedNumber(
                raw=match.group(0).strip(),
                value=value,
                is_percent=is_percent,
                is_negative=is_negative,
            )
        )
    return out


def find_numeric_match(
    target: float,
    cited_text: str,
    tolerance_pct: float = 0.5,
) -> tuple[bool, float | None, float | None]:
    """Search cited_text for a number that matches `target` within tolerance.

    Returns: (matched, matched_value, best_relative_error_pct)
    """
    if target == 0:
        # Special-case: tolerance % is undefined for zero. Use absolute small epsilon.
        for n in extract_numbers(cited_text):
            if abs(n.value) < 1e-6:
                return True, n.value, 0.0
        return False, None, None

    target_abs = abs(target)
    best_value: float | None = None
    best_rel_err: float | None = None

    for n in extract_numbers(cited_text):
        rel_err = abs(n.value - target) / target_abs * 100.0
        if best_rel_err is None or rel_err < best_rel_err:
            best_rel_err = rel_err
            best_value = n.value
        if rel_err <= tolerance_pct:
            return True, n.value, rel_err

    return False, best_value, best_rel_err


# ---------------------------------------------------------------------------
# Cited-text resolution
# ---------------------------------------------------------------------------


async def resolve_cited_text(
    citation: Citation,
    session: AsyncSession,
) -> tuple[str | None, str | None]:
    """Fetch the actual text at (filing_id, char_offset_start, char_offset_end).

    Returns: (text, error_reason). If text is None, error_reason explains why.
    """
    # If filing_id isn't a valid UUID (e.g. LLM put accession_number there),
    # try resolving by accession_number instead.
    import uuid as _uuid
    is_uuid = False
    try:
        _uuid.UUID(citation.filing_id)
        is_uuid = True
    except (ValueError, AttributeError):
        pass

    if is_uuid:
        # Try the chunks table first, exact offset match.
        row = (
            await session.execute(
                text(
                    """
                    SELECT text
                    FROM chunks
                    WHERE filing_id = :fid::uuid
                      AND char_offset_start = :start
                      AND char_offset_end = :end
                    LIMIT 1
                    """
                ),
                {
                    "fid": citation.filing_id,
                    "start": citation.char_offset_start,
                    "end": citation.char_offset_end,
                },
            )
        ).fetchone()
        if row is not None:
            return row[0], None

    # No exact chunk (or non-UUID filing_id), fall back to looking up by
    # accession_number first, then by filing UUID.
    if not is_uuid:
        # Treat citation.filing_id as accession_number
        filing_row = (
            await session.execute(
                text("SELECT raw_path FROM filings WHERE accession_number = :acc LIMIT 1"),
                {"acc": citation.filing_id},
            )
        ).fetchone()
    else:
        filing_row = (
            await session.execute(
                text("SELECT raw_path FROM filings WHERE id = :id::uuid LIMIT 1"),
                {"id": citation.filing_id},
            )
        ).fetchone()
    if filing_row is None or not filing_row[0]:
        return None, "filing_missing"

    raw_path = Path(filing_row[0])
    if not raw_path.exists():
        return None, "filing_missing"

    try:
        from backend.ingestion.narrative_parser import _html_to_text
    except ImportError:
        return None, "narrative_parser_unavailable"

    full_text = _html_to_text(raw_path.read_bytes())
    if (
        citation.char_offset_start < 0
        or citation.char_offset_end > len(full_text)
        or citation.char_offset_start >= citation.char_offset_end
    ):
        return None, "offset_invalid"

    return full_text[citation.char_offset_start:citation.char_offset_end], None


# ---------------------------------------------------------------------------
# Semantic similarity
# ---------------------------------------------------------------------------


def semantic_similarity(
    claim_text: str,
    cited_text: str,
    embedder: Any | None = None,
) -> float:
    """Cosine similarity between claim and cited text using BGE embeddings.

    `embedder` is optional and exists for unit tests, pass a stub model.
    Production path lazily loads `embed_corpus.get_model()`.
    """
    if embedder is None:
        from backend.indexing.embed_corpus import get_model
        embedder = get_model()

    vectors = embedder.encode(
        [claim_text, cited_text],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    # Normalised vectors -> cosine = dot product
    return float(_dot(vectors[0], vectors[1]))


def _dot(a: Any, b: Any) -> float:
    """Dot product that accepts numpy arrays, lists, or any iterable of floats."""
    return float(sum(x * y for x, y in zip(list(a), list(b))))


# ---------------------------------------------------------------------------
# Main verifier
# ---------------------------------------------------------------------------


async def verify_citation(
    claim: Claim,
    citation: Citation,
    session: AsyncSession,
    tolerance_pct: float = 0.5,
    semantic_threshold: float = 0.55,
    embedder: Any | None = None,
) -> CitationVerifyResult:
    """Verify that `claim` is supported by `citation`.

    Args:
        claim:               The factual statement to verify.
        citation:            The cited filing offsets.
        session:             Async DB session for chunk / filing lookup.
        tolerance_pct:       % tolerance for numeric matching (default 0.5%).
        semantic_threshold:  Cosine sim threshold for narrative claims (default 0.55).
        embedder:            Optional embedder injection point for tests.
    """
    cited_text, error = await resolve_cited_text(citation, session)
    if cited_text is None:
        method = (
            VerifyMethod.OFFSET_INVALID
            if error == "offset_invalid"
            else VerifyMethod.FILING_MISSING
        )
        return CitationVerifyResult(
            verified=False,
            method=method,
            confidence=0.0,
            issues=[error or "unknown_resolution_failure"],
        )

    # Numeric path: claim has is_numeric=True and a numeric_value.
    if claim.is_numeric:
        if claim.numeric_value is None:
            return CitationVerifyResult(
                verified=False,
                method=VerifyMethod.NO_NUMERIC_VALUE,
                confidence=0.0,
                cited_text=cited_text,
                issues=["claim marked numeric but numeric_value is None"],
            )

        matched, matched_value, rel_err = find_numeric_match(
            target=claim.numeric_value,
            cited_text=cited_text,
            tolerance_pct=tolerance_pct,
        )

        if matched and rel_err is not None and rel_err <= 1e-6:
            return CitationVerifyResult(
                verified=True,
                method=VerifyMethod.NUMERIC_EXACT,
                confidence=1.0,
                cited_text=cited_text,
                matched_value=matched_value,
            )

        if matched:
            return CitationVerifyResult(
                verified=True,
                method=VerifyMethod.NUMERIC_TOLERANCE,
                confidence=1.0 - (rel_err or 0.0) / max(tolerance_pct, 1e-9),
                cited_text=cited_text,
                matched_value=matched_value,
                issues=[f"matched within {rel_err:.4f}% tolerance"] if rel_err else [],
            )

        return CitationVerifyResult(
            verified=False,
            method=VerifyMethod.NUMERIC_MISMATCH,
            confidence=0.0,
            cited_text=cited_text,
            matched_value=matched_value,
            issues=[
                f"target {claim.numeric_value} not found in cited text "
                f"(closest: {matched_value}, rel_err: {rel_err})"
            ],
        )

    # Narrative path: cosine similarity.
    sim = semantic_similarity(claim.text, cited_text, embedder=embedder)

    if sim >= semantic_threshold:
        return CitationVerifyResult(
            verified=True,
            method=VerifyMethod.SEMANTIC_SIMILARITY,
            confidence=sim,
            cited_text=cited_text,
            similarity_score=sim,
        )

    return CitationVerifyResult(
        verified=False,
        method=VerifyMethod.SEMANTIC_BELOW_THRESHOLD,
        confidence=sim,
        cited_text=cited_text,
        similarity_score=sim,
        issues=[
            f"similarity {sim:.3f} below threshold {semantic_threshold}"
        ],
    )
