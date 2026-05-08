"""Unit tests for RRF fusion and retrieval result types.

No DB, no network, no GPU. pure logic tests.
"""

from __future__ import annotations

import pytest

from backend.retrieval.hybrid_retriever import RetrievalResult, _rrf_merge, RRF_K


def _make_bm25_rows(chunk_ids: list[str]) -> list[dict]:
    return [
        {
            "chunk_id": cid,
            "filing_id": "f1",
            "ticker": "NVDA",
            "fiscal_year": 2024,
            "form": "10-K",
            "section": "Item 1A",
            "item_label": "Risk Factors",
            "char_offset_start": i * 100,
            "char_offset_end": i * 100 + 50,
            "text": f"chunk {cid}",
            "score": 1.0 / (i + 1),
        }
        for i, cid in enumerate(chunk_ids)
    ]


def _make_dense_rows(chunk_ids: list[str]) -> list[dict]:
    return [
        {
            "chunk_id": cid,
            "filing_id": "f1",
            "ticker": "NVDA",
            "fiscal_year": 2024,
            "form": "10-K",
            "section": "Item 1A",
            "item_label": "Risk Factors",
            "char_offset_start": i * 100,
            "char_offset_end": i * 100 + 50,
            "text_preview": f"chunk {cid}",
            "dense_score": 1.0 / (i + 1),
        }
        for i, cid in enumerate(chunk_ids)
    ]


class TestRRF:
    def test_empty_lists_return_empty(self):
        assert _rrf_merge([], []) == []

    def test_single_source_preserves_order(self):
        bm25 = _make_bm25_rows(["a", "b", "c"])
        merged = _rrf_merge(bm25, [])
        ids = [m[0] for m in merged]
        # Top BM25 rank → highest RRF score
        assert ids[0] == "a"

    def test_agreement_boosts_score(self):
        """A chunk appearing in BOTH sources should score higher than one in only one."""
        bm25 = _make_bm25_rows(["shared", "bm25_only"])
        dense = _make_dense_rows(["shared", "dense_only"])
        merged = _rrf_merge(bm25, dense)
        scores = {m[0]: m[1] for m in merged}

        assert scores["shared"] > scores["bm25_only"]
        assert scores["shared"] > scores["dense_only"]

    def test_rrf_score_formula(self):
        """Verify the formula: score = 1/(k+rank) per source."""
        bm25 = _make_bm25_rows(["a"])   # rank 1 in BM25
        dense = _make_dense_rows(["a"])  # rank 1 in dense
        merged = _rrf_merge(bm25, dense)
        expected = 2.0 / (RRF_K + 1)
        assert abs(merged[0][1] - expected) < 1e-9

    def test_rank_tracking(self):
        bm25 = _make_bm25_rows(["a", "b"])
        dense = _make_dense_rows(["b", "a"])
        merged = _rrf_merge(bm25, dense)
        merged_dict = {m[0]: (m[2], m[3]) for m in merged}  # {chunk_id: (bm25_rank, dense_rank)}
        assert merged_dict["a"] == (1, 2)
        assert merged_dict["b"] == (2, 1)

    def test_dense_only_chunk_has_no_bm25_rank(self):
        bm25: list = []
        dense = _make_dense_rows(["x"])
        merged = _rrf_merge(bm25, dense)
        assert merged[0][2] is None   # bm25_rank
        assert merged[0][3] == 1      # dense_rank

    def test_output_sorted_descending(self):
        bm25 = _make_bm25_rows(["a", "b", "c", "d"])
        dense = _make_dense_rows(["d", "c", "b", "a"])
        merged = _rrf_merge(bm25, dense)
        scores = [m[1] for m in merged]
        assert scores == sorted(scores, reverse=True)

    def test_no_duplicate_chunk_ids_in_output(self):
        bm25 = _make_bm25_rows(["a", "b", "c"])
        dense = _make_dense_rows(["b", "c", "d"])
        merged = _rrf_merge(bm25, dense)
        ids = [m[0] for m in merged]
        assert len(ids) == len(set(ids))


class TestRetrievalResult:
    def test_construction(self):
        r = RetrievalResult(
            chunk_id="abc",
            filing_id="f1",
            ticker="MSFT",
            fiscal_year=2023,
            form="10-K",
            section="Item 7",
            item_label="MD&A",
            char_offset_start=100,
            char_offset_end=200,
            text="Revenue increased.",
            rrf_score=0.03,
            bm25_rank=1,
            dense_rank=2,
        )
        assert r.ticker == "MSFT"
        assert r.rerank_score is None  # not set yet

    def test_rerank_score_settable(self):
        r = RetrievalResult(
            chunk_id="x",
            filing_id="f",
            ticker="AAPL",
            fiscal_year=2022,
            form="10-K",
            section="Item 1A",
            item_label=None,
            char_offset_start=0,
            char_offset_end=10,
            text="text",
            rrf_score=0.01,
        )
        r.rerank_score = 3.7
        assert r.rerank_score == 3.7
