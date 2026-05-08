"""Tests for backend.api.streaming. StreamEvent and helper functions.

The `stream_graph_loop` async generator requires a live DB + LLM and is
covered by the agent graph tests (via run_graph_loop equivalence).
Here we test the pure-logic helpers: event construction, SSE encoding,
tool output summaries, and the /query/stream endpoint contract.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.api.streaming import (
    EventType,
    StreamEvent,
    _summarise_inputs,
    _summarise_output,
)


# ---------------------------------------------------------------------------
# StreamEvent
# ---------------------------------------------------------------------------


class TestStreamEvent:
    def test_construction(self):
        ev = StreamEvent(
            type=EventType.PLAN,
            data={"sub_tasks": []},
            trace_id="trace-1",
            iteration=0,
        )
        assert ev.type == EventType.PLAN
        assert ev.trace_id == "trace-1"
        assert ev.timestamp  # auto-filled

    def test_timestamp_auto_filled(self):
        ev = StreamEvent(type=EventType.DONE, data={})
        assert ev.timestamp != ""

    def test_sse_line_starts_with_data(self):
        ev = StreamEvent(type=EventType.TOOL_CALL, data={"tool": "calculator"})
        line = ev.to_sse_line()
        assert line.startswith("data: ")
        # Parse back
        payload = json.loads(line[6:])
        assert payload["type"] == "tool_call"
        assert payload["data"]["tool"] == "calculator"

    def test_round_trip_json(self):
        ev = StreamEvent(
            type=EventType.SYNTHESIS,
            data={"markdown": "# Answer\n\nRevenue was $96B."},
            trace_id="t",
            iteration=2,
        )
        ev2 = StreamEvent.model_validate_json(ev.model_dump_json())
        assert ev2.type == ev.type
        assert ev2.iteration == ev.iteration

    def test_all_event_types_are_valid(self):
        for t in EventType:
            ev = StreamEvent(type=t, data={})
            assert ev.type == t


# ---------------------------------------------------------------------------
# Input / output summarisers
# ---------------------------------------------------------------------------


class TestSummariseInputs:
    def test_long_string_truncated(self):
        inputs = {"query": "x" * 200}
        out = _summarise_inputs(inputs)
        assert len(out["query"]) <= 84  # 80 + "…"
        assert out["query"].endswith("…")

    def test_long_list_truncated(self):
        inputs = {"tickers": list("ABCDEFGH")}
        out = _summarise_inputs(inputs)
        assert len(out["tickers"]) <= 6  # 5 + ["…"]

    def test_short_values_unchanged(self):
        inputs = {"top_k": 10, "form": "10-K"}
        out = _summarise_inputs(inputs)
        assert out == inputs

    def test_empty_inputs(self):
        assert _summarise_inputs({}) == {}


class TestSummariseOutput:
    def test_xbrl_sql(self):
        out = _summarise_output("xbrl_sql", {"rows": [1, 2, 3], "canonical_concept": "revenue"})
        assert "3 XBRL rows" in out
        assert "revenue" in out

    def test_filing_retriever(self):
        out = _summarise_output("filing_retriever", {"n_results": 7})
        assert "7 chunks" in out

    def test_hybrid_retriever(self):
        out = _summarise_output("hybrid_retriever", {"n_results": 5})
        assert "5 chunks" in out

    def test_calculator(self):
        out = _summarise_output("calculator", {"result": 50.0, "formula": "(150-100)/100*100 = 50.0%"})
        assert "50.0" in out

    def test_filing_diff(self):
        out = _summarise_output("filing_diff", {"summary": "NVDA Item 1A: 2 additions"})
        assert "NVDA" in out or "additions" in out

    def test_unknown_tool(self):
        out = _summarise_output("unknown", {"value": 42})
        assert len(out) > 0

    def test_none_output(self):
        out = _summarise_output("xbrl_sql", None)
        assert out == "no output"


# ---------------------------------------------------------------------------
# /query/stream endpoint. FastAPI testclient (no live LLM)
# ---------------------------------------------------------------------------


def _make_mock_stream():
    """Return an async generator that yields a canned event sequence."""
    from backend.agent.schemas import Answer

    async def _gen(query: str, trace_id: str = ""):
        yield StreamEvent(
            type=EventType.PLAN,
            data={"query": query, "sub_tasks": [{"description": "d", "tool": "xbrl_sql"}], "is_refined": False},
            trace_id=trace_id,
        )
        yield StreamEvent(
            type=EventType.DONE,
            data={"answer": Answer(query=query, markdown="OK", claims=[], trace_id=trace_id, iterations=1, used_tools=[]).model_dump(), "iterations": 1, "trace_id": trace_id},
            trace_id=trace_id,
        )

    return _gen


def test_query_stream_endpoint_emits_sse(monkeypatch):
    """Smoke test: /query/stream returns SSE events when the generator is mocked."""
    from backend.api import streaming as st_mod
    from backend.api.main import app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(st_mod, "stream_graph_loop", _make_mock_stream())

    client = TestClient(app, raise_server_exceptions=True)
    with client.stream("POST", "/query/stream", json={"query": "What was NVDA revenue?"}) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        events = []
        for chunk in resp.iter_lines():
            if chunk.startswith("data: "):
                events.append(json.loads(chunk[6:]))
            if any(e.get("type") == "done" for e in events):
                break

    assert len(events) >= 2
    assert events[0]["type"] == "plan"
    assert events[-1]["type"] == "done"


def test_query_stream_empty_query_returns_400():
    from backend.api.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post("/query/stream", json={"query": "   "})
    assert resp.status_code == 400
