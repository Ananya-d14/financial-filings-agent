"""Planner agent, decompose a user query into a typed Plan.

The planner is the first node in the LangGraph state machine. It takes the
user's natural-language question and emits a `Plan` containing 1-4 typed
`SubTask` instances, each routed to a specific tool.

Routing strategy
----------------
* Tier-1 single-fact questions ("What was NVDA's FY2024 revenue?") are
  routed to the cheap model (Llama 3.1 8B). The heuristic for "Tier-1":
  one ticker, one year, one canonical concept, no comparison/multi-step.
* Everything else (Tier-2/3/4) goes through the primary model (Llama 3.3 70B).

The heuristic is conservative, when in doubt, route to primary. False
negatives (Tier-1 routed to primary) cost a bit of latency; false positives
(Tier-3 routed to cheap) cost accuracy, which is worse.
"""

from __future__ import annotations

import re

from backend.agent.llm import get_llm
from backend.agent.prompts import PLANNER_SYSTEM, PLANNER_USER_TEMPLATE
from backend.agent.schemas import Plan, SubTask, ToolName
from backend.config import get_settings
from backend.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tier-1 detector, returns True if the query looks like a single-fact lookup.
# ---------------------------------------------------------------------------


_SINGLE_FACT_PATTERNS = [
    r"\bwhat\s+was\b",
    r"\bhow\s+much\s+did\b",
    r"\breport(?:ed)?\b",
]

_COMPARISON_PATTERNS = [
    r"\bcompare\b",
    r"\bdifference\b",
    r"\bversus\b|\bvs\.?\b",
    r"\bbetween\b",
    r"\bwhich\b.*\b(?:grew|grow|fastest|highest|lowest)\b",
    r"\bover\s+(?:the\s+)?(?:past|last)\s+\d+\s+years?\b",
    r"\bcagr\b|\bcompound\b",
    r"\bgrowth\b",
]


def _is_likely_tier_1(query: str) -> bool:
    """Heuristic: looks like a single-fact lookup → cheap model."""
    q = query.lower()
    if any(re.search(p, q) for p in _COMPARISON_PATTERNS):
        return False
    # Has at most ONE of our locked tickers mentioned
    settings = get_settings()
    tickers_mentioned = sum(1 for t in settings.ticker_list if re.search(rf"\b{t}\b", query))
    if tickers_mentioned > 1:
        return False
    # Looks like "what was X's Y". short, factual
    if any(re.search(p, q) for p in _SINGLE_FACT_PATTERNS) and len(query.split()) < 16:
        return True
    return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def plan_query(query: str) -> Plan:
    """Generate a Plan for the given user query."""
    role = "cheap" if _is_likely_tier_1(query) else "primary"
    log.info("planner.routing", query=query[:100], role=role)

    llm = get_llm()
    plan, resp = await llm.chat_json(
        messages=[
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": PLANNER_USER_TEMPLATE.format(query=query)},
        ],
        schema=Plan,
        model_role=role,
        temperature=0.0,
        max_tokens=1024,
    )

    log.info(
        "planner.plan_emitted",
        sub_tasks=len(plan.sub_tasks),
        tools=[t.intended_tool.value for t in plan.sub_tasks],
        provider=resp.provider,
        used_fallback=resp.used_fallback,
    )

    # Defensive: if the model emitted no sub-tasks, fall back to a generic
    # filing_retriever call so the loop doesn't stall.
    if not plan.sub_tasks:
        log.warning("planner.empty_plan_fallback", query=query[:100])
        plan = Plan(
            query=query,
            sub_tasks=[
                SubTask(
                    description=f"Retrieve filing chunks relevant to: {query}",
                    intended_tool=ToolName.FILING_RETRIEVER,
                    intended_inputs={"query": query, "top_k": 10},
                )
            ],
            rationale="empty plan from model; fell back to single retrieval",
        )

    return plan
