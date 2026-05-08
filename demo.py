"""
Quick demo of the Financial Filings Analyst.
Runs without Docker/DB/GPU, shows the deterministic layers working live.
"""

import asyncio
import json
import urllib.request

SEP = "=" * 60


def demo_calculator():
    print(f"\n{SEP}")
    print("DEMO 1: Calculator Tool (deterministic, no LLM)")
    print(SEP)
    from backend.agent.calculator import calculate, CalculatorRequest, CalculatorOp

    examples = [
        ("NVDA YoY revenue growth FY22 to FY24",
         CalculatorRequest(operation=CalculatorOp.YOY_GROWTH, operands=[60_922_000_000, 26_974_000_000])),
        ("AAPL FY2023 gross margin",
         CalculatorRequest(operation=CalculatorOp.MARGIN, operands=[169_148_000_000, 383_285_000_000])),
        ("MSFT CAGR FY2020 to FY2023 revenue",
         CalculatorRequest(operation=CalculatorOp.CAGR, operands=[143_015_000_000, 211_915_000_000], years=3)),
        ("Safe expression: (96.9 - 60.9) / 60.9 * 100",
         CalculatorRequest(operation=CalculatorOp.EXPRESSION, expression="(96.9 - 60.9) / 60.9 * 100")),
    ]

    for label, req in examples:
        result = calculate(req)
        print(f"  {label}")
        print(f"  = {result.formula}")
        print()


def demo_citation_verifier():
    print(f"\n{SEP}")
    print("DEMO 2: Citation Verifier, Number Extraction")
    print(SEP)
    from backend.agent.citation_verifier import extract_numbers, find_numeric_match

    samples = [
        "NVIDIA reported net revenue of $60,922 million for fiscal year 2024.",
        "Operating income was $(1.2) billion, reflecting restructuring charges.",
        "Gross margin improved to 74.6% driven by data center mix shift.",
        "Capital expenditures totaled $59.305 billion, a 14% increase YoY.",
    ]

    print("  Number extraction from filing text:")
    for text in samples:
        nums = extract_numbers(text)
        print(f"\n  Input: {text[:70]}…" if len(text) > 70 else f"\n  Input: {text}")
        for n in nums[:3]:
            print(f"     {n.raw!r:25} = {n.value:,.0f}  {'(negative)' if n.is_negative else ''}")

    # Verify match
    print("\n  Matching NVDA claim ($60.922B) against cited text:")
    matched, val, err = find_numeric_match(
        target=60_922_000_000,
        cited_text="NVIDIA reported net revenue of $60,922 million for fiscal year 2024.",
        tolerance_pct=0.5,
    )
    print(f"  Matched: {matched}, value: {val:,.0f}, error: {err:.6f}%")


def demo_sec_xbrl():
    print(f"\n{SEP}")
    print("DEMO 3: Live SEC EDGAR XBRL Fetch, NVDA Revenue")
    print(SEP)
    from backend.ingestion.xbrl_parser import CONCEPT_ALIASES

    cik = "0001045810"  # NVDA CIK
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    headers = {"User-Agent": "Demo demo@example.com"}

    print(f"  Fetching: {url}")
    print("  (This is a real SEC API call, no key required)")

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        # Pull revenue concept
        facts = data.get("facts", {}).get("us-gaap", {})
        revenue_entries = []
        for concept, canonical in CONCEPT_ALIASES.items():
            if canonical == "revenue" and concept in facts:
                entries = facts[concept].get("units", {}).get("USD", [])
                annual = [e for e in entries if e.get("form") == "10-K" and e.get("fy", 0) >= 2020]
                revenue_entries.extend(annual)
                break

        if revenue_entries:
            revenue_entries.sort(key=lambda x: x.get("end", ""))
            print(f"\n  NVDA Revenue from SEC XBRL (10-K, FY2020+):")
            seen = set()
            for e in revenue_entries:
                fy = e.get("fy")
                if fy in seen:
                    continue
                seen.add(fy)
                val = e.get("val", 0)
                print(f"  FY{fy}: ${val/1e9:.3f}B")
        else:
            print("  (No revenue data found, try running after ingestion)")
    except Exception as exc:
        print(f"  [network error: {exc}]")


def demo_streaming_events():
    print(f"\n{SEP}")
    print("DEMO 4: Streaming Event Structure (no LLM needed)")
    print(SEP)
    from backend.api.streaming import EventType, StreamEvent, _summarise_output

    events = [
        StreamEvent(type=EventType.PLAN, data={
            "query": "What was NVDA's FY2024 revenue?",
            "sub_tasks": [{"description": "Fetch NVDA revenue from XBRL", "tool": "xbrl_sql"}],
            "rationale": "Single-fact XBRL lookup",
            "is_refined": False,
        }, trace_id="demo-trace-001", iteration=0),
        StreamEvent(type=EventType.TOOL_CALL, data={
            "index": 0, "tool": "xbrl_sql",
            "description": "Fetch NVDA revenue from XBRL",
            "inputs_preview": {"canonical_concept": "revenue", "tickers": ["NVDA"], "fiscal_years": [2024]},
        }, trace_id="demo-trace-001", iteration=0),
        StreamEvent(type=EventType.TOOL_RESULT, data={
            "index": 0, "tool": "xbrl_sql",
            "latency_ms": 42, "success": True,
            "summary": _summarise_output("xbrl_sql", {"rows": [{"ticker":"NVDA","value":60922000000}], "canonical_concept": "revenue"}),
        }, trace_id="demo-trace-001", iteration=0),
        StreamEvent(type=EventType.REFLECTION, data={
            "phase": "pre_synthesis", "passed": True,
            "failures": [], "will_refine": False,
        }, trace_id="demo-trace-001", iteration=0),
        StreamEvent(type=EventType.SYNTHESIS, data={
            "markdown": "NVIDIA reported FY2024 revenue of **$60.922 billion**[^1].",
            "n_claims": 1, "n_citations": 1,
            "used_tools": ["xbrl_sql"], "iterations": 1,
        }, trace_id="demo-trace-001", iteration=0),
    ]

    print("  These are the events the frontend receives via SSE stream:\n")
    for ev in events:
        sse_line = ev.to_sse_line()
        print(f"  event: {ev.type.value}")
        # Print key fields only
        payload = json.loads(sse_line[6:])
        for k, v in payload["data"].items():
            val_str = str(v)[:80]
            print(f"    {k}: {val_str}")
        print()


def demo_benchmark():
    print(f"\n{SEP}")
    print("DEMO 5: Benchmark Questions (all 4 tiers)")
    print(SEP)
    import os
    path = os.path.join(os.path.dirname(__file__), "backend", "eval", "benchmark_questions.jsonl")
    with open(path, encoding="utf-8") as f:
        questions = [json.loads(l) for l in f if l.strip()]

    for tier in [1, 2, 3, 4]:
        tier_qs = [q for q in questions if q["tier"] == tier]
        print(f"\n  Tier {tier} ({len(tier_qs)} questions):")
        for q in tier_qs[:2]:
            print(f"  • {q['question']}")
            if q.get("gold_numeric"):
                print(f"    Gold: {q.get('gold_answer', 'N/A')}")
        if len(tier_qs) > 2:
            print(f"    … +{len(tier_qs) - 2} more")


if __name__ == "__main__":
    demo_calculator()
    demo_citation_verifier()
    demo_sec_xbrl()
    demo_streaming_events()
    demo_benchmark()
    print(f"\n{SEP}")
    print("Demo complete.")
    print(SEP)
    print("""
To run the FULL system:
  1. cp .env.example .env          # fill in GROQ_API_KEY + SEC_USER_AGENT
  2. docker compose up -d          # starts Postgres + Qdrant + Langfuse
  3. uv sync                       # install Python deps
  4. ollama pull qwen2.5:7b-instruct-q5_K_M  # download local fallback model
  5. uv run python -m backend.ingestion.run --tickers NVDA --years 2024
  6. uv run python -m backend.indexing.embed_corpus
  7. uv run python -m backend.indexing.build_indexes
  8. uv run uvicorn backend.api.main:app --reload   # backend on :8000
  9. cd frontend && pnpm install && pnpm dev        # UI on :3000
""")
