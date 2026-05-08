"""Streaming agent graph with Server-Sent Events support.

The agent graph (`run_graph_loop`) runs fully then returns. This module
wraps it as an **async generator** that yields typed `StreamEvent` objects
as each node completes, so the frontend receives progressive updates:

  plan       → the planner's sub-task breakdown
  tool_call  → a tool is about to run (name + inputs preview)
  tool_result → a tool returned (output summary)
  reflection → the reflector ran (passed or failed + reason)
  synthesis  → the synthesizer finished (final Answer)
  done       → terminal event, echoes the Answer for easy handling

Each event carries `trace_id` and `iteration` so the client can correlate
events with the server logs and Langfuse traces.

Streaming vs non-streaming
--------------------------
`run_graph_loop()` (in graph.py) is the batch entry point used by tests and
the non-streaming `/query` endpoint. `stream_graph_loop()` here is the
streaming entry point used by `/query/stream`. Both call the same underlying
node functions; streaming adds zero latency to the non-streaming path.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel

from backend.agent.schemas import Answer, Plan, ReflectionVerdict
from backend.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    PLAN = "plan"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    REFLECTION = "reflection"
    SYNTHESIS = "synthesis"
    DONE = "done"
    ERROR = "error"


class StreamEvent(BaseModel):
    type: EventType
    data: dict[str, Any]
    trace_id: str = ""
    iteration: int = 0
    timestamp: str = ""

    def __init__(self, **kwargs: Any) -> None:
        if "timestamp" not in kwargs or not kwargs["timestamp"]:
            kwargs["timestamp"] = datetime.now(timezone.utc).isoformat()
        super().__init__(**kwargs)

    def to_sse_line(self) -> str:
        """Encode as a single SSE data line (no trailing newlines, caller adds them)."""
        return f"data: {self.model_dump_json()}"


# ---------------------------------------------------------------------------
# Streaming graph loop
# ---------------------------------------------------------------------------


async def stream_graph_loop(
    query: str,
    trace_id: str = "",
) -> AsyncGenerator[StreamEvent, None]:
    """Run the agent graph and yield StreamEvents as each node completes.

    Yields events in order:
      plan → (tool_call + tool_result)* → reflection* → synthesis → done

    On unhandled exception: yields an `error` event and stops.
    """
    from backend.agent.graph import _run_tool, planner_node, synthesizer_node
    from backend.agent.planner import plan_query
    from backend.agent.reflector import MAX_ITERATIONS, reflect
    from backend.agent.schemas import AgentState  # type: ignore[attr-defined]
    from backend.agent.synthesizer import synthesize
    from backend.db.session import get_session

    state: dict[str, Any] = {
        "query": query,
        "trace_id": trace_id,
        "iteration": 0,
        "sub_task_results": [],
    }

    try:
        iteration = 0

        while True:
            # -----------------------------------------------------------------
            # 1. Planner
            # -----------------------------------------------------------------
            refined = (state.get("reflection") or ReflectionVerdict(is_complete=False)).refined_plan
            if iteration > 0 and refined is not None:
                plan: Plan = refined
            else:
                plan = await plan_query(query)

            state["plan"] = plan

            yield StreamEvent(
                type=EventType.PLAN,
                data={
                    "query": query,
                    "sub_tasks": [
                        {
                            "description": t.description,
                            "tool": t.intended_tool.value,
                        }
                        for t in plan.sub_tasks
                    ],
                    "rationale": plan.rationale,
                    "is_refined": iteration > 0 and refined is not None,
                },
                trace_id=trace_id,
                iteration=iteration,
            )

            # -----------------------------------------------------------------
            # 2. Tools, one event per sub-task
            # -----------------------------------------------------------------
            sub_task_results: list[dict[str, Any]] = []

            for i, task in enumerate(plan.sub_tasks):
                yield StreamEvent(
                    type=EventType.TOOL_CALL,
                    data={
                        "index": i,
                        "tool": task.intended_tool.value,
                        "description": task.description,
                        "inputs_preview": _summarise_inputs(task.intended_inputs),
                    },
                    trace_id=trace_id,
                    iteration=iteration,
                )

                t_start = time.monotonic()
                try:
                    output = await _run_tool(task.intended_tool, task.intended_inputs)
                    latency_ms = int((time.monotonic() - t_start) * 1000)
                    sub_task_results.append({"tool": task.intended_tool.value, "output": output})

                    yield StreamEvent(
                        type=EventType.TOOL_RESULT,
                        data={
                            "index": i,
                            "tool": task.intended_tool.value,
                            "latency_ms": latency_ms,
                            "summary": _summarise_output(task.intended_tool.value, output),
                            "success": True,
                        },
                        trace_id=trace_id,
                        iteration=iteration,
                    )
                except Exception as exc:
                    latency_ms = int((time.monotonic() - t_start) * 1000)
                    sub_task_results.append(
                        {"tool": task.intended_tool.value, "error": str(exc), "output": None}
                    )
                    yield StreamEvent(
                        type=EventType.TOOL_RESULT,
                        data={
                            "index": i,
                            "tool": task.intended_tool.value,
                            "latency_ms": latency_ms,
                            "error": str(exc),
                            "success": False,
                        },
                        trace_id=trace_id,
                        iteration=iteration,
                    )

            state["sub_task_results"] = sub_task_results

            # -----------------------------------------------------------------
            # 3. Post-tools reflection (plan completeness only)
            # -----------------------------------------------------------------
            async with get_session() as session:
                pre_synth_verdict = await reflect(
                    query=query,
                    plan=plan,
                    sub_task_results=sub_task_results,
                    answer=None,
                    iteration=iteration,
                    session=session,
                )

            yield StreamEvent(
                type=EventType.REFLECTION,
                data={
                    "phase": "pre_synthesis",
                    "passed": pre_synth_verdict.is_complete,
                    "failures": pre_synth_verdict.failed_checks,
                    "will_refine": (
                        not pre_synth_verdict.is_complete
                        and pre_synth_verdict.refined_plan is not None
                    ),
                },
                trace_id=trace_id,
                iteration=iteration,
            )

            if not pre_synth_verdict.is_complete and pre_synth_verdict.refined_plan is not None:
                state["reflection"] = pre_synth_verdict
                iteration += 1
                state["iteration"] = iteration
                if iteration >= MAX_ITERATIONS:
                    pass  # fall through to synthesis
                else:
                    continue  # loop back to planner

            # -----------------------------------------------------------------
            # 4. Synthesis
            # -----------------------------------------------------------------
            answer: Answer = await synthesize(
                query=query,
                plan=plan,
                sub_task_results=sub_task_results,
                trace_id=trace_id,
            )
            state["answer"] = answer

            yield StreamEvent(
                type=EventType.SYNTHESIS,
                data={
                    "markdown": answer.markdown,
                    "n_claims": len(answer.claims),
                    "n_citations": sum(len(c.citations) for c in answer.claims),
                    "used_tools": [t.value for t in answer.used_tools],
                    "iterations": iteration + 1,
                },
                trace_id=trace_id,
                iteration=iteration,
            )

            # -----------------------------------------------------------------
            # 5. Post-synthesis reflection
            # -----------------------------------------------------------------
            async with get_session() as session:
                post_synth_verdict = await reflect(
                    query=query,
                    plan=plan,
                    sub_task_results=sub_task_results,
                    answer=answer,
                    iteration=iteration,
                    session=session,
                )

            yield StreamEvent(
                type=EventType.REFLECTION,
                data={
                    "phase": "post_synthesis",
                    "passed": post_synth_verdict.is_complete,
                    "failures": post_synth_verdict.failed_checks,
                    "will_refine": (
                        not post_synth_verdict.is_complete
                        and post_synth_verdict.refined_plan is not None
                        and iteration < MAX_ITERATIONS - 1
                    ),
                },
                trace_id=trace_id,
                iteration=iteration,
            )

            if (
                not post_synth_verdict.is_complete
                and post_synth_verdict.refined_plan is not None
                and iteration < MAX_ITERATIONS - 1
            ):
                state["reflection"] = post_synth_verdict
                state["answer"] = None  # reset for next iteration
                iteration += 1
                state["iteration"] = iteration
                continue

            # -----------------------------------------------------------------
            # 6. Done
            # -----------------------------------------------------------------
            yield StreamEvent(
                type=EventType.DONE,
                data={
                    "answer": answer.model_dump(),
                    "iterations": iteration + 1,
                    "trace_id": trace_id,
                },
                trace_id=trace_id,
                iteration=iteration,
            )
            break

    except Exception as exc:
        log.error("streaming.error", error=str(exc), trace_id=trace_id)
        yield StreamEvent(
            type=EventType.ERROR,
            data={"error": str(exc), "trace_id": trace_id},
            trace_id=trace_id,
        )


# ---------------------------------------------------------------------------
# Helpers, compact summaries for tool calls / results
# ---------------------------------------------------------------------------


def _summarise_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Return a small, UI-friendly preview of tool inputs."""
    preview: dict[str, Any] = {}
    for k, v in inputs.items():
        if isinstance(v, str) and len(v) > 80:
            preview[k] = v[:80] + "…"
        elif isinstance(v, list) and len(v) > 5:
            preview[k] = v[:5] + ["…"]
        else:
            preview[k] = v
    return preview


def _summarise_output(tool: str, output: Any) -> str:
    """Return a one-line summary of a tool result for the UI trace."""
    if output is None:
        return "no output"
    if tool == "xbrl_sql":
        n = len(output.get("rows", []))
        concept = output.get("canonical_concept", "")
        return f"{n} XBRL rows for '{concept}'"
    if tool in ("filing_retriever", "hybrid_retriever"):
        n = output.get("n_results", 0)
        return f"{n} chunks retrieved"
    if tool == "calculator":
        formula = output.get("formula", "")
        return formula[:100] if formula else "computed"
    if tool == "filing_diff":
        return output.get("summary", "diff complete")[:100]
    return str(output)[:100]
