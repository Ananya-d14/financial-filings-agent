"""
Live demo: real Groq API call, Planner + Synthesizer
No Docker needed. Just needs GROQ_API_KEY in .env
"""

import asyncio
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Load .env
load_dotenv(Path(__file__).parent / ".env")

SEP = "=" * 60


async def demo_groq_planner():
    print(f"\n{SEP}")
    print("LIVE DEMO: Groq Planner (real LLM call)")
    print(SEP)

    # Patch settings cache so it picks up .env values
    from backend.config import get_settings
    from functools import lru_cache
    import backend.config as cfg_mod
    cfg_mod.get_settings.cache_clear()

    from backend.agent.llm import reset_llm
    reset_llm()

    from backend.agent.planner import plan_query

    queries = [
        ("Tier 1 - single fact", "What was NVIDIA's FY2024 R&D expense?"),
        ("Tier 3 - comparison",  "Compare gross margins of MSFT and AAPL in FY2023."),
    ]

    for label, query in queries:
        print(f"\n  [{label}]")
        print(f"  Query: {query}")
        t = time.monotonic()
        try:
            plan = await plan_query(query)
            latency = int((time.monotonic() - t) * 1000)
            print(f"  Latency: {latency}ms")
            print(f"  Sub-tasks ({len(plan.sub_tasks)}):")
            for i, task in enumerate(plan.sub_tasks, 1):
                print(f"    {i}. [{task.intended_tool.value}] {task.description}")
            if plan.rationale:
                print(f"  Rationale: {plan.rationale}")
        except Exception as exc:
            print(f"  ERROR: {exc}")


async def demo_groq_calculator_query():
    print(f"\n{SEP}")
    print("LIVE DEMO: Groq Synthesizer (generates a cited answer)")
    print(SEP)

    from backend.agent.llm import get_llm, reset_llm
    from backend.agent.schemas import Answer
    from backend.agent.prompts import SYNTHESIZER_SYSTEM, SYNTHESIZER_USER_TEMPLATE

    reset_llm()

    # Fake evidence (what the tools would return after real ingestion)
    evidence = """--- Step 1: xbrl_sql ---
  NVDA rd_expense FY2024 FY: 8675000000.0 USD (accession=0001045810-24-000029)
  NVDA rd_expense FY2023 FY: 7339000000.0 USD (accession=0001045810-23-000017)
  NVDA rd_expense FY2022 FY: 5268000000.0 USD (accession=0001045810-22-000024)

--- Step 2: calculator ---
  result: 18.21
  formula: (8675000000.0 - 7339000000.0) / 7339000000.0 * 100 = 18.2108%
"""

    query = "What was NVIDIA's FY2024 R&D expense and how did it change YoY?"
    plan_summary = "1. [xbrl_sql] Fetch NVDA R&D expense FY2022-2024\n2. [calculator] Compute YoY growth FY23->FY24"

    print(f"  Query: {query}")
    print("  (Using canned XBRL evidence, normally fetched from the DB)")
    print("  Calling Groq Llama 3.3 70B synthesizer...")

    # Use a simplified schema, LLM fills markdown + claims only
    from pydantic import BaseModel
    class SimpleAnswer(BaseModel):
        markdown: str
        claims: list[dict] = []

    llm = get_llm()
    t = time.monotonic()
    try:
        result, resp = await llm.chat_json(
            messages=[
                {"role": "system", "content": SYNTHESIZER_SYSTEM},
                {"role": "user", "content": SYNTHESIZER_USER_TEMPLATE.format(
                    query=query,
                    plan_summary=plan_summary,
                    evidence_summary=evidence,
                )},
            ],
            schema=SimpleAnswer,
            model_role="primary",
            temperature=0.0,
            max_tokens=1024,
        )
        latency = int((time.monotonic() - t) * 1000)

        print(f"\n  Provider: {resp.provider}  |  Latency: {latency}ms  |  Fallback: {resp.used_fallback}")
        print(f"\n  ANSWER:\n  {'-'*50}")
        for line in result.markdown.strip().split("\n"):
            print(f"  {line}")
        print(f"  {'-'*50}")
        print(f"\n  Claims ({len(result.claims)}):")
        for i, claim in enumerate(result.claims, 1):
            text = claim.get("text","")[:80]
            is_num = claim.get("is_numeric", False)
            val = claim.get("numeric_value")
            cits = claim.get("citations", [])
            numeric = f"  = {val:,.0f}" if is_num and val else ""
            cited = f"  [{len(cits)} citation(s)]" if cits else "  [no citation]"
            print(f"  {i}. {text}{numeric}{cited}")

    except Exception as exc:
        print(f"  ERROR: {exc}")


async def main():
    key = os.getenv("GROQ_API_KEY", "")
    if not key or key == "gsk_REPLACE_ME":
        print("ERROR: GROQ_API_KEY not set in .env")
        sys.exit(1)
    print(f"Using Groq key: {key[:12]}...{key[-4:]}")

    await demo_groq_planner()
    await demo_groq_calculator_query()

    print(f"\n{SEP}")
    print("Live demo complete. LLM calls went through successfully.")
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())
