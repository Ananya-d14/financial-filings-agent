# Production backend image. Slim Python base + uv for fast deps.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# System deps for lxml + curl for the healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libxml2-dev libxslt-dev curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

# Install only runtime deps. The prod API doesn't run embeddings or rerankers
# locally. Those run during ingestion which is a separate job. Keeping the
# image slim helps Railway cold starts.
COPY pyproject.toml ./
RUN uv pip install --system --no-cache \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.32" \
    "pydantic>=2.9" \
    "pydantic-settings>=2.6" \
    "sqlalchemy[asyncio]>=2.0" \
    "asyncpg>=0.30" \
    "qdrant-client>=1.12" \
    "groq>=0.13" \
    "ollama>=0.4" \
    "langgraph>=0.2.50" \
    "langchain-core>=0.3" \
    "langchain-groq>=0.2" \
    "structlog>=24.4" \
    "sse-starlette>=2.1" \
    "httpx>=0.27" \
    "tenacity>=9.0" \
    "beautifulsoup4>=4.12" \
    "lxml>=5.3" \
    "python-dotenv>=1.0"

COPY backend/ ./backend/

# Non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/health || exit 1

# Railway sets $PORT
CMD ["sh", "-c", "uvicorn backend.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
