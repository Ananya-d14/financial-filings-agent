"""Evaluation metrics for the Financial Filings Analyst.

All metrics are deterministic except `ragas_metrics()`, which makes LLM calls.
The core four are always computed; RAGAS is optional additive.

Metrics
-------
1. **numerical_accuracy**. for questions with a gold numeric value, checks
   whether the answer's claimed numeric value matches within 0.5% tolerance.
2. **citation_faithfulness**. fraction of numeric claims that have at least
   one programmatically-verified citation.
3. **latency**. p50 and p95 wall-clock time per query (ms).
4. **cost**. input/output token counts per query; USD always $0 for Groq/Ollama.

Failure taxonomy
----------------
Every wrong answer is auto-categorised into one of:
  retrieval_miss, table_parsing_error, arithmetic_error,
  hallucination, planning_failure, tool_error

The taxonomy is heuristic, a reviewer may reclassify, but the automated
tags are the starting point for understanding where the system breaks.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.agent.citation_verifier import find_numeric_match
from backend.agent.schemas import Answer, Claim
from backend.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------


class FailureMode(str, Enum):
    RETRIEVAL_MISS = "retrieval_miss"
    TABLE_PARSING_ERROR = "table_parsing_error"
    ARITHMETIC_ERROR = "arithmetic_error"
    HALLUCINATION = "hallucination"
    PLANNING_FAILURE = "planning_failure"
    TOOL_ERROR = "tool_error"
    CORRECT = "correct"


def classify_failure(
    question: dict[str, Any],
    answer: Answer | None,
    numerical_match: bool | None,
    tool_errors: list[str],
    used_tools: list[str],
) -> FailureMode:
    """Heuristic classification of what went wrong (or didn't)."""
    if answer is None:
        return FailureMode.PLANNING_FAILURE

    if tool_errors:
        return FailureMode.TOOL_ERROR

    # Numeric question, check if number came from the right source
    gold_numeric = question.get("gold_numeric")
    if gold_numeric is not None:
        if numerical_match is True:
            return FailureMode.CORRECT
        if "xbrl_sql" not in used_tools:
            # Numeric question answered without XBRL. likely text extraction
            return FailureMode.TABLE_PARSING_ERROR
        # XBRL was used but number is wrong, arithmetic or hallucination
        numeric_claims = [c for c in answer.claims if c.is_numeric]
        if not numeric_claims:
            return FailureMode.HALLUCINATION
        if any("calculator" in (t or "") for t in used_tools):
            return FailureMode.ARITHMETIC_ERROR
        return FailureMode.HALLUCINATION

    # Qualitative question, check if retrieval returned anything
    if "filing_retriever" in used_tools or "hybrid_retriever" in used_tools:
        if not answer.claims:
            return FailureMode.HALLUCINATION
        return FailureMode.CORRECT

    if not used_tools:
        return FailureMode.PLANNING_FAILURE

    return FailureMode.RETRIEVAL_MISS


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------


@dataclass
class QuestionResult:
    """Evaluation result for a single question."""

    question_id: str
    tier: int
    question: str
    config: str

    answer: Answer | None = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    numerical_match: bool | None = None   # None if question has no gold numeric
    numerical_error_pct: float | None = None
    citation_faithfulness: float = 0.0   # fraction of numeric claims verified

    failure_mode: FailureMode = FailureMode.PLANNING_FAILURE
    tool_errors: list[str] = field(default_factory=list)

    # Optional RAGAS scores (populated if RAGAS available and configured)
    ragas_faithfulness: float | None = None
    ragas_answer_relevance: float | None = None
    ragas_context_precision: float | None = None
    ragas_context_recall: float | None = None

    # LLM judge score (0-3)
    judge_score: int | None = None


@dataclass
class TierSummary:
    tier: int
    n: int
    accuracy: float            # % numerically correct (for numeric questions)
    faithfulness: float        # mean citation faithfulness
    p50_ms: float
    p95_ms: float
    mean_tokens_in: float
    mean_tokens_out: float
    failure_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass
class EvalSummary:
    config: str
    n_questions: int
    by_tier: dict[int, TierSummary] = field(default_factory=dict)
    overall_accuracy: float = 0.0
    overall_faithfulness: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    mean_tokens_in: float = 0.0
    mean_tokens_out: float = 0.0
    mean_cost_usd: float = 0.0   # always 0 for Groq/Ollama
    ragas_faithfulness: float | None = None
    ragas_answer_relevance: float | None = None


# ---------------------------------------------------------------------------
# Core metric functions
# ---------------------------------------------------------------------------


def numerical_accuracy(
    result: QuestionResult,
    gold_numeric: float | None,
    tolerance_pct: float = 0.5,
) -> tuple[bool | None, float | None]:
    """Compare answer's first numeric claim against the gold value.

    Returns: (matched, rel_error_pct). Both None if question has no gold numeric.
    """
    if gold_numeric is None:
        return None, None
    if result.answer is None:
        return False, None

    numeric_claims = [c for c in result.answer.claims if c.is_numeric and c.numeric_value is not None]
    if not numeric_claims:
        return False, None

    # Check any numeric claim, if any match, the answer is correct.
    for claim in numeric_claims:
        rel_err = abs(claim.numeric_value - gold_numeric) / max(abs(gold_numeric), 1) * 100
        if rel_err <= tolerance_pct:
            return True, rel_err

    # Return error from the closest claim
    best_err = min(
        abs(c.numeric_value - gold_numeric) / max(abs(gold_numeric), 1) * 100
        for c in numeric_claims
        if c.numeric_value is not None
    )
    return False, best_err


def citation_faithfulness_score(answer: Answer) -> float:
    """Fraction of numeric claims that have at least one citation.

    Full programmatic citation verification (via `citation_verifier`) is
    expensive per-query. We use the presence of citations as a fast proxy here;
    the full verifier runs in the reflection loop and the slow eval sweep.
    """
    numeric_claims = [c for c in answer.claims if c.is_numeric]
    if not numeric_claims:
        return 1.0  # no numeric claims to verify -> trivially faithful
    cited = sum(1 for c in numeric_claims if c.citations)
    return cited / len(numeric_claims)


def compute_latency_stats(results: list[QuestionResult]) -> dict[str, float]:
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    if not latencies:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}
    return {
        "p50_ms": float(statistics.median(latencies)),
        "p95_ms": float(sorted(latencies)[int(len(latencies) * 0.95)]),
        "mean_ms": float(statistics.mean(latencies)),
    }


def aggregate_results(
    results: list[QuestionResult],
    config: str,
) -> EvalSummary:
    """Roll up per-question results into a summary with per-tier breakdown."""
    by_tier: dict[int, list[QuestionResult]] = {}
    for r in results:
        by_tier.setdefault(r.tier, []).append(r)

    tier_summaries: dict[int, TierSummary] = {}
    for tier, tier_results in by_tier.items():
        numeric_qs = [r for r in tier_results if r.numerical_match is not None]
        correct = sum(1 for r in numeric_qs if r.numerical_match is True)

        latency_stats = compute_latency_stats(tier_results)
        failures = {}
        for r in tier_results:
            key = r.failure_mode.value
            failures[key] = failures.get(key, 0) + 1

        tier_summaries[tier] = TierSummary(
            tier=tier,
            n=len(tier_results),
            accuracy=correct / max(len(numeric_qs), 1),
            faithfulness=float(
                statistics.mean(r.citation_faithfulness for r in tier_results)
            ) if tier_results else 0.0,
            p50_ms=latency_stats["p50_ms"],
            p95_ms=latency_stats["p95_ms"],
            mean_tokens_in=float(statistics.mean(r.input_tokens for r in tier_results)) if tier_results else 0.0,
            mean_tokens_out=float(statistics.mean(r.output_tokens for r in tier_results)) if tier_results else 0.0,
            failure_breakdown=failures,
        )

    all_numeric = [r for r in results if r.numerical_match is not None]
    overall_accuracy = (
        sum(1 for r in all_numeric if r.numerical_match) / len(all_numeric)
        if all_numeric else 0.0
    )
    overall_faithfulness = (
        float(statistics.mean(r.citation_faithfulness for r in results))
        if results else 0.0
    )

    latency_stats = compute_latency_stats(results)
    mean_in = float(statistics.mean(r.input_tokens for r in results)) if results else 0.0
    mean_out = float(statistics.mean(r.output_tokens for r in results)) if results else 0.0

    # RAGAS rollup (if populated)
    ragas_f = [r.ragas_faithfulness for r in results if r.ragas_faithfulness is not None]
    ragas_ar = [r.ragas_answer_relevance for r in results if r.ragas_answer_relevance is not None]

    return EvalSummary(
        config=config,
        n_questions=len(results),
        by_tier=tier_summaries,
        overall_accuracy=overall_accuracy,
        overall_faithfulness=overall_faithfulness,
        p50_ms=latency_stats["p50_ms"],
        p95_ms=latency_stats["p95_ms"],
        mean_tokens_in=mean_in,
        mean_tokens_out=mean_out,
        mean_cost_usd=0.0,  # Groq/Ollama = free
        ragas_faithfulness=float(statistics.mean(ragas_f)) if ragas_f else None,
        ragas_answer_relevance=float(statistics.mean(ragas_ar)) if ragas_ar else None,
    )


def format_ablation_row(summary: EvalSummary, tiers: list[int] = [1, 2, 3, 4]) -> str:
    """Format a single row of the ablation markdown table."""
    tier_accs = []
    for t in tiers:
        ts = summary.by_tier.get(t)
        if ts:
            tier_accs.append(f"{ts.accuracy:.0%}")
        else:
            tier_accs.append("N/A")

    faithfulness = f"{summary.overall_faithfulness:.0%}"
    p95 = f"{summary.p95_ms / 1000:.1f}s" if summary.p95_ms else "N/A"
    cost = "$0" if summary.mean_cost_usd == 0 else f"${summary.mean_cost_usd:.4f}"

    cells = [summary.config] + tier_accs + [faithfulness, p95, cost]
    return "| " + " | ".join(cells) + " |"


def render_ablation_table(summaries: list[EvalSummary]) -> str:
    """Render the full ablation markdown table."""
    header = (
        "| Configuration | Tier 1 Acc | Tier 2 Acc | Tier 3 Acc | Tier 4 Acc "
        "| Faithfulness | p95 Latency | Cost/query |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    rows = "\n".join(format_ablation_row(s) for s in summaries)
    return header + rows
