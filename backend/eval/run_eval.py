"""Ablation eval CLI.

Usage
-----
    # Dev set (30 questions). fast iteration, ~$0
    uv run python -m backend.eval.run_eval --suite dev

    # Full set (all questions). slow, still $0 due to Groq free tier
    uv run python -m backend.eval.run_eval --suite full

    # Single config (for debugging)
    uv run python -m backend.eval.run_eval --suite dev --config "Vanilla RAG"

    # Include LLM judge
    uv run python -m backend.eval.run_eval --suite dev --judge

    # Rate-limit-friendly: add a gap between Groq calls
    uv run python -m backend.eval.run_eval --suite dev --gap-ms 2000

Outputs
-------
    - Console: live per-question log + final ablation table
    - EVAL_RESULTS.md: updated with new ablation rows
    - backend/eval/runs/{timestamp}.jsonl: raw per-question results

⚠  COST GATE: total LLM cost is $0 (Groq free tier). The constraint is
   wall-clock time. Full 100Q × 5-config sweep ≈ 2-4 hours with Groq
   rate limits. Use --suite dev for iteration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.eval.metrics import (
    EvalSummary,
    QuestionResult,
    aggregate_results,
    render_ablation_table,
)
from backend.eval.runner import ABLATION_CONFIGS, EvalConfig, evaluate_question
from backend.logging_config import configure_logging, get_logger

log = get_logger(__name__)

BENCHMARK_PATH = Path(__file__).parent / "benchmark_questions.jsonl"
RUNS_DIR = Path(__file__).parent / "runs"


# ---------------------------------------------------------------------------
# Benchmark loader
# ---------------------------------------------------------------------------


def load_benchmark(suite: str = "dev") -> list[dict[str, Any]]:
    """Load questions from benchmark_questions.jsonl.

    suite="dev"  → 30-question dev subset (first 30 rows)
    suite="full" → all rows
    """
    if not BENCHMARK_PATH.exists():
        raise FileNotFoundError(f"Benchmark not found: {BENCHMARK_PATH}")

    questions = []
    with open(BENCHMARK_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                questions.append(json.loads(line))

    if suite == "dev":
        # Take first 30 or all if fewer
        return questions[:30]
    return questions


# ---------------------------------------------------------------------------
# Per-config runner
# ---------------------------------------------------------------------------


async def run_config(
    questions: list[dict[str, Any]],
    config: EvalConfig,
    run_judge: bool = False,
    gap_ms: int = 0,
) -> list[QuestionResult]:
    """Evaluate all questions for one config, respecting rate-limit gaps."""
    results: list[QuestionResult] = []
    for i, q in enumerate(questions, 1):
        log.info(
            "eval.question",
            n=f"{i}/{len(questions)}",
            config=config.name,
            id=q["id"],
            tier=q["tier"],
        )
        result = await evaluate_question(q, config, run_judge=run_judge)
        results.append(result)

        if gap_ms > 0:
            await asyncio.sleep(gap_ms / 1000)

    return results


# ---------------------------------------------------------------------------
# Failure taxonomy report
# ---------------------------------------------------------------------------


def render_failure_report(all_results: list[QuestionResult]) -> str:
    from backend.eval.metrics import FailureMode

    counts: dict[str, int] = {}
    for r in all_results:
        k = r.failure_mode.value
        counts[k] = counts.get(k, 0) + 1

    total = len(all_results)
    lines = ["\n## Failure-mode taxonomy (final config)\n",
             "| Mode | Count | % |",
             "|---|---|---|"]
    for mode in FailureMode:
        c = counts.get(mode.value, 0)
        pct = c / total * 100 if total else 0
        lines.append(f"| {mode.value} | {c} | {pct:.1f}% |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------


def save_run_results(
    run_id: str,
    results: dict[str, list[QuestionResult]],
) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{run_id}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for config_name, config_results in results.items():
            for r in config_results:
                row: dict[str, Any] = {
                    "config": config_name,
                    "id": r.question_id,
                    "tier": r.tier,
                    "latency_ms": r.latency_ms,
                    "numerical_match": r.numerical_match,
                    "numerical_error_pct": r.numerical_error_pct,
                    "citation_faithfulness": r.citation_faithfulness,
                    "failure_mode": r.failure_mode.value,
                    "judge_score": r.judge_score,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "tool_errors": r.tool_errors,
                }
                fh.write(json.dumps(row) + "\n")
    return path


def update_eval_results_md(
    summaries: list[EvalSummary],
    failure_report: str,
    run_id: str,
    suite: str,
) -> None:
    """Rewrite the ablation table section in EVAL_RESULTS.md."""
    eval_results_path = Path(__file__).parent.parent.parent / "EVAL_RESULTS.md"
    if not eval_results_path.exists():
        return

    table_md = render_ablation_table(summaries)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    ablation_block = (
        f"\n## Ablation table\n\n"
        f"_Last updated: {timestamp}. suite={suite}, run={run_id}_\n\n"
        + table_md
        + "\n"
        + failure_report
        + "\n"
    )

    original = eval_results_path.read_text(encoding="utf-8")
    # Replace between ## Ablation table and the next ## heading
    import re
    replaced = re.sub(
        r"## Ablation table.*?(?=\n## |\Z)",
        ablation_block,
        original,
        flags=re.DOTALL,
    )
    eval_results_path.write_text(replaced, encoding="utf-8")
    log.info("eval.updated_eval_results_md")


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Financial Filings Analyst ablation eval.")
    parser.add_argument("--suite", choices=["dev", "full"], default="dev",
                        help="dev = 30 questions (default); full = all questions")
    parser.add_argument("--config", default="all",
                        help="Name of a specific config or 'all' (default: all)")
    parser.add_argument("--judge", action="store_true",
                        help="Run LLM-as-judge scoring (extra Groq calls)")
    parser.add_argument("--gap-ms", type=int, default=1500,
                        help="Millisecond gap between questions (Groq rate-limit relief, default 1500)")
    parser.add_argument("--update-md", action="store_true", default=True,
                        help="Update EVAL_RESULTS.md with new ablation table (default: True)")
    args = parser.parse_args()

    configure_logging(level="INFO", json_output=False)

    questions = load_benchmark(args.suite)
    log.info("eval.loaded", n=len(questions), suite=args.suite)

    configs = ABLATION_CONFIGS
    if args.config != "all":
        configs = [c for c in ABLATION_CONFIGS if c.name == args.config]
        if not configs:
            parser.error(f"Unknown config: {args.config!r}. "
                         f"Options: {[c.name for c in ABLATION_CONFIGS]}")

    run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    all_results: dict[str, list[QuestionResult]] = {}
    summaries: list[EvalSummary] = []

    for config in configs:
        print(f"\n{'=' * 60}")
        print(f"Config: {config.name}")
        print(f"{'=' * 60}")

        results = await run_config(
            questions=questions,
            config=config,
            run_judge=args.judge,
            gap_ms=args.gap_ms,
        )
        all_results[config.name] = results
        summary = aggregate_results(results, config.name)
        summaries.append(summary)

        # Per-config summary
        print(f"\n  Overall accuracy:    {summary.overall_accuracy:.1%}")
        print(f"  Overall faithfulness:{summary.overall_faithfulness:.1%}")
        print(f"  p50 latency:         {summary.p50_ms:.0f}ms")
        print(f"  p95 latency:         {summary.p95_ms:.0f}ms")
        for tier, ts in sorted(summary.by_tier.items()):
            print(f"  Tier {tier} accuracy:   {ts.accuracy:.1%} ({ts.n} questions)")

    # Final ablation table
    print(f"\n\n{'=' * 80}")
    print("ABLATION TABLE")
    print("=" * 80)
    print(render_ablation_table(summaries))

    # Failure taxonomy on the final (best) config
    final_results = list(all_results.values())[-1] if all_results else []
    failure_report = render_failure_report(final_results)
    print(failure_report)

    # Persist
    saved_path = save_run_results(run_id, all_results)
    print(f"\nRaw results saved: {saved_path}")

    if args.update_md and summaries:
        update_eval_results_md(summaries, failure_report, run_id, args.suite)


if __name__ == "__main__":
    asyncio.run(main())
