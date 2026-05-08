"""FastAPI entrypoint.

Phase 0: serves /health, /version, /tickers and a stub /query that 501s.
Phase 4-6: /query becomes an SSE stream emitting plan/tool_call/...etc events.

Run locally:
    uv run uvicorn backend.api.main:app --reload
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from backend import __version__
from backend.api.routes import router
from backend.config import get_settings
from backend.logging_config import bind_trace_id, clear_trace_context, configure_logging, get_logger


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.is_production)
    log = get_logger(__name__)
    log.info("backend.startup", version=__version__, environment=settings.environment)
    yield
    log.info("backend.shutdown")


app = FastAPI(
    title="Financial Filings Analyst",
    description="Agentic RAG over SEC filings with grounded citations.",
    version=__version__,
    lifespan=lifespan,
)

# Permissive CORS for local dev; tighten in prod via env-driven config later.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Generate a trace_id per request, bind to logging context, expose in response header.

    The same ID propagates to Langfuse traces and tool-call metadata further
    down the stack, recruiters reviewing logs can pivot from UI to backend
    to Langfuse via this single value.
    """
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
    bind_trace_id(trace_id)
    try:
        response: Response = await call_next(request)
    finally:
        clear_trace_context()
    response.headers["X-Trace-Id"] = trace_id
    return response


app.include_router(router)
