"""HTTP routes.

Phase 4: /query (batch JSON)
Phase 6: /query/stream (SSE. streams plan, tool calls, reflection, answer)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from backend import __version__
from backend.agent.schemas import Answer
from backend.config import get_settings
from backend.logging_config import get_logger

router = APIRouter()
log = get_logger(__name__)


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class VersionResponse(BaseModel):
    version: str


class TickersResponse(BaseModel):
    tickers: list[str]
    fiscal_years: list[int]


class QueryRequest(BaseModel):
    query: str
    ticker_filters: list[str] | None = None
    year_filters: list[int] | None = None


class QueryResponse(BaseModel):
    answer: Answer | None
    error: str | None = None


@router.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(status="ok", version=__version__, environment=s.environment)


@router.get("/version", response_model=VersionResponse, tags=["meta"])
async def version() -> VersionResponse:
    return VersionResponse(version=__version__)


@router.get("/tickers", response_model=TickersResponse, tags=["meta"])
async def tickers() -> TickersResponse:
    s = get_settings()
    return TickersResponse(tickers=s.ticker_list, fiscal_years=s.fiscal_year_list)


@router.post("/query", response_model=QueryResponse, tags=["agent"])
async def query(req: QueryRequest) -> QueryResponse:
    """Batch /query, runs the full agent graph and returns a JSON Answer.

    For streaming results use POST /query/stream (SSE).
    """
    from backend.agent.graph import run_graph_loop

    if not req.query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty query.",
        )

    trace_id = str(uuid.uuid4())
    log.info("api.query.start", query=req.query[:100], trace_id=trace_id)

    try:
        result = await run_graph_loop(query=req.query, trace_id=trace_id)
    except Exception as exc:
        err_str = str(exc)
        log.error("api.query.error", error=err_str, trace_id=trace_id)
        # Give user-friendly messages for common failure modes
        if "rate" in err_str.lower() or "429" in err_str or "quota" in err_str.lower():
            friendly = "Groq free-tier rate limit hit. Wait 30 seconds and try again."
        elif "fallback provider is disabled" in err_str or "ollama" in err_str.lower():
            friendly = "LLM temporarily unavailable (rate limited). Wait 30 seconds and try again."
        else:
            friendly = f"Query failed: {err_str[:200]}"
        return QueryResponse(answer=None, error=friendly)

    log.info(
        "api.query.done",
        trace_id=trace_id,
        n_claims=len(result.answer.claims) if result.answer else 0,
        iterations=result.state.get("iteration", 0),
    )
    return QueryResponse(answer=result.answer)


@router.post("/query/stream", tags=["agent"])
async def query_stream(req: QueryRequest, request: Request):  # type: ignore[return]
    """SSE endpoint, streams plan, tool_call, tool_result, reflection, synthesis, done.

    Each event is a JSON-encoded StreamEvent:
      data: {"type": "plan", "data": {...}, "trace_id": "...", "iteration": 0}

    The client should read the stream until it receives a "done" or "error" event.
    """
    from sse_starlette.sse import EventSourceResponse

    from backend.api.streaming import stream_graph_loop

    if not req.query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty query.",
        )

    trace_id = str(uuid.uuid4())
    log.info("api.stream.start", query=req.query[:100], trace_id=trace_id)

    async def event_generator():  # type: ignore[return-type]
        async for event in stream_graph_loop(query=req.query, trace_id=trace_id):
            # Check if the client disconnected
            if await request.is_disconnected():
                log.info("api.stream.client_disconnected", trace_id=trace_id)
                break
            yield {"event": event.type.value, "data": event.model_dump_json()}

    return EventSourceResponse(event_generator(), headers={"X-Trace-Id": trace_id})
