# Build Notes

A running log of decisions, surprises, and things I changed my mind about while building this. Less polished than `ARCHITECTURE.md`, more honest.

## Why I picked this project

I wanted to learn agentic RAG for real, not just from a tutorial. SEC filings are an honest stress test: numbers actually have to be right, citations are checkable, and there are enough heterogeneity issues (different XBRL tags per company, varying section structures across filing types) to force decisions you can't gloss over.

A finance use case also forced me to take "correctness" seriously. If the system claimed Apple made $200B and it was actually $96B, that's not a "rough answer", that's broken. Setting up the calculator + citation verifier + reflection loop was a deliberate response to that constraint.

## Decisions I changed my mind on

**LangChain -> LangGraph.** Started with vanilla LangChain chains, hit a wall when I needed conditional routing for the reflection loop. LangGraph's state machine model fits this much better. I still kept a plain Python loop in `graph.py:run_graph_loop()` because tests against a real LangGraph compiled object are slow and I wanted unit tests to run fast.

**edgartools vs SEC API direct.** First version of the downloader used `edgartools`. Worked fine for one ticker, broke on JPM because of overflow files. Rewrote against SEC's submissions JSON directly. `edgartools` is in `pyproject.toml` for any future helper use but the core path is plain httpx.

**Anthropic -> Groq.** Original plan was Claude Sonnet 4.5 + Haiku 4.5 with prompt caching. About halfway through I decided I wanted this to be runnable by anyone without a paid API. Groq's free tier handles Llama 3.3 70B at 250 tok/s; the quality drop on Tier 4 is real but recoverable with better prompting.

**Langfuse v3 -> v2.** v3 wants Postgres + Clickhouse + Redis + MinIO. v2 needs one Postgres. For a portfolio project, v2 wins.

**react-markdown -> hand-written renderer.** Wrote a small `renderMarkdown()` in `AnswerView.tsx` because react-markdown was throwing hydration warnings during the streaming demo and I didn't want to debug it under time pressure. `react-markdown` is still in package.json so I can swap it back when I have an afternoon.

## Things I got wrong the first time

**Numeric extraction regex.** First version: `\d{1,3}(?:,\d{3})*`. Looked fine until a test failed on plain `1234` (4 digits, no comma). Fixed to `\d+(?:,\d{3})*`. Then `$(96.9) million` exposed that I was trying to handle the open paren and close paren inline in the regex; refactored to a context check on the surrounding 5 chars. The history is in `git log` for `citation_verifier.py`.

**Calculator AST eval.** Initially thought I could do `eval()` with restricted globals. Read enough about Python sandbox escapes to talk myself out of it. Rewrote as a recursive AST walker that whitelists ~10 specific node types and never calls eval. 100% test coverage and mutation testing on this file because it's the integrity backbone.

**Async + sync Qdrant client.** `qdrant-client` is sync. First attempt naively `await`ed it inside an async handler and was confused why hybrid retrieval was slow. Wrapped in `loop.run_in_executor()`. Better fix would be `AsyncQdrantClient` but that's a follow-up.

**8-K parsing.** I assumed 8-K would have the same `ITEM N` structure as 10-K. It doesn't; 8-K uses `Item N.NN` (e.g., `Item 2.02`). Built a separate regex pattern. There are still rough edges where 8-K body text doesn't have clear section boundaries; this is an open issue.

**Failure-mode classifier.** First version returned `RETRIEVAL_MISS` when the agent had no tool calls. The test caught that empty-tools should be `PLANNING_FAILURE` (the planner failed to emit a plan, not a retrieval miss). Updated.

## Things I'd do differently with more time

- Stream the synthesis token-by-token to the frontend instead of waiting for the JSON to complete and emitting it as one event.
- Wire `AsyncQdrantClient` properly so dense search doesn't need an executor.
- Build a real labeled set of 100 questions (currently I have 20 stubs, the full 100 will be added before the final eval run).
- Add a citation-text endpoint so the citation chip in the UI can show the actual quoted text from the filing on hover.
- Move XBRL concept aliases out of code into a YAML file for easier extension.
- A proper Postgres migration system. Currently `schema.sql` is applied on first container start; any change after that requires manual SQL.
- Persist embedded model weights as a Docker volume so the BGE download doesn't repeat on container rebuild.

## What I'm proud of

The reflection loop catching real failures, then the LLM proposing a refined plan, then the loop running again with that plan. Watching that work on a Tier 4 question for the first time was the best moment of the build. The whole thing rests on the idea that the LLM is good at proposing fixes when told what's wrong, and surprisingly that holds up well.

The 100% test coverage on the calculator and citation verifier. Mutation testing didn't find a single surviving mutant on the calculator; that file genuinely cannot regress without a test failure.

Total LLM cost across the entire build was zero dollars. That wasn't an accident, that was a design constraint.

## Open issues / known limitations

- Reranker latency under load. ~200ms on the 2080 Ti is fine for single-user dev but would need batching for production.
- Groq rate limits constrain eval sweep parallelism. Currently single-threaded with sleep gaps.
- The frontend doesn't gracefully handle a backend disconnect mid-stream. Needs an explicit error state and reconnect logic.
- Citation verifier does cosine similarity for narrative claims; this is OK but a fine-tuned NLI model would be better for "does this text support this claim" specifically.
