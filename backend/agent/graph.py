"""LangGraph state machine wiring all agent nodes together.

Topology
--------
                  ┌──────────┐
       query  ->   │  planner │
                  └────┬─────┘
                       ▼
                  ┌──────────┐
                  │  tools   │ <- refined plan (loop)
                  └────┬─────┘
                       ▼
                  ┌──────────┐
                  │ reflect1 │  (plan-completeness only)
                  └────┬─────┘
              fail/    │   pass
              loop     ▼
                  ┌──────────┐
                  │synthesize│
                  └────┬─────┘
                       ▼
                  ┌──────────┐
                  │ reflect2 │  (citations + numeric)
                  └────┬─────┘
              fail/    │   pass / cap reached
              loop     ▼
                       END

The graph runs at most 3 reflection iterations. Each iteration may include
a plan-execution pass and/or a synthesis pass. Hitting the cap means we
emit whatever answer we have plus the failed-checks list (for debugging).

If LangGraph is not installed, `build_graph()` raises ImportError. The
individual node functions are still importable and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from backend.agent.planner import plan_query
from backend.agent.reflector import MAX_ITERATIONS, reflect
from backend.agent.schemas import (
    Answer,
    Citation,
    Claim,
    Plan,
    ReflectionVerdict,
    ToolName,
)
from backend.agent.synthesizer import synthesize
from backend.agent.tool_schemas import (
    CalculatorInput,
    FilingDiffInput,
    FilingRetrieverInput,
    XBRLSQLInput,
)
from backend.agent.tools import (
    calculator_tool,
    filing_diff_tool,
    filing_retriever_tool,
    xbrl_sql_tool,
)
from backend.logging_config import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# State definition, flows through every node
# ---------------------------------------------------------------------------


class AgentState(TypedDict, total=False):
    query: str
    trace_id: str
    plan: Plan | None
    sub_task_results: list[dict[str, Any]]
    answer: Answer | None
    reflection: ReflectionVerdict | None
    iteration: int
    error: str | None


# ---------------------------------------------------------------------------
# Node implementations, pure async functions over AgentState
# ---------------------------------------------------------------------------


async def planner_node(state: AgentState) -> AgentState:
    """Generate or refine the plan."""
    query = state["query"]
    iteration = state.get("iteration", 0)

    # If a refined plan was emitted by the reflector, use it directly.
    refined = (state.get("reflection") or ReflectionVerdict(is_complete=False)).refined_plan
    if iteration > 0 and refined is not None:
        log.info("graph.planner.using_refined", iteration=iteration)
        return {**state, "plan": refined}

    plan = await plan_query(query)
    return {**state, "plan": plan, "iteration": iteration}


async def tools_node(state: AgentState) -> AgentState:
    """Execute every sub-task in the current plan, accumulating results."""
    plan = state["plan"]
    if plan is None:
        return {**state, "sub_task_results": [], "error": "no_plan"}

    results: list[dict[str, Any]] = []
    for i, task in enumerate(plan.sub_tasks):
        try:
            output = await _run_tool(task.intended_tool, task.intended_inputs)
            results.append({"tool": task.intended_tool.value, "output": output})
        except Exception as exc:
            log.error(
                "graph.tools.error",
                tool=task.intended_tool.value,
                index=i,
                error=str(exc),
            )
            results.append(
                {"tool": task.intended_tool.value, "error": str(exc), "output": None}
            )

    return {**state, "sub_task_results": results}


async def _run_tool(tool: ToolName, inputs: dict[str, Any]) -> Any:
    """Dispatch a single tool call by name. Returns Pydantic model dump-ready dict."""
    if tool == ToolName.CALCULATOR:
        result = calculator_tool(CalculatorInput.model_validate(inputs))
        return result.model_dump()

    if tool in (ToolName.FILING_RETRIEVER, ToolName.HYBRID_RETRIEVER):
        result = await filing_retriever_tool(FilingRetrieverInput.model_validate(inputs))
        return result.model_dump()

    if tool == ToolName.XBRL_SQL:
        result = await xbrl_sql_tool(XBRLSQLInput.model_validate(inputs))
        return result.model_dump()

    if tool == ToolName.FILING_DIFF:
        result = await filing_diff_tool(FilingDiffInput.model_validate(inputs))
        return result.model_dump()

    raise ValueError(f"Unsupported tool in graph: {tool}")


async def synthesizer_node(state: AgentState) -> AgentState:
    """Generate the final cited Answer from accumulated evidence."""
    plan = state["plan"]
    if plan is None:
        return {**state, "answer": None, "error": "no_plan_at_synth"}

    answer = await synthesize(
        query=state["query"],
        plan=plan,
        sub_task_results=state.get("sub_task_results", []),
        trace_id=state.get("trace_id", ""),
    )
    return {**state, "answer": answer}


async def reflector_node(state: AgentState) -> AgentState:
    """Run integrity checks and (if needed) emit a refined plan.

    The graph runs the reflector twice, once after tools (plan-completeness
    only, answer=None) and once after synthesis (full check). Same node, same
    code; the answer field gates which checks are active.
    """
    from backend.db.session import get_session

    iteration = state.get("iteration", 0)

    async with get_session() as session:
        verdict = await reflect(
            query=state["query"],
            plan=state["plan"],
            sub_task_results=state.get("sub_task_results", []),
            answer=state.get("answer"),
            iteration=iteration,
            session=session,
        )

    return {
        **state,
        "reflection": verdict,
        "iteration": iteration + 1,
    }


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------


def route_after_reflection(state: AgentState) -> str:
    """Decide the next step after the reflector runs.

    * If the reflector said complete -> 'synthesize' (if not yet synthesized)
      else 'end'.
    * If incomplete and we have a refined plan and iteration < MAX -> 'planner'.
    * If incomplete and at iteration cap -> 'synthesize' (best effort) or 'end'
      if we already have an answer.
    """
    verdict = state.get("reflection")
    iteration = state.get("iteration", 0)
    has_answer = state.get("answer") is not None

    if verdict and verdict.is_complete:
        return "end" if has_answer else "synthesize"

    if iteration >= MAX_ITERATIONS:
        return "end" if has_answer else "synthesize"

    if verdict and verdict.refined_plan is not None:
        return "planner"

    # Incomplete but no refined plan emitted -> proceed to synthesize with what we have.
    return "end" if has_answer else "synthesize"


# ---------------------------------------------------------------------------
# Graph builder (LangGraph)
# ---------------------------------------------------------------------------


def build_graph() -> Any:
    """Construct the compiled LangGraph state machine.

    Imports langgraph lazily so the module remains testable without it.
    """
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise ImportError(
            "langgraph is required to build the agent graph. "
            "Install via `uv sync` or `pip install langgraph`."
        ) from exc

    graph: Any = StateGraph(AgentState)
    graph.add_node("planner", planner_node)
    graph.add_node("tools", tools_node)
    graph.add_node("synthesize", synthesizer_node)
    graph.add_node("reflector", reflector_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "tools")
    graph.add_edge("tools", "reflector")

    graph.add_conditional_edges(
        "reflector",
        route_after_reflection,
        {
            "planner": "planner",
            "synthesize": "synthesize",
            "end": END,
        },
    )
    graph.add_edge("synthesize", "reflector")

    return graph.compile()


# ---------------------------------------------------------------------------
# Standalone runner, used by /query when langgraph isn't available
# ---------------------------------------------------------------------------


@dataclass
class GraphRunResult:
    answer: Answer | None
    state: AgentState


async def run_graph_loop(query: str, trace_id: str = "") -> GraphRunResult:
    """Plain-Python implementation of the graph for environments without langgraph.

    Behaviourally identical to `build_graph().ainvoke({"query": ...})`, but
    avoids the langgraph dependency. Useful for testing and as a fallback.
    """
    state: AgentState = {
        "query": query,
        "trace_id": trace_id,
        "iteration": 0,
        "sub_task_results": [],
    }

    while True:
        # planner
        state = await planner_node(state)

        # tools
        state = await tools_node(state)

        # post-tools reflection (plan completeness only)
        state = await reflector_node(state)
        next_step = route_after_reflection(state)

        if next_step == "planner":
            continue  # loop with refined plan

        # synthesize
        state = await synthesizer_node(state)

        # post-synth reflection (full check)
        state = await reflector_node(state)
        next_step = route_after_reflection(state)

        if next_step == "planner":
            # Reset answer so the next loop's reflector evaluates fresh evidence
            state["answer"] = None
            continue

        # 'end' or 'synthesize' (with existing answer) -> done
        break

    return GraphRunResult(answer=state.get("answer"), state=state)
