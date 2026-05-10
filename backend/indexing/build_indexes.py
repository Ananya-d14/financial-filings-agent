"""Index builder, run once after ingestion, safe to re-run.

Tasks
-----
1. Backfill any chunks.text_tsv that are NULL (shouldn't happen if the trigger
   fired, but guards against bulk-insert paths that bypass triggers).
2. Verify the GIN index on chunks.text_tsv exists; create it if missing.
3. Create the Qdrant collection if embed_corpus.py hasn't done so yet.
4. Print a short health report (collection vector count vs Postgres chunk count).

This script does NOT embed vectors, that's embed_corpus.py. Run order:
    1. ingest  (run.py)
    2. embed   (embed_corpus.py)
    3. index   (build_indexes.py)  <- this file, verifies everything is wired up

Usage:
    uv run python -m backend.indexing.build_indexes
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from backend.config import get_settings
from backend.db.session import get_session
from backend.indexing.embed_corpus import BGE_VECTOR_SIZE, ensure_collection, get_qdrant_client
from backend.logging_config import configure_logging, get_logger

log = get_logger(__name__)


async def backfill_tsvectors() -> int:
    """Update text_tsv for any chunks where it's NULL (trigger miss recovery)."""
    async with get_session() as session:
        result = await session.execute(
            text(
                """
                UPDATE chunks
                SET text_tsv = to_tsvector('english', COALESCE(text, ''))
                WHERE text_tsv IS NULL
                RETURNING id
                """
            )
        )
        count = len(result.fetchall())
    if count:
        log.info("index.tsvector_backfill", count=count)
    return count


async def ensure_gin_index() -> None:
    """Create GIN index on chunks.text_tsv if missing."""
    async with get_session() as session:
        exists = await session.execute(
            text(
                """
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'chunks'
                  AND indexname = 'idx_chunks_tsv'
                LIMIT 1
                """
            )
        )
        if exists.scalar():
            log.info("index.gin_exists")
            return

        log.info("index.creating_gin")
        await session.execute(
            text("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN (text_tsv)")
        )
    log.info("index.gin_created")


async def health_report() -> dict[str, int]:
    """Compare Postgres chunk count vs embedded count vs Qdrant vector count."""
    async with get_session() as session:
        total_chunks = (
            await session.execute(text("SELECT COUNT(*) FROM chunks"))
        ).scalar() or 0
        embedded_chunks = (
            await session.execute(
                text("SELECT COUNT(*) FROM chunks WHERE qdrant_point_id IS NOT NULL")
            )
        ).scalar() or 0
        total_filings = (
            await session.execute(text("SELECT COUNT(*) FROM filings"))
        ).scalar() or 0
        total_xbrl = (
            await session.execute(text("SELECT COUNT(*) FROM xbrl_facts"))
        ).scalar() or 0

    settings = get_settings()
    qdrant = get_qdrant_client()
    try:
        collection_info = qdrant.get_collection(settings.qdrant_collection)
        qdrant_count = collection_info.vectors_count or 0
    except Exception:
        qdrant_count = 0

    report = {
        "filings": int(total_filings),
        "xbrl_facts": int(total_xbrl),
        "chunks_total": int(total_chunks),
        "chunks_embedded": int(embedded_chunks),
        "qdrant_vectors": int(qdrant_count),
        "unembedded": int(total_chunks) - int(embedded_chunks),
    }

    print("\n=== Index health report ===")
    for k, v in report.items():
        flag = " " if k == "unembedded" and v > 0 else ""
        print(f"  {k}: {v}{flag}")

    if report["chunks_total"] == 0:
        print("\n    No chunks found. Run ingestion first:")
        print("     uv run python -m backend.ingestion.run --tickers NVDA --years 2024")
    elif report["unembedded"] > 0:
        print(f"\n    {report['unembedded']} chunks not yet embedded. Run:")
        print("     uv run python -m backend.indexing.embed_corpus")

    return report


async def build_all() -> None:
    settings = get_settings()

    log.info("index.start")

    # 1. Backfill any missed tsvectors
    await backfill_tsvectors()

    # 2. Ensure Postgres GIN index
    await ensure_gin_index()

    # 3. Ensure Qdrant collection exists
    qdrant = get_qdrant_client()
    ensure_collection(qdrant)

    # 4. Print health report
    await health_report()

    log.info("index.done")


if __name__ == "__main__":
    configure_logging(level=get_settings().log_level, json_output=False)
    asyncio.run(build_all())
