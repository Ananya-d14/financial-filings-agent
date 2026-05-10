# Costs

Total LLM USD spend over the entire build: **$0**. Everything runs on free tiers or local hardware.

## Stack costs

| Component | Where it runs | Cost |
|---|---|---|
| Groq (Llama 3.1 8B-instant) | Cloud, free tier | $0 |
| Ollama (Qwen 2.5 7B Q5) | My RTX 2080 Ti | electricity |
| BGE-large embeddings | Same GPU | electricity |
| BGE-reranker-large | Same GPU | electricity |
| Qdrant | Docker locally / Cloud free tier in prod | $0 |
| Postgres 16 | Docker locally / Supabase free tier in prod | $0 |
| Langfuse v2 | Docker locally | $0 |
| SEC EDGAR + companyfacts | Public API, fair-use | $0 |
| `edgartools` | OSS | $0 |

## Budget vs actuals

| Operation | Estimate | Actual | Notes |
|---|---|---|---|
| Filing ingestion (SEC) | $0 | $0 | Hours of network time, not money |
| Embedding ~32k chunks | $0 | $0 | Local BGE on the 2080 Ti |
| Tools layer dev | <$1 | $0 | Tests don't hit the LLM |
| Agent dev iterations | $5-10 | $0 | Switched to Groq |
| Eval dev runs | $5-10 / sweep | $0 | Groq free tier handles it |
| Full 100Q ablation | $15-30 | $0 | Same |
| Deployment + demo | $0 | $0 | Free tier hosting |

The first plan budgeted $40-80 in API spend assuming Anthropic Claude. Switching to Groq + Ollama brought it to zero. The trade-off is real: Llama 3.1 8B-instant does materially worse than Sonnet 4.5 on Tier 4 multi-hop reasoning. For a portfolio project that's a fair price for $0 ongoing cost.

## Per-query targets vs measured

Targets from the plan:

| Tier | p95 target | Reasoning |
|---|---|---|
| 1 | <3s | single tool call |
| 2 | <8s | 1-2 tool calls |
| 3 | <15s | Multi-company XBRL + comparison |
| 4 | <25s | Full reflection loop |

Measured numbers land in `EVAL_RESULTS.md` after the final ablation run.

## Free-tier ceilings to design against

These limits change. Verify before each major eval sweep at https://console.groq.com/docs/rate-limits.

- Groq Llama 3.1 8B-instant free: ~30 RPM, daily token cap is the real constraint.
- Ollama: bounded by GPU. Qwen 2.5 7B Q5 hits ~60-80 tok/s on the 2080 Ti.

The eval runner reads RPM/TPM from response headers, maintains a rolling-window estimator, and sleeps between requests to stay 80% under the ceiling. On 429, it backs off exponentially up to 3 retries and then routes to Ollama.

## How costs are tracked

Langfuse logs every LLM call with token counts. The Groq/Ollama provider cost field is always $0. `backend/eval/metrics.py` aggregates per-query tokens and latency. The USD column in the eval table stays at $0 throughout.
