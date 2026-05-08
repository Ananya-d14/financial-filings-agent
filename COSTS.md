# Costs

Total LLM USD spend over the entire build: **$0**. Everything runs on free tiers or local hardware.

## Stack costs

| Component | Where it runs | Cost |
|---|---|---|
| Groq (Llama 3.3 70B, 3.1 8B) | Cloud, free tier | $0 |
| Ollama (Qwen 2.5 7B Q5) | My RTX 2080 Ti | electricity |
| BGE-large embeddings | Same GPU | electricity |
| BGE-reranker-large | Same GPU | electricity |
| Qdrant | Docker locally / Cloud free tier in prod | $0 |
| Postgres 16 | Docker locally / Supabase free tier in prod | $0 |
| Langfuse v2 | Docker locally | $0 |
| SEC EDGAR + companyfacts | Public API, fair-use | $0 |
| `edgartools` | OSS | $0 |

## Per-phase budget vs actuals

Estimates were made in the planning doc; actuals tracked here.

| Phase | Operation | Estimate | Actual | Notes |
|---|---|---|---|---|
| 0 | Repo skeleton, infra | $0 | $0 | No API calls |
| 1 | Filing ingestion (SEC) | $0 | $0 | Hours of network time, not money |
| 2 | Embedding ~60k chunks | $0 | $0 | Local BGE on the 2080 Ti |
| 3 | Tools layer dev | <$1 | $0 | Tests don't hit the LLM |
| 4 | Agent dev iterations | $5-10 in plan | $0 | Switched to Groq, ate the cost myself |
| 5 | Eval dev runs | $5-10/sweep in plan | $0 | Groq free tier handles it |
| 5 | Full 100Q ablation | $15-30 in plan | $0 | Same |
| 6 | Frontend wiring | $0 | $0 | |
| 7 | Deployment + demo | $0 | $0 | Free tier hosting |

The original plan budgeted $40-80 in API spend assuming Anthropic Claude. Switching the LLM strategy to Groq + Ollama brought it to zero. The trade-off is documented in `EVAL_RESULTS.md` under the calibration section: Llama 3.3 70B does ~5-15% worse than Sonnet 4.5 on Tier 4 multi-hop reasoning. For a portfolio project that's a fair price for $0 ongoing cost.

## Per-query targets vs measured

Targets from the plan:

| Tier | p95 target | Reasoning |
|---|---|---|
| 1 | <3s | 8B path, single tool call |
| 2 | <8s | 70B with 1-2 tool calls |
| 3 | <15s | Multi-company XBRL + comparison |
| 4 | <25s | Full reflection loop |

Measured numbers land in `EVAL_RESULTS.md` after the final ablation run.

## Free-tier ceilings to design against

These limits change. Verify before each major eval sweep at https://console.groq.com/docs/rate-limits.

- Groq Llama 3.3 70B free: ~30 RPM, ~6,000 TPM input.
- Groq Llama 3.1 8B free: same RPM, larger TPM.
- Ollama: bounded by GPU. Qwen 2.5 7B Q5 hits ~60-80 tok/s on the 2080 Ti.

The eval runner reads RPM/TPM from response headers, maintains a rolling-window estimator, and sleeps between requests to stay 80% under the ceiling. On 429, it backs off exponentially up to 3 retries and then routes to Ollama.

## How costs are tracked

Langfuse logs every LLM call with token counts. The Groq/Ollama provider cost field is always $0. `backend/eval/metrics.py` aggregates per-query tokens and latency. The USD column in the eval table stays at $0 throughout.
