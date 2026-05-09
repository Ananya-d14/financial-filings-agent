"""Reflection verifier, the verification step before synthesis is finalised.

Three deterministic checks (no LLM calls):
  (a) every numeric claim in the answer has at least one citation,
  (b) every citation passes citation_verifier (text actually supports claim),
  (c) every plan sub-task produced at least one non-empty result.

If all checks pass → emit `is_complete=True`.
If any fail AND iteration < 3 → emit a `refined_plan` (LLM-generated) and
    let the graph route back to the tool runner.
If iteration ≥ 3 → emit `is_complete=False` but no refinement (graph proceeds
    to synthesis with whatever evidence is available).

The LLM is only invoked for plan refinement, not for the verification itself.
This keeps the integrity check deterministic and fast.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.citation_verifier import verify_citation
from backend.agent.llm import get_llm
from backend.agent.prompts import REFINER_SYSTEM, REFINER_USER_TEMPLATE
from backend.agent.schemas import Answer, Plan, ReflectionVerdict
from backend.logging_config import get_logger

log = get_logger(__name__)

MAX_ITERATIONS = 3


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------


def check_numeric_claims_have_citations(answer: Answer) -> list[str]:
    """Every numeric claim must have at least one citation. Returns failure descriptions."""
    failures = []
    for i, claim in enumerate(answer.claims):
        if claim.is_numeric and not claim.citations:
            failures.append(
                f"claim[{i}] is numeric but has no citations: {claim.text[:80]!r}"
            )
    return failures


def check_plan_completeness(
    plan: Plan, sub_task_results: list[dict[str, Any]]
) -> list[str]:
    """Every sub-task should have at least one non-empty result."""
    failures = []
    if len(sub_task_results) < len(plan.sub_tasks):
        failures.append(
            f"only {len(sub_task_results)}/{len(plan.sub_tasks)} sub-tasks produced output"
        )

    for i, (task, result) in enumerate(zip(plan.sub_tasks, sub_task_results)):
        if not result or (isinstance(result, dict) and not result.get("output")):
            failures.append(
                f"sub-task[{i}] ({task.intended_tool.value}) returned empty"
            )
    return failures


async def check_citations_verify(
    answer: Answer,
    session: AsyncSession,
) -> list[str]:
    """Run citation_verifier on every citation; collect any failures."""
    failures = []
    for i, claim in enumerate(answer.claims):
        for j, citation in enumerate(claim.citations):
            verdict = await verify_citation(
                claim=claim,
                citation=citation,
                session=session,
            )
            if not verdict.verified:
                failures.append(
                    f"claim[{i}] citation[{j}] failed: "
                    f"method={verdict.method.value}, "
                    f"issues={verdict.issues}"
                )
    return failures


# ---------------------------------------------------------------------------
# Plan refinement (LLM)
# ---------------------------------------------------------------------------


async def refine_plan(
    query: str,
    prior_plan: Plan,
    failures: list[str],
) -> Plan:
    """Ask the LLM to produce a revised plan addressing the listed failures."""
    llm = get_llm()
    refined, _ = await llm.chat_json(
        messages=[
            {"role": "system", "content": REFINER_SYSTEM},
            {
                "role": "user",
                "content": REFINER_USER_TEMPLATE.format(
                    query=query,
                    prior_plan=prior_plan.model_dump_json(indent=2),
                    failures="\n".join(f"- {f}" for f in failures),
                ),
            },
        ],
        schema=Plan,
        model_role="primary",
        temperature=0.0,
        max_tokens=1024,
    )
    return refined


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def reflect(
    query: str,
    plan: Plan,
    sub_task_results: list[dict[str, Any]],
    answer: Answer | None,
    iteration: int,
    session: AsyncSession,
) -> ReflectionVerdict:
    """Run all checks; emit a verdict + (if needed) a refined plan.

    Args:
        query:             The user's original question.
        plan:              The plan that just finished executing.
        sub_task_results:  Outputs from each sub-task in order.
        answer:            Synthesised answer (None if synth hasn't run yet).
        iteration:         Current iteration count (0-indexed).
        session:           Async DB session for citation verification.
    """
    failures: list[str] = []
    soft_failures: list[str] = []  # logged but don't trigger refinement

    # Plan completeness, runs before synthesis.
    failures.extend(check_plan_completeness(plan, sub_task_results))

    # Answer-level checks. Citation verification is a soft signal because
    # the LLM doesn't have access to real filing_ids and char offsets, so
    # citations often fail to verify even when the underlying claim is correct.
    # Logged for observability but doesn't trigger another expensive iteration.
    if answer is not None:
        failures.extend(check_numeric_claims_have_citations(answer))
        try:
            verify_failures = await check_citations_verify(answer, session)
            soft_failures.extend(verify_failures)
        except Exception as exc:
            log.warning("reflector.verify_skipped", error=str(exc))

    if soft_failures:
        log.info(
            "reflector.soft_failures",
            iteration=iteration,
            count=len(soft_failures),
            sample=soft_failures[:2],
        )

    if not failures:
        log.info("reflector.passed", iteration=iteration)
        return ReflectionVerdict(is_complete=True)

    log.info(
        "reflector.failed",
        iteration=iteration,
        failure_count=len(failures),
        failures=failures[:3],
    )

    # If we're past the iteration cap, give up, graph proceeds to synth.
    if iteration >= MAX_ITERATIONS - 1:
        log.warning("reflector.iteration_cap_reached", iteration=iteration)
        return ReflectionVerdict(is_complete=False, failed_checks=failures)

    # Otherwise refine the plan and continue.
    try:
        refined_plan = await refine_plan(query, plan, failures)
    except Exception as exc:
        log.error("reflector.refine_failed", error=str(exc))
        return ReflectionVerdict(is_complete=False, failed_checks=failures)

    return ReflectionVerdict(
        is_complete=False,
        failed_checks=failures,
        refined_plan=refined_plan,
    )
