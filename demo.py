"""Quick offline sanity check.

Runs the deterministic layers (calculator, citation verifier, XBRL fetch)
without needing Docker or the LLM. Useful for "is anything obviously broken"
before touching the agent.

    python demo.py
"""

import json
import urllib.request


def _hr(label: str) -> None:
    print()
    print(f"# {label}")


def calculator():
    _hr("calculator")
    from backend.agent.calculator import calculate, CalculatorRequest, CalculatorOp

    cases = [
        ("NVDA YoY revenue FY22 -> FY24",
         CalculatorRequest(operation=CalculatorOp.YOY_GROWTH, operands=[60_922_000_000, 26_974_000_000])),
        ("AAPL FY23 gross margin",
         CalculatorRequest(operation=CalculatorOp.MARGIN, operands=[169_148_000_000, 383_285_000_000])),
        ("MSFT 3y CAGR",
         CalculatorRequest(operation=CalculatorOp.CAGR, operands=[143_015_000_000, 211_915_000_000], years=3)),
        ("Free expression",
         CalculatorRequest(operation=CalculatorOp.EXPRESSION, expression="(96.9 - 60.9) / 60.9 * 100")),
    ]
    for label, req in cases:
        r = calculate(req)
        print(f"  {label}: {r.formula}")


def citation_verifier():
    _hr("citation verifier / number extraction")
    from backend.agent.citation_verifier import extract_numbers, find_numeric_match

    samples = [
        "NVIDIA reported net revenue of $60,922 million for fiscal year 2024.",
        "Operating income was $(1.2) billion, reflecting restructuring charges.",
        "Gross margin improved to 74.6% driven by data center mix shift.",
        "Capital expenditures totaled $59.305 billion, a 14% increase YoY.",
    ]
    for s in samples:
        nums = extract_numbers(s)
        print(f"  {s[:70]}")
        for n in nums[:3]:
            tag = " (neg)" if n.is_negative else ""
            print(f"    {n.raw!r:25} = {n.value:,.0f}{tag}")

    matched, val, err = find_numeric_match(
        target=60_922_000_000,
        cited_text="NVIDIA reported net revenue of $60,922 million for fiscal year 2024.",
        tolerance_pct=0.5,
    )
    print(f"  match=$60.922B vs cited text: matched={matched} val={val:,.0f} err={err:.4f}%")


def sec_xbrl():
    _hr("live SEC XBRL fetch (NVDA)")
    from backend.ingestion.xbrl_parser import CONCEPT_ALIASES

    cik = "0001045810"
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    headers = {"User-Agent": "demo demo@example.com"}
    print(f"  GET {url}")
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"  network error: {exc}")
        return

    facts = data.get("facts", {}).get("us-gaap", {})
    revenue_entries = []
    for concept, canonical in CONCEPT_ALIASES.items():
        if canonical == "revenue" and concept in facts:
            entries = facts[concept].get("units", {}).get("USD", [])
            revenue_entries.extend(e for e in entries if e.get("form") == "10-K" and e.get("fy", 0) >= 2020)
            break

    if not revenue_entries:
        print("  no revenue rows matched")
        return
    revenue_entries.sort(key=lambda x: x.get("end", ""))
    seen = set()
    for e in revenue_entries:
        fy = e.get("fy")
        if fy in seen:
            continue
        seen.add(fy)
        print(f"  FY{fy}: ${e.get('val', 0)/1e9:.3f}B")


def benchmark():
    _hr("benchmark questions on disk")
    import os
    path = os.path.join(os.path.dirname(__file__), "backend", "eval", "benchmark_questions.jsonl")
    with open(path, encoding="utf-8") as f:
        questions = [json.loads(l) for l in f if l.strip()]
    for tier in (1, 2, 3, 4):
        rows = [q for q in questions if q["tier"] == tier]
        print(f"  tier {tier}: {len(rows)} questions")
        for q in rows[:2]:
            print(f"    - {q['question']}")


if __name__ == "__main__":
    calculator()
    citation_verifier()
    sec_xbrl()
    benchmark()
    print()
    print("done. for the full system see README.md.")
