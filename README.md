# financial-filings-agent

An agent that answers questions about SEC filings (10-K, 10-Q, 8-K) for 20 S&P 500 companies, FY2020 to FY2024. It pulls structured financial data via XBRL, retrieves narrative text via hybrid search, does the arithmetic with a deterministic calculator, and verifies every numeric claim against the source filing before showing it.

I built this to learn how to put a real RAG system together end-to-end and to have something concrete to point at when people ask what I've actually shipped.

## What it answers

Four difficulty tiers, mixed across the benchmark:

```
Tier 1: What was NVIDIA's FY2024 R&D expense?
Tier 2: Summarise Tesla's 2024 China-related risk factors.
Tier 3: Compare gross margins of MSFT, GOOGL, AAPL from 2020 to 2024.
Tier 4: Which mega-cap tech firms grew capex faster than revenue 2022 to 2024,
        and what reasons did they cite?
```

## Why I cared about getting this right

A regular chatbot pointed at filings will happily invent numbers. For finance that is unusable. The non-negotiables I worked from:

1. The agent never does arithmetic. Every number flows through a Python calculator.
2. Numeric data comes from XBRL, not text extraction.
3. Every numeric claim has a citation that is programmatically verified.
4. The reflection loop is bounded (3 iterations max) so cost and latency stay predictable.
5. The whole stack runs on free tiers. Total LLM spend across the entire project: $0.

## Stack

| Layer | Choice | Why |
|---|---|---|
| LLM | Llama 3.3 70B via Groq, Llama 3.1 8B for cheap routing | Free tier, fast inference (~250 tok/s) |
| Local fallback LLM | Qwen 2.5 7B Q5_K_M via Ollama | When Groq throttles. Runs on my RTX 2080 Ti |
| Embeddings | `BAAI/bge-large-en-v1.5` | Local, no API cost, decent quality |
| Reranker | `BAAI/bge-reranker-large` | Cross-encoder on candidates |
| Vector DB | Qdrant | Filter pushdown by ticker/year/form |
| Lexical search | Postgres FTS (BM25) | One DB instead of running Elasticsearch |
| Structured DB | Postgres 16 | XBRL facts, filings, chunks |
| Orchestration | LangGraph | Reflection loop is easier to model as a graph |
| Backend | FastAPI (async, SSE streaming) | |
| Frontend | Next.js 14 App Router | |
| Tracing | Langfuse v2 self-hosted | |

## Repo layout

```
backend/
  ingestion/        download filings, XBRL parser, section-aware chunker
  indexing/         embed corpus, build Qdrant + Postgres FTS indexes
  retrieval/        hybrid retriever (BM25 + dense + RRF), reranker, XBRL SQL
  agent/            planner, tools, calculator, citation verifier, graph
  eval/             benchmark questions, metrics, judge, ablation runner
  api/              FastAPI app, /query and /query/stream
  tests/            unit and integration tests
frontend/           Next.js streaming chat UI
```

## Architecture (one paragraph)

A user query goes to the planner, which emits a typed list of sub-tasks routed to one of five tools (XBRL SQL, hybrid retriever, calculator, filing diff, citation verifier). The reflector runs deterministic checks after tools execute and again after synthesis: every numeric claim must have a citation, every citation must resolve to text that supports the claim, every plan sub-task must produce non-empty output. If any check fails the LLM proposes a refined plan and we loop, capped at 3 iterations. Synthesis is the only place markdown gets written; the reflector verifies it before it leaves the system. Full diagram and component-level detail in `ARCHITECTURE.md`.

## Evaluation

The thing most portfolio RAG projects skip. I built a benchmark of 100 questions across the four tiers, scored five ablation configs (vanilla RAG, plus hybrid, plus XBRL, plus agentic planning, plus reflection), and tracked numerical accuracy, citation faithfulness, latency p50/p95, and per-query token cost. An LLM judge calibrated against human ratings (Cohen's kappa reported) supplements the automated metrics. Failure modes auto-categorised into 7 buckets so I can see where the system actually breaks.

Numbers in `EVAL_RESULTS.md`.

## Running it locally

You need Docker, a Groq API key (free), and Python 3.11.

```bash
cp .env.example .env             # fill GROQ_API_KEY and SEC_USER_AGENT
docker compose up -d             # Postgres, Qdrant, Langfuse
uv sync                          # Python deps
ollama pull qwen2.5:7b-instruct-q5_K_M    # local fallback model

# Ingest a small corpus first to test
uv run python -m backend.ingestion.run --tickers NVDA --years 2024
uv run python -m backend.indexing.embed_corpus
uv run python -m backend.indexing.build_indexes

# Backend
uv run uvicorn backend.api.main:app --reload

# Frontend (separate terminal)
cd frontend && pnpm install && pnpm dev
```

Open http://localhost:3000 and ask: "What was NVDA's FY2024 R&D expense?"

For a quick sanity check that doesn't need Docker, run `python demo.py` — it shows the deterministic layers (calculator, citation verifier, live SEC API call).

## Things I learned the hard way

- Numeric extraction regex is fiddlier than it looks. `$(96.9) million` parses as accounting-negative; `1,234,567` needs comma handling; `74.6%` is a percent but `1.5x` isn't a number at all. The version in `citation_verifier.py` went through a few rewrites.
- XBRL tag heterogeneity is real. Revenue alone has six valid US-GAAP concept names across the 20 tickers. The synonym table in `xbrl_parser.py` is curated, and unmapped tags get logged for review.
- LangGraph is overkill for the happy path but earns its keep when reflection refines the plan and routes back through the planner. A plain Python loop also exists in `graph.py` for testing.
- Groq's free tier is generous for dev but gets rate-limited during eval sweeps. The eval runner sleeps between requests; full 100-question x 5-config sweep takes 2 to 4 hours.
- Self-hosted Langfuse v3 needs Postgres + Clickhouse + Redis + MinIO. v2 needs one Postgres. I went with v2.

## Things I'd change with more time

- Move from sync `qdrant-client` (run in executor) to `AsyncQdrantClient`.
- Add proper streaming markdown rendering to the frontend (currently a hand-written renderer; `react-markdown` is in `package.json` but not wired).
- 8-K parsing is event-item-aware but rough. Specific item types (2.02 results of operations) deserve dedicated parsers.
- The judge prompt could use a calibration round on the actual 100-question set rather than just the schema.

## Cost

$0 in API spend over the entire build. Local GPU electricity not counted. Details in `COSTS.md`.

## Live demo

Backend: https://financial-filings-agent-production.up.railway.app/health

Frontend: (Vercel, coming next)

## Deploying

`DEPLOY.md` walks through the production setup: Supabase Postgres, Qdrant Cloud, Railway for the backend, Vercel for the frontend. All on free tiers.

## License

MIT, see `LICENSE`.

## Author

Ananya Joshi · joshi.ananya.joshi@gmail.com
