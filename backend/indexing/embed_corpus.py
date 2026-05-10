"""Embed the chunks corpus with BAAI/bge-large-en-v1.5.

Pipeline
--------
1. Query Postgres for chunks that have no qdrant_point_id yet (pending).
2. Encode texts in batches on GPU (RTX 2080 Ti ≈ 30-60 min for ~60k chunks).
3. Upsert vectors to Qdrant, each point ID is the chunk's Postgres UUID.
4. UPDATE chunks.qdrant_point_id in Postgres to mark as embedded.

Resumability: only processes chunks WHERE qdrant_point_id IS NULL, so any
restart after interruption continues from where it left off.

BGE-large-en-v1.5 notes
-----------------------
* Output dimension: 1024
* normalize_embeddings=True  ->  cosine similarity via dot product (faster in Qdrant)
* No instruction prefix needed for v1.5 documents or queries.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.config import get_settings
from backend.db.session import get_session
from backend.logging_config import get_logger

log = get_logger(__name__)

BGE_VECTOR_SIZE = 1024
DB_FETCH_BATCH = 512   # rows fetched from Postgres per iteration
EMBED_BATCH_SIZE = 64  # texts sent to the model at once


# ---------------------------------------------------------------------------
# Model singleton, loaded once per process.
# ---------------------------------------------------------------------------

_model: Any = None  # SentenceTransformer, typed as Any to avoid hard import at top-level


def get_model(device: str | None = None) -> Any:
    """Load and cache the BGE-large-en-v1.5 sentence-transformer model."""
    global _model
    if _model is not None:
        return _model

    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    resolved_device = device or settings.embedding_device

    if resolved_device == "auto":
        import torch
        resolved_device = "cuda" if torch.cuda.is_available() else "cpu"

    log.info("embedder.loading_model", model=settings.embedding_model, device=resolved_device)
    _model = SentenceTransformer(settings.embedding_model, device=resolved_device)
    log.info("embedder.model_loaded")
    return _model


def embed_texts(texts: list[str], model: Any) -> list[list[float]]:
    """Encode a list of texts and return normalised float32 vectors."""
    vectors = model.encode(
        texts,
        batch_size=EMBED_BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------


def get_qdrant_client() -> Any:
    from qdrant_client import QdrantClient

    settings = get_settings()
    api_key = settings.qdrant_api_key.get_secret_value() or None
    return QdrantClient(url=settings.qdrant_url, api_key=api_key)


def ensure_collection(client: Any) -> None:
    """Create the Qdrant collection if it doesn't already exist."""
    from qdrant_client.http import models as qm

    settings = get_settings()
    collection = settings.qdrant_collection
    existing = [c.name for c in client.get_collections().collections]
    if collection in existing:
        log.info("qdrant.collection_exists", collection=collection)
        return

    client.create_collection(
        collection_name=collection,
        vectors_config=qm.VectorParams(
            size=BGE_VECTOR_SIZE,
            distance=qm.Distance.COSINE,
        ),
        hnsw_config=qm.HnswConfigDiff(m=16, ef_construct=100),
        # Optimise for high-accuracy retrieval; latency is less critical for
        # an offline-indexing job than for real-time search.
        optimizers_config=qm.OptimizersConfigDiff(indexing_threshold=20_000),
    )
    log.info("qdrant.collection_created", collection=collection)


def upsert_points(client: Any, points: list[dict[str, Any]]) -> None:
    """Upsert a batch of {id, vector, payload} dicts to Qdrant."""
    from qdrant_client.http import models as qm

    settings = get_settings()
    client.upsert(
        collection_name=settings.qdrant_collection,
        points=[
            qm.PointStruct(
                id=p["id"],
                vector=p["vector"],
                payload=p["payload"],
            )
            for p in points
        ],
    )


# ---------------------------------------------------------------------------
# Main embedding pipeline
# ---------------------------------------------------------------------------


async def embed_pending_chunks(
    db_batch: int = DB_FETCH_BATCH,
) -> dict[str, int]:
    """Embed all chunks that don't have a qdrant_point_id yet.

    Returns: {"embedded": N, "skipped": M}
    """
    from sqlalchemy import text

    model = get_model()
    qdrant = get_qdrant_client()
    ensure_collection(qdrant)

    total_embedded = 0
    total_skipped = 0
    offset = 0

    while True:
        async with get_session() as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT
                            c.id,
                            c.text,
                            c.section,
                            c.item_label,
                            c.char_offset_start,
                            c.char_offset_end,
                            f.id        AS filing_id,
                            co.ticker   AS ticker,
                            f.fiscal_year,
                            f.form
                        FROM chunks c
                        JOIN filings f  ON f.id  = c.filing_id
                        JOIN companies co ON co.cik = f.cik
                        WHERE c.qdrant_point_id IS NULL
                        ORDER BY c.id
                        LIMIT :limit OFFSET :offset
                        """
                    ),
                    {"limit": db_batch, "offset": offset},
                )
            ).fetchall()

        if not rows:
            break

        texts = [r[1] for r in rows]
        vectors = embed_texts(texts, model)

        points: list[dict[str, Any]] = []
        chunk_ids: list[str] = []
        point_ids: list[str] = []

        for row, vector in zip(rows, vectors):
            (
                chunk_id, chunk_text, section, item_label, offset_start, offset_end,
                filing_id, ticker, fiscal_year, form,
            ) = row
            point_id = str(uuid.uuid4())
            points.append(
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "chunk_id": str(chunk_id),
                        "filing_id": str(filing_id),
                        "ticker": ticker,
                        "fiscal_year": fiscal_year,
                        "form": form,
                        "section": section,
                        "item_label": item_label,
                        "char_offset_start": offset_start,
                        "char_offset_end": offset_end,
                        # Preview text (first 200 chars) stored in Qdrant payload
                        # for UI display without hitting Postgres.
                        "text_preview": chunk_text[:200],
                    },
                }
            )
            chunk_ids.append(str(chunk_id))
            point_ids.append(point_id)

        # Upsert vectors to Qdrant
        upsert_points(qdrant, points)

        # Mark chunks as embedded in Postgres
        async with get_session() as session:
            for chunk_id, point_id in zip(chunk_ids, point_ids):
                await session.execute(
                    text(
                        "UPDATE chunks SET qdrant_point_id = :pid WHERE id = :cid"
                    ),
                    {"pid": point_id, "cid": chunk_id},
                )

        total_embedded += len(rows)
        offset += db_batch
        log.info("embedder.progress", embedded=total_embedded)

    log.info("embedder.done", embedded=total_embedded, skipped=total_skipped)
    return {"embedded": total_embedded, "skipped": total_skipped}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    from backend.logging_config import configure_logging
    from backend.config import get_settings

    configure_logging(level=get_settings().log_level, json_output=False)
    result = await embed_pending_chunks()
    print(f"\nEmbedded {result['embedded']} chunks.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
