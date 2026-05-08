# Project Overview (plain-English version)

This is the non-technical version. For the engineering details see `ARCHITECTURE.md`.

## The problem

Every public US company files annual and quarterly reports with the SEC. They are big documents, an average 10-K is 80,000 words, full of tables, footnotes, and dense legal language. Reading and comparing them is the day job for thousands of equity analysts, and it eats most of their time.

A typical question like "how have Microsoft, Google, and Apple gross margins changed since 2020?" requires:
1. Locating each company's last 5 annual reports.
2. Finding the gross profit and revenue lines (different companies report them differently).
3. Doing the arithmetic.
4. Cross-checking the citations.
5. Building a comparable view.

That is half a day for a person. Software can do it in seconds, if it's careful enough not to invent numbers.

## What this project does

A web app where you type a question and get back a cited, accurate answer.

- "What was NVIDIA's FY2024 R&D expense?" — single fact lookup.
- "Summarise Tesla's 2024 China-related risk factors" — qualitative.
- "Compare gross margins of MSFT, GOOGL, AAPL 2020 to 2024" — multi-company table.
- "Which mega-cap tech firms grew capex faster than revenue and why?" — multi-step reasoning.

Every numeric claim in the answer has a citation that links back to the exact paragraph and character offsets in the original SEC filing. The arithmetic is done by a deterministic Python calculator, not the language model. Language models are notoriously bad at maths and worse at admitting it.

## Why this is not "ChatGPT with PDFs"

| Off-the-shelf chatbot | This system |
|---|---|
| Reads filings as text and guesses at numbers | Pulls structured XBRL data from SEC's machine-readable feed |
| Sometimes invents citations | Every citation is verified against the source before display |
| LLM does the maths (and gets it wrong) | A separate calculator does all arithmetic |
| Returns prose | Returns a structured `Answer` object: markdown body + machine-readable claims with citations |
| One pass | Reflection loop catches missing citations and refines the plan |
| Works on one document | Searches 400 filings simultaneously with hybrid BM25 + vector retrieval |

## Why I built it

To learn how to put a real RAG system together end-to-end. Most public RAG examples are toy notebooks; very few discuss the engineering details that matter once you ingest more than ten documents: XBRL tag heterogeneity, section-aware chunking, citation verification, evaluation harnesses with actual ablation tables. I wanted something I could point at when an interviewer asked what I have actually shipped.

## Scope

- 20 hand-picked S&P 500 tickers across tech, finance, consumer, healthcare, industrials, energy.
- Fiscal years 2020 to 2024.
- Filing types: 10-K (annual), 10-Q (quarterly), 8-K (current events).
- Roughly 400 filings total.

Things explicitly out of scope: international filings, IFRS, real-time alerting, earnings call transcripts, multi-user accounts.

## What it costs

Nothing. The LLM (Llama 3.3 70B) runs on Groq's free tier; the local fallback (Qwen 2.5 7B) runs on my home GPU; embeddings and reranking are local. The whole thing was built and runs without a single paid API call.

## Who would use it

Equity analysts, portfolio managers, financial journalists. Anyone whose job involves reading SEC filings under deadline pressure.

## Status

Phases 0 to 6 complete: ingestion, indexing, hybrid retrieval, agent graph, evaluation harness, streaming UI. Phase 7 (deployment + final docs + demo video) in progress.
