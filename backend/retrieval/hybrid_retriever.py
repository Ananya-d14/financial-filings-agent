"""Hybrid BM25 + dense retriever with Reciprocal Rank Fusion.

Pipeline
--------
1. BM25 (Postgres FTS) . lexical search via tsvector/tsquery.
2. Dense (Qdrant)      . semantic search via BGE-large-en-v1.5 embeddings.
3. RRF fusion          . combine ranked lists: score = Σ 1/(k + rank).
4. Reranker            . optional BGE-reranker-large cross-encoder pass.

The result is a list of `RetrievalResult` objects, each carrying the chunk
text and its citation fields (filing_id, section, char offsets) so the agent
can pass them directly to the citation verifier.

Filter support
--------------
Any combination of: ticker, fiscal_year, form, section. Filters are pushed
down to BOTH the BM25 and dense queries, no post-filtering after merge.

RRF constant k=60 is the standard value from the original paper (Cormack et
al., 2009). Higher k flattens the ranking; lower k amplifies top positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.config import get_settings
from backend.logging_config import get_logger

log = get_logger(__name__)

RRF_K = 60  # reciprocal rank fusion constant


# ---------------------------------------------------------------------------
# Result type returned by retrieve()
# ---------------------------------------------------------------------------


@dataclass
class RetrievalResult:
    chunk_id: str
    filing_id: str
    ticker: str
    fiscal_year: int
    form: str
    section: str
    item_label: str | None
    char_offset_start: int
    char_offset_end: int
    text: str
    rrf_score: float
    bm25_rank: int | None = None   # None if not in BM25 results
    dense_rank: int | None = None  # None if not in dense results
    rerank_score: float | None = None


# ---------------------------------------------------------------------------
# BM25 via Postgres FTS
# ---------------------------------------------------------------------------


async def _bm25_search(
    query: str,
    top_k: int,
    filters: dict[str, Any],
    session: Any,  # AsyncSession
) -> list[dict[str, Any]]:
    """Run a plainto_tsquery FTS search and return ranked chunk rows."""
    from sqlalchemy import text

    where_clauses = ["c.text_tsv @@ plainto_tsquery('english', :query)"]
    params: dict[str, Any] = {"query": query, "limit": top_k}

    if filters.get("ticker"):
        where_clauses.append("co.ticker = ANY(:tickers)")
        tickers = filters["ticker"] if isinstance(filters["ticker"], list) else [filters["ticker"]]
        params["tickers"] = tickers

    if filters.get("fiscal_year"):
        where_clauses.append("f.fiscal_year = ANY(:years)")
        years = filters["fiscal_year"] if isinstance(filters["fiscal_year"], list) else [filters["fiscal_year"]]
        params["years"] = years

    if filters.get("form"):
        where_clauses.append("f.form = ANY(:forms)")
        forms = filters["form"] if isinstance(filters["form"], list) else [filters["form"]]
        params["forms"] = forms

    if filters.get("section"):
        where_clauses.append("c.section = :section")
        params["section"] = filters["section"]

    where_sql = " AND ".join(where_clauses)

    sql = text(
        f"""
        SELECT
            c.id            AS chunk_id,
            c.filing_id,
            co.ticker,
            f.fiscal_year,
            f.form,
            c.section,
            c.item_label,
            c.char_offset_start,
            c.char_offset_end,
            c.text,
            ts_rank_cd(c.text_tsv, plainto_tsquery('english', :query)) AS score
        FROM chunks c
        JOIN filings f    ON f.id  = c.filing_id
        JOIN companies co ON co.cik = f.cik
        WHERE {where_sql}
        ORDER BY score DESC
        LIMIT :limit
        """
    )

    rows = (await session.execute(sql, params)).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Dense search via Qdrant
# ---------------------------------------------------------------------------


def _embed_query(query: str) -> list[float]:
    """Embed a single query string using the BGE model singleton."""
    from backend.indexing.embed_corpus import embed_texts, get_model

    model = get_model()
    return embed_texts([query], model)[0]


def _build_qdrant_filter(filters: dict[str, Any]) -> Any | None:
    """Translate filter dict to a Qdrant Filter object."""
    from qdrant_client.http import models as qm

    conditions = []

    if filters.get("ticker"):
        tickers = filters["ticker"] if isinstance(filters["ticker"], list) else [filters["ticker"]]
        conditions.append(
            qm.FieldCondition(
                key="ticker",
                match=qm.MatchAny(any=tickers),
            )
        )

    if filters.get("fiscal_year"):
        years = filters["fiscal_year"] if isinstance(filters["fiscal_year"], list) else [filters["fiscal_year"]]
        conditions.append(
            qm.FieldCondition(
                key="fiscal_year",
                match=qm.MatchAny(any=years),
            )
        )

    if filters.get("form"):
        forms = filters["form"] if isinstance(filters["form"], list) else [filters["form"]]
        conditions.append(
            qm.FieldCondition(
                key="form",
                match=qm.MatchAny(any=forms),
            )
        )

    if not conditions:
        return None

    return qm.Filter(must=conditions)


def _dense_search_sync(
    query: str,
    top_k: int,
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Synchronous dense vector search via Qdrant, called from run_in_executor."""
    from backend.indexing.embed_corpus import get_qdrant_client

    settings = get_settings()
    vector = _embed_query(query)
    qdrant = get_qdrant_client()
    qdrant_filter = _build_qdrant_filter(filters)

    hits = qdrant.search(
        collection_name=settings.qdrant_collection,
        query_vector=vector,
        query_filter=qdrant_filter,
        limit=top_k,
        with_payload=True,
    )

    results = []
    for hit in hits:
        payload = hit.payload or {}
        results.append(
            {
                "chunk_id": payload.get("chunk_id", ""),
                "filing_id": payload.get("filing_id", ""),
                "ticker": payload.get("ticker", ""),
                "fiscal_year": payload.get("fiscal_year"),
                "form": payload.get("form", ""),
                "section": payload.get("section", ""),
                "item_label": payload.get("item_label"),
                "char_offset_start": payload.get("char_offset_start", 0),
                "char_offset_end": payload.get("char_offset_end", 0),
                "text_preview": payload.get("text_preview", ""),
                "dense_score": hit.score,
            }
        )
    return results


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


def _rrf_merge(
    bm25_rows: list[dict[str, Any]],
    dense_rows: list[dict[str, Any]],
    k: int = RRF_K,
) -> list[tuple[str, float, int | None, int | None]]:
    """Merge two ranked lists with Reciprocal Rank Fusion.

    Returns: [(chunk_id, rrf_score, bm25_rank, dense_rank), ...]  sorted desc.
    """
    scores: dict[str, float] = {}
    bm25_ranks: dict[str, int] = {}
    dense_ranks: dict[str, int] = {}

    for rank, row in enumerate(bm25_rows, start=1):
        cid = str(row["chunk_id"])
        bm25_ranks[cid] = rank
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)

    for rank, row in enumerate(dense_rows, start=1):
        cid = str(row["chunk_id"])
        dense_ranks[cid] = rank
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [
        (cid, score, bm25_ranks.get(cid), dense_ranks.get(cid))
        for cid, score in merged
    ]


# ---------------------------------------------------------------------------
# Enrich merged results with full text from Postgres
# ---------------------------------------------------------------------------


async def _enrich_chunks(
    ranked: list[tuple[str, float, int | None, int | None]],
    bm25_rows: list[dict[str, Any]],
    dense_rows: list[dict[str, Any]],
    session: Any,
) -> list[RetrievalResult]:
    """Fetch full text + metadata for each chunk_id in the merged list."""
    from sqlalchemy import text

    # Build a lookup from bm25 rows (already have full text)
    bm25_by_id = {str(r["chunk_id"]): r for r in bm25_rows}
    dense_by_id = {r["chunk_id"]: r for r in dense_rows}

    # Find any chunk_ids we don't have full text for (dense-only hits)
    dense_only_ids = [
        cid for cid, *_ in ranked
        if cid not in bm25_by_id
    ]

    pg_rows: dict[str, dict[str, Any]] = {}
    if dense_only_ids:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT
                        c.id::text AS chunk_id,
                        c.filing_id::text,
                        co.ticker,
                        f.fiscal_year,
                        f.form,
                        c.section,
                        c.item_label,
                        c.char_offset_start,
                        c.char_offset_end,
                        c.text
                    FROM chunks c
                    JOIN filings f    ON f.id  = c.filing_id
                    JOIN companies co ON co.cik = f.cik
                    WHERE c.id = ANY(:ids::uuid[])
                    """
                ),
                {"ids": dense_only_ids},
            )
        ).fetchall()
        pg_rows = {r[0]: dict(r._mapping) for r in rows}

    results: list[RetrievalResult] = []
    for cid, rrf_score, bm25_rank, dense_rank in ranked:
        if cid in bm25_by_id:
            row = bm25_by_id[cid]
        elif cid in pg_rows:
            row = pg_rows[cid]
        else:
            # Dense-only with no Postgres row found (shouldn't happen)
            continue

        results.append(
            RetrievalResult(
                chunk_id=cid,
                filing_id=str(row.get("filing_id", "")),
                ticker=row.get("ticker", ""),
                fiscal_year=row.get("fiscal_year", 0),
                form=row.get("form", ""),
                section=row.get("section", ""),
                item_label=row.get("item_label"),
                char_offset_start=row.get("char_offset_start", 0),
                char_offset_end=row.get("char_offset_end", 0),
                text=row.get("text", row.get("text_preview", "")),
                rrf_score=rrf_score,
                bm25_rank=bm25_rank,
                dense_rank=dense_rank,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


async def retrieve(
    query: str,
    top_k: int = 20,
    rerank: bool = True,
    rerank_top_k: int = 10,
    **filters: Any,
) -> list[RetrievalResult]:
    """Main entry point for hybrid retrieval.

    Args:
        query:        Natural-language query string.
        top_k:        Number of candidates to fetch from each source before RRF.
        rerank:       Whether to apply BGE-reranker-large cross-encoder.
        rerank_top_k: How many final results to return after reranking.
        **filters:    Keyword filters, ticker, fiscal_year, form, section.
                      Each accepts a single value or a list.

    Returns:
        List of RetrievalResult, sorted by rerank_score (if reranked) or rrf_score.
    """
    from backend.db.session import get_session

    async with get_session() as session:
        bm25_rows, dense_rows = await _run_parallel_search(
            query, top_k, filters, session
        )

    ranked = _rrf_merge(bm25_rows, dense_rows)

    async with get_session() as session:
        results = await _enrich_chunks(ranked, bm25_rows, dense_rows, session)

    if rerank and results:
        from backend.retrieval.reranker import rerank_results
        results = rerank_results(query, results, top_k=rerank_top_k)

    log.debug(
        "retrieval.done",
        query=query[:80],
        bm25_hits=len(bm25_rows),
        dense_hits=len(dense_rows),
        merged=len(results),
    )
    return results


async def _run_parallel_search(
    query: str,
    top_k: int,
    filters: dict[str, Any],
    session: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run BM25 (async Postgres) and dense (sync Qdrant in executor) concurrently."""
    import asyncio

    loop = asyncio.get_running_loop()

    # BM25 is an async Postgres query; dense uses the sync Qdrant client in
    # a thread pool to avoid blocking the event loop.
    bm25_results, dense_results = await asyncio.gather(
        _bm25_search(query, top_k, filters, session),
        loop.run_in_executor(None, _dense_search_sync, query, top_k, filters),
    )
    return bm25_results, dense_results
