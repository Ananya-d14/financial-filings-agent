# Architecture

## Query lifecycle

```
                       ┌─────────────────────┐
                       │   FastAPI /query    │   batch JSON
                       │ /query/stream (SSE) │   per-event streaming
                       └──────────┬──────────┘
                                  │
                                  ▼
                       ┌─────────────────────┐
                       │       Planner       │   Llama 3.1 8B-instant on Groq
                       │  query -> SubTask[]  │   JSON-mode output, Pydantic-validated
                       └──────────┬──────────┘
                                  │
                                  ▼
                       ┌─────────────────────┐
                       │      Tool Router    │   per-task dispatch
                       └──────────┬──────────┘
                                  │
       ┌──────────┬───────────────┼───────────────┬──────────────┐
       ▼          ▼               ▼               ▼              ▼
   ┌────────┐ ┌────────┐ ┌─────────────────┐ ┌─────────┐ ┌──────────────┐
   │Filing  │ │XBRL    │ │ Hybrid          │ │Calc     │ │Filing Diff   │
   │Retr.   │ │SQL     │ │ (BM25+dense+RRF │ │(safe AST│ │(YoY section) │
   │        │ │        │ │  +rerank)       │ │ no eval)│ │              │
   └────────┘ └────────┘ └─────────────────┘ └─────────┘ └──────────────┘
       │          │               │               │              │
       └──────────┴───────────────┴───────────────┴──────────────┘
                                  │
                                  ▼
                       ┌─────────────────────┐
                       │   Reflector pass 1  │   plan completeness check
                       └──────────┬──────────┘
                                  │  ok? -> continue
                                  │  refined plan? -> loop to planner
                                  ▼
                       ┌─────────────────────┐
                       │     Synthesizer     │   Llama 3.1 8B-instant
                       │   evidence -> Answer │   markdown + structured claims
                       └──────────┬──────────┘
                                  │
                                  ▼
                       ┌─────────────────────┐
                       │   Reflector pass 2  │   citation verification
                       └──────────┬──────────┘   (≤3 iterations total)
                                  │
                                  ▼
                                 done
```

Every node emits a Langfuse span. State carries `evidence: list[Citation]`, `tool_calls: list[ToolCall]`, `iteration: int`.

## Components

### Ingestion (`backend/ingestion/`)

`edgar_downloader.py` uses the SEC submissions REST API directly with an httpx async client capped at 8 req/s and a User-Agent from settings. SHA-256 content hashing makes re-runs idempotent. It chooses `submissions/CIK{cik}.json` over `edgartools` because the JSON is simpler to handle in batch and pagination via `filings.files[]` is straightforward.

`xbrl_parser.py` pulls `companyfacts/CIK{cik}.json` per company and maps raw GAAP concepts to canonical names. The synonym table covers the 20-ticker universe (51 entries as of last update) including bank-specific tags (`InterestIncomeExpenseNet`, `NoninterestIncome`) and oil-and-gas variants (`OilAndGasRevenue`, `RevenueFromContractWithCustomerAndOtherOperatingRevenue`). Unmapped tags get logged so the table can be extended without a code change.

`narrative_parser.py` strips HTML with BeautifulSoup, normalises Unicode (NFKC), and detects sections by regex. 10-K and 10-Q use the same `ITEM \d+[AB]?` pattern; 8-K uses `\d+\.\d{2}` for event items. Sections under 50 chars are skipped (table-of-contents entries). Each chunk persists `(filing_id, section, item_label, char_offset_start, char_offset_end, text)` so citations can be verified later.

### Indexing (`backend/indexing/`)

`embed_corpus.py` loads BGE-large once per process and processes chunks in DB batches of 512 (encode batches of 64 inside that). It only touches chunks where `qdrant_point_id IS NULL`, so any restart resumes from where it left off. ~60k chunks at 1024-d float32 = ~250 MB in Qdrant.

`build_indexes.py` is idempotent: backfills any missing tsvector via UPDATE-WHERE-NULL, ensures the GIN index exists (`CREATE INDEX CONCURRENTLY IF NOT EXISTS`), and ensures the Qdrant collection exists.

### Retrieval (`backend/retrieval/`)

`hybrid_retriever.py` runs BM25 (Postgres FTS) and dense (Qdrant) in parallel via `asyncio.gather`, with the sync Qdrant client wrapped in `loop.run_in_executor` to avoid blocking. Reciprocal Rank Fusion uses the standard k=60. Filter pushdown by ticker / fiscal_year / form / section happens in both queries before RRF; no post-filtering.

`reranker.py` lazy-loads BGE-reranker-large as a singleton. Scoring is `O(n)` in candidates so the default flow is: retrieve top 20, rerank to top 10. ~200 ms per call on the 2080 Ti.

`sql_retriever.py` is the structured XBRL query path. The agent's `XBRLSQLTool` should be called for any GAAP-tagged figure (revenue, net income, capex, R&D, etc.), text extraction is reserved for narrative claims.

### Agent (`backend/agent/`)

`llm.py` is the provider-agnostic chat wrapper. Two roles map to models: `primary` and `cheap` both go to Groq Llama 3.1 8B-instant in production (the 70B model is on Groq's free tier too but its daily token cap blew up during eval sweeps, so we ended up running everything on 8B). The fallback path is Ollama Qwen 2.5 7B; on Groq 429 it does exponential-backoff retries (5s/15s/30s, max 3) and then routes to Ollama if `LLM_FALLBACK_PROVIDER` allows it. `chat_json()` requests JSON mode, validates against a Pydantic schema, and on parse failure does one repair retry with a "your previous JSON was invalid" message. Both providers go through the same interface so swapping models is one env var.

`planner.py` heuristically routes Tier-1 single-fact queries to the cheap model (8B). The heuristic checks for at most one ticker mention and absence of comparison keywords (compare, versus, growth, CAGR). Conservative by design: false negatives cost a bit of latency, false positives cost accuracy.

`tools.py` is the dispatcher. Each tool has a typed Pydantic input/output and the `TOOL_REGISTRY` maps `ToolName` enum to callable. None of the tools call an LLM. The calculator uses an AST walker that allows only numeric literals and arithmetic operators, never `eval()`. The citation verifier resolves text via the chunks table first, falls back to re-parsing raw HTML if the offsets don't match a chunk row.

`reflector.py` runs three deterministic checks (numeric claims have citations, plan sub-tasks produced output, citations resolve and back the claim). Only refinement uses the LLM. Cap at 3 iterations.

`synthesizer.py` renders plan + evidence into a compact prompt, requests JSON, and fills in `trace_id` / `iterations` / `used_tools` post-hoc since the model often omits them.

`graph.py` has two implementations: `build_graph()` returns a compiled LangGraph state machine; `run_graph_loop()` is the equivalent in plain Python. Tests use the plain version. `/query` uses the plain version too, LangGraph is one more dependency that can break, and the loop is small enough that having a fallback is cheap.

`citation_verifier.py` (100% test coverage) handles three matching modes: numeric exact (zero-tolerance), numeric within tolerance (default 0.5%), semantic similarity via cosine of BGE embeddings (threshold 0.55). Number extraction handles commas, suffix multipliers (M/B/K/T/billion/million), parens-as-negative, and dot-leading decimals.

`calculator.py` (100% test coverage) supports 14 named operations and a safe-eval expression mode. The AST walker whitelists `Add, Sub, Mult, Div, Mod, Pow, FloorDiv, UAdd, USub, Constant`. Anything else raises.

### API (`backend/api/`)

`main.py` is the FastAPI app. Lifespan starts logging, middleware generates a per-request UUID trace ID, exposes it in the `X-Trace-Id` response header.

`routes.py` exposes `/health`, `/version`, `/tickers`, `POST /query` (batch JSON), `POST /query/stream` (SSE).

`streaming.py` wraps the same agent loop as an async generator yielding typed `StreamEvent` objects. Events: `plan`, `tool_call`, `tool_result`, `reflection`, `synthesis`, `done`, `error`.

### Eval (`backend/eval/`)

`benchmark_questions.jsonl` schema: `id`, `tier`, `question`, optional `gold_answer`, `gold_numeric`, `gold_unit`, `canonical_concept`, `ticker_filters`, `year_filters`, `gold_citations`, `tags`. Currently 20 stubs (5 per tier) for CI; the full 100-question gold set gets added before the final eval run.

`runner.py` defines five ablation configs. Configs 1-3 (Vanilla, +Hybrid, +XBRL) bypass the agent graph entirely and run a straight retrieve-then-synthesize pipeline. Configs 4-5 (Agentic, +Reflection) use the graph; reflection is toggled by patching `MAX_ITERATIONS = 1`.

`metrics.py` computes numerical accuracy (0.5% tolerance), citation faithfulness (% numeric claims with citations), latency p50/p95, mean tokens. Failure taxonomy heuristics tag each result with one of: `correct, retrieval_miss, table_parsing_error, arithmetic_error, hallucination, planning_failure, tool_error`.

`llm_judge.py` uses Groq Llama 3.1 8B-instant with a 4-point rubric (0-3). Cohen's kappa against a 30-question human-rated calibration set. Threshold for publishing: kappa >= 0.4 (moderate agreement). Self-preference bias is a real risk since the generator and judge are the same model, the calibration step is what catches it; ideally the judge is a stronger model than the generator, that's a known limitation here.

`run_eval.py` is the CLI. `--suite dev` runs the first 30 questions; `--suite full` runs all. `--gap-ms` adds sleep between Groq calls to stay under the free-tier RPM. Results write to `backend/eval/runs/{timestamp}.jsonl` and the ablation table section in `EVAL_RESULTS.md` is rewritten in place.

## Data model (Postgres)

```
companies(cik PK, ticker UNIQUE, name, sector, sic_code)

filings(id PK, cik FK, accession_number UNIQUE, form, fiscal_year,
        fiscal_period, filed_date, period_end, content_sha256, raw_path)

filing_sections(id PK, filing_id FK, section, item_label,
                char_offset_start, char_offset_end, text_md)

xbrl_facts(id PK, cik FK, concept, canonical_concept,
           period_start, period_end, value NUMERIC, unit,
           form, accession_number,
           UNIQUE (cik, canonical_concept, period_start, period_end, form))

chunks(id PK, filing_id FK, section, item_label,
       char_offset_start, char_offset_end, text,
       text_tsv tsvector,                          -- GIN-indexed for BM25
       qdrant_point_id UUID)                       -- 1:1 with Qdrant point
```

Indexes: `filings(cik, fiscal_year)`, `xbrl_facts(cik, canonical_concept, period_end)`, `chunks(filing_id, section)`, GIN on `chunks.text_tsv`. The `text_tsv` column is auto-populated by an INSERT/UPDATE trigger.

## External APIs

| Service | Limit | Auth | Use |
|---|---|---|---|
| SEC EDGAR submissions | 10 req/s (we use 8) | User-Agent | Filing discovery and download |
| SEC `companyfacts` | same | User-Agent | XBRL fact extraction |
| Groq | tier RPM/TPM | API key | Llama 3.1 8B-instant |
| Ollama | none (local) | none | Qwen 2.5 7B Q5_K_M |

Groq 429 retries with exponential backoff then falls back to Ollama. SEC requests outside the User-Agent guard get blocked at the application layer to prevent IP bans.

## Trace propagation

Every `/query` request gets a UUID4 trace ID. It returns in the `X-Trace-Id` response header, gets bound to the structlog context for the request, and stamps every Langfuse span. Recruiters or anyone debugging the demo can correlate UI events to backend logs to Langfuse traces with the same ID.

## Cost levers

The system was free across all phases. The levers that matter:

- Provider routing (Groq -> Ollama on 429). Removes Groq quota as a blocker.
- Model routing across Groq (8B-instant everywhere). 70B was original plan but the daily token cap was the binding constraint, not RPM.
- Local embeddings + reranker (BGE on the 2080 Ti). $0 instead of API embedding cost.
- Eval discipline: 30-question dev set during iteration, full 100 once a week.

See `COSTS.md` for the per-phase breakdown.
