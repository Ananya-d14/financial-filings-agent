"""100%-coverage test suite for backend.agent.citation_verifier.

Strategy
--------
* Numeric extraction: feed varied text (commas, suffixes, parens, percent,
  dot-decimals) and assert the parsed values.
* Numeric matching: target ± tolerance, exact-match, mismatch, zero-target.
* Cited-text resolution: mock AsyncSession to return chunk row, filing row,
  or nothing. Use tmp_path for the raw-html file fallback.
* Semantic similarity: inject a stub embedder (no BGE / no GPU).
* Verify: full path coverage of every method enum value.

Run with:
    pytest backend/tests/agent/test_citation_verifier.py \
      --cov=backend.agent.citation_verifier --cov-report=term-missing
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.agent.citation_verifier import (
    CitationVerifyResult,
    ExtractedNumber,
    VerifyMethod,
    _dot,
    extract_numbers,
    find_numeric_match,
    resolve_cited_text,
    semantic_similarity,
    verify_citation,
)
from backend.agent.schemas import Citation, Claim


# ===========================================================================
# Fixtures
# ===========================================================================


def _make_citation(
    filing_id: str = "filing-1",
    char_offset_start: int = 0,
    char_offset_end: int = 100,
) -> Citation:
    return Citation(
        filing_id=filing_id,
        accession_number="0000123456-24-000001",
        ticker="NVDA",
        form="10-K",
        fiscal_year=2024,
        section="Item 7",
        item_label="MD&A",
        char_offset_start=char_offset_start,
        char_offset_end=char_offset_end,
    )


def _make_numeric_claim(value: float, text: str = "Revenue was $96.995 billion.") -> Claim:
    return Claim(
        text=text,
        is_numeric=True,
        numeric_value=value,
        numeric_unit="USD",
    )


def _make_narrative_claim(text: str) -> Claim:
    return Claim(text=text, is_numeric=False)


class _FakeRow:
    """Minimal stand-in for SQLAlchemy Row results."""

    def __init__(self, *values: Any) -> None:
        self._vals = values

    def __getitem__(self, idx: int) -> Any:
        return self._vals[idx]


class _FakeResult:
    def __init__(self, row: _FakeRow | None) -> None:
        self._row = row

    def fetchone(self) -> _FakeRow | None:
        return self._row


class _FakeSession:
    """Captures executed SQL strings, returns scripted results in order."""

    def __init__(self, scripted: list[_FakeRow | None]) -> None:
        self._queue = list(scripted)
        self.calls: list[Any] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _FakeResult:
        self.calls.append((str(statement), params))
        if not self._queue:
            return _FakeResult(None)
        return _FakeResult(self._queue.pop(0))


class _StubEmbedder:
    """Returns deterministic vectors for given inputs.

    Maps input text to a unit-norm 4-d vector via a simple lookup. Two strings
    that match a "similar" pattern produce vectors with high cosine sim.
    """

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def encode(
        self,
        texts: list[str],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
    ) -> list[list[float]]:
        out = []
        for t in texts:
            v = self.vectors.get(t, [1.0, 0.0, 0.0, 0.0])
            if normalize_embeddings:
                norm = sum(x * x for x in v) ** 0.5
                v = [x / norm for x in v] if norm > 0 else v
            out.append(v)
        return out


# ===========================================================================
# Number extraction
# ===========================================================================


class TestExtractNumbers:
    def test_simple_integer(self):
        nums = extract_numbers("Revenue was 1234")
        assert any(n.value == 1234 for n in nums)

    def test_comma_separated(self):
        nums = extract_numbers("Revenue was $1,234,567")
        assert any(n.value == 1234567 for n in nums)

    def test_decimal(self):
        nums = extract_numbers("Margin was 12.5")
        assert any(n.value == 12.5 for n in nums)

    def test_billion_suffix(self):
        nums = extract_numbers("Revenue was $96.995 billion")
        # 96.995 * 1e9
        assert any(abs(n.value - 96_995_000_000) < 1 for n in nums)

    def test_million_suffix(self):
        nums = extract_numbers("Revenue was $250 million")
        assert any(n.value == 250_000_000 for n in nums)

    def test_short_suffix_b(self):
        nums = extract_numbers("Revenue was $5B")
        assert any(n.value == 5e9 for n in nums)

    def test_trillion_suffix(self):
        nums = extract_numbers("Market cap exceeded $3 trillion")
        assert any(n.value == 3e12 for n in nums)

    def test_thousand_suffix(self):
        nums = extract_numbers("Earned $50 thousand")
        assert any(n.value == 50_000 for n in nums)

    def test_paren_negative(self):
        nums = extract_numbers("Net loss was $(96.9) million")
        assert any(n.value == -96_900_000 for n in nums)

    def test_percent_suffix(self):
        nums = extract_numbers("Margin was 25%")
        # Percent values are NOT multiplied
        pct_nums = [n for n in nums if n.is_percent]
        assert any(n.value == 25 for n in pct_nums)

    def test_dot_leading_decimal(self):
        nums = extract_numbers("Margin .25")
        assert any(n.value == 0.25 for n in nums)

    def test_extracted_number_is_dataclass(self):
        nums = extract_numbers("$5.0 billion")
        assert isinstance(nums[0], ExtractedNumber)
        assert nums[0].is_negative is False

    def test_no_numbers(self):
        nums = extract_numbers("This is plain text with no numbers")
        assert nums == []


# ===========================================================================
# Numeric matching
# ===========================================================================


class TestFindNumericMatch:
    def test_exact_match(self):
        matched, value, err = find_numeric_match(
            target=96_995_000_000, cited_text="Revenue was $96.995 billion."
        )
        assert matched is True
        assert err is not None and err < 0.001

    def test_within_tolerance(self):
        matched, value, err = find_numeric_match(
            target=100.0, cited_text="The value was 100.4", tolerance_pct=0.5
        )
        assert matched is True
        assert err is not None and err <= 0.5

    def test_outside_tolerance(self):
        matched, value, err = find_numeric_match(
            target=100.0, cited_text="The value was 105", tolerance_pct=0.5
        )
        assert matched is False
        assert value == 105
        assert err is not None and err > 0.5

    def test_target_zero_with_zero_in_text(self):
        matched, value, err = find_numeric_match(target=0, cited_text="The change was 0")
        assert matched is True
        assert value == 0

    def test_target_zero_no_zero_in_text(self):
        matched, value, err = find_numeric_match(target=0, cited_text="Value was 100")
        assert matched is False

    def test_no_numbers_in_text(self):
        matched, value, err = find_numeric_match(target=100, cited_text="No numbers here")
        assert matched is False
        assert value is None
        assert err is None

    def test_picks_closest(self):
        matched, value, err = find_numeric_match(
            target=99, cited_text="Values were 50, 100, 150", tolerance_pct=2.0
        )
        assert matched is True
        assert value == 100  # closest to 99


# ===========================================================================
# Resolve cited text
# ===========================================================================


@pytest.mark.asyncio
class TestResolveCitedText:
    async def test_chunk_hit(self):
        session = _FakeSession([_FakeRow("This is the chunk text.")])
        text, err = await resolve_cited_text(_make_citation(), session)
        assert text == "This is the chunk text."
        assert err is None

    async def test_no_chunk_no_filing(self):
        session = _FakeSession([None, None])
        text, err = await resolve_cited_text(_make_citation(), session)
        assert text is None
        assert err == "filing_missing"

    async def test_no_chunk_filing_path_missing(self):
        # Chunk lookup empty, filing returned but path doesn't exist
        session = _FakeSession([None, _FakeRow("/no/such/path.html")])
        text, err = await resolve_cited_text(_make_citation(), session)
        assert text is None
        assert err == "filing_missing"

    async def test_no_chunk_null_raw_path(self):
        session = _FakeSession([None, _FakeRow(None)])
        text, err = await resolve_cited_text(_make_citation(), session)
        assert text is None
        assert err == "filing_missing"

    async def test_no_chunk_html_offset_invalid(self, tmp_path: Path):
        # Build a tiny html file, but cite offsets past end of text
        html_file = tmp_path / "filing.html"
        html_file.write_bytes(b"<html><body><p>Hello world.</p></body></html>")

        session = _FakeSession([None, _FakeRow(str(html_file))])
        # The de-tagged text is short; cite an offset way past EOF
        cit = _make_citation(char_offset_start=0, char_offset_end=10_000)
        text, err = await resolve_cited_text(cit, session)
        assert text is None
        assert err == "offset_invalid"

    async def test_no_chunk_html_negative_offset(self, tmp_path: Path):
        html_file = tmp_path / "filing.html"
        html_file.write_bytes(b"<html><body><p>Hello world.</p></body></html>")

        session = _FakeSession([None, _FakeRow(str(html_file))])
        cit = _make_citation(char_offset_start=-1, char_offset_end=5)
        text, err = await resolve_cited_text(cit, session)
        assert text is None
        assert err == "offset_invalid"

    async def test_no_chunk_html_inverted_offsets(self, tmp_path: Path):
        html_file = tmp_path / "filing.html"
        html_file.write_bytes(b"<html><body><p>Hello world.</p></body></html>")

        session = _FakeSession([None, _FakeRow(str(html_file))])
        cit = _make_citation(char_offset_start=5, char_offset_end=2)  # start >= end
        text, err = await resolve_cited_text(cit, session)
        assert text is None
        assert err == "offset_invalid"

    async def test_no_chunk_html_fallback_succeeds(self, tmp_path: Path):
        html_file = tmp_path / "filing.html"
        html_file.write_bytes(b"<html><body><p>Hello world.</p></body></html>")

        session = _FakeSession([None, _FakeRow(str(html_file))])
        cit = _make_citation(char_offset_start=0, char_offset_end=5)
        text, err = await resolve_cited_text(cit, session)
        assert err is None
        assert text == "Hello"

    async def test_narrative_parser_unavailable(self, tmp_path: Path, monkeypatch):
        """Cover the ImportError fallback when narrative_parser fails to import."""
        import sys

        html_file = tmp_path / "filing.html"
        html_file.write_bytes(b"<html><body>x</body></html>")

        # Inject a faux import failure: remove narrative_parser from sys.modules
        # AND make subsequent imports raise.
        monkeypatch.delitem(sys.modules, "backend.ingestion.narrative_parser", raising=False)

        # Replace the real module's place in the import system with a stub that raises
        import importlib.abc
        import importlib.machinery

        class _BlockingFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname == "backend.ingestion.narrative_parser":
                    raise ImportError("simulated import failure")
                return None

        finder = _BlockingFinder()
        monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])

        session = _FakeSession([None, _FakeRow(str(html_file))])
        cit = _make_citation()
        text, err = await resolve_cited_text(cit, session)
        assert text is None
        assert err == "narrative_parser_unavailable"


# ===========================================================================
# Semantic similarity
# ===========================================================================


class TestSemanticSimilarity:
    def test_high_similarity_same_vector(self):
        embedder = _StubEmbedder({"a": [1, 0, 0, 0], "b": [1, 0, 0, 0]})
        sim = semantic_similarity("a", "b", embedder=embedder)
        assert abs(sim - 1.0) < 1e-6

    def test_zero_similarity_orthogonal(self):
        embedder = _StubEmbedder({"a": [1, 0, 0, 0], "b": [0, 1, 0, 0]})
        sim = semantic_similarity("a", "b", embedder=embedder)
        assert abs(sim) < 1e-6

    def test_partial_similarity(self):
        # Two unit vectors at 60° → cos = 0.5
        embedder = _StubEmbedder({"a": [1, 0, 0, 0], "b": [0.5, 0.866, 0, 0]})
        sim = semantic_similarity("a", "b", embedder=embedder)
        assert abs(sim - 0.5) < 0.01

    def test_default_loader_path(self, monkeypatch):
        """Exercise the lazy-load path when embedder is None."""
        # Stub the get_model import to return our deterministic embedder
        stub = _StubEmbedder({"a": [1, 0, 0, 0], "b": [1, 0, 0, 0]})

        from backend.indexing import embed_corpus
        monkeypatch.setattr(embed_corpus, "get_model", lambda: stub)

        sim = semantic_similarity("a", "b")
        assert abs(sim - 1.0) < 1e-6

    def test_dot_helper_with_lists(self):
        assert _dot([1, 2, 3], [4, 5, 6]) == 32


# ===========================================================================
# verify_citation, full integration of the pieces above
# ===========================================================================


@pytest.mark.asyncio
class TestVerifyCitation:
    async def test_numeric_exact_match(self):
        session = _FakeSession([_FakeRow("Revenue was $96.995 billion.")])
        claim = _make_numeric_claim(96_995_000_000)
        result = await verify_citation(claim, _make_citation(), session)
        assert result.verified is True
        assert result.method == VerifyMethod.NUMERIC_EXACT
        assert result.confidence == 1.0

    async def test_numeric_within_tolerance(self):
        session = _FakeSession([_FakeRow("Revenue was 100.3 dollars.")])
        claim = _make_numeric_claim(100.0)
        result = await verify_citation(claim, _make_citation(), session, tolerance_pct=0.5)
        assert result.verified is True
        assert result.method == VerifyMethod.NUMERIC_TOLERANCE

    async def test_numeric_mismatch(self):
        session = _FakeSession([_FakeRow("Revenue was $200 million.")])
        claim = _make_numeric_claim(100.0)
        result = await verify_citation(claim, _make_citation(), session)
        assert result.verified is False
        assert result.method == VerifyMethod.NUMERIC_MISMATCH

    async def test_numeric_no_value_set(self):
        session = _FakeSession([_FakeRow("Some text here")])
        # numeric flag set but value is None
        claim = Claim(text="x", is_numeric=True, numeric_value=None)
        result = await verify_citation(claim, _make_citation(), session)
        assert result.verified is False
        assert result.method == VerifyMethod.NO_NUMERIC_VALUE

    async def test_offset_invalid(self, tmp_path: Path):
        html_file = tmp_path / "filing.html"
        html_file.write_bytes(b"<html><body><p>Short</p></body></html>")

        session = _FakeSession([None, _FakeRow(str(html_file))])
        cit = _make_citation(char_offset_start=0, char_offset_end=99999)
        claim = _make_numeric_claim(100)
        result = await verify_citation(claim, cit, session)
        assert result.verified is False
        assert result.method == VerifyMethod.OFFSET_INVALID

    async def test_filing_missing(self):
        session = _FakeSession([None, None])
        result = await verify_citation(
            _make_numeric_claim(100), _make_citation(), session
        )
        assert result.verified is False
        assert result.method == VerifyMethod.FILING_MISSING

    async def test_semantic_above_threshold(self):
        session = _FakeSession([_FakeRow("Tesla reported strong China demand.")])
        claim = _make_narrative_claim("Tesla had strong demand in China.")
        embedder = _StubEmbedder(
            {
                "Tesla had strong demand in China.": [1, 0, 0, 0],
                "Tesla reported strong China demand.": [0.99, 0.1, 0, 0],
            }
        )
        result = await verify_citation(
            claim, _make_citation(), session, embedder=embedder, semantic_threshold=0.5
        )
        assert result.verified is True
        assert result.method == VerifyMethod.SEMANTIC_SIMILARITY
        assert result.similarity_score is not None and result.similarity_score > 0.5

    async def test_semantic_below_threshold(self):
        session = _FakeSession([_FakeRow("Completely unrelated content here.")])
        claim = _make_narrative_claim("Tesla had strong demand in China.")
        embedder = _StubEmbedder(
            {
                "Tesla had strong demand in China.": [1, 0, 0, 0],
                "Completely unrelated content here.": [0, 1, 0, 0],
            }
        )
        result = await verify_citation(
            claim, _make_citation(), session, embedder=embedder
        )
        assert result.verified is False
        assert result.method == VerifyMethod.SEMANTIC_BELOW_THRESHOLD

    async def test_result_serialises(self):
        session = _FakeSession([_FakeRow("Revenue was $100.")])
        claim = _make_numeric_claim(100)
        result = await verify_citation(claim, _make_citation(), session)
        roundtrip = CitationVerifyResult.model_validate_json(result.model_dump_json())
        assert roundtrip == result
