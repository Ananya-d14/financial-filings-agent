"""Phase 0 smoke tests: package imports, settings load, ticker universe is correct.

These tests must always pass on a fresh checkout, they're the CI canary.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


EXPECTED_TICKERS = {
    "MSFT", "AAPL", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    "AMD", "INTC", "CRM", "ORCL",
    "JPM", "BAC",
    "WMT", "COST",
    "JNJ", "PFE", "LLY",
    "CAT", "XOM",
}
EXPECTED_FISCAL_YEARS = {2020, 2021, 2022, 2023, 2024}


def test_package_imports() -> None:
    import backend  # noqa: F401
    import backend.agent  # noqa: F401
    import backend.api  # noqa: F401
    import backend.eval  # noqa: F401
    import backend.indexing  # noqa: F401
    import backend.ingestion  # noqa: F401
    import backend.retrieval  # noqa: F401


def test_settings_load_with_defaults() -> None:
    from backend.config import get_settings

    s = get_settings()
    assert set(s.ticker_list) == EXPECTED_TICKERS
    assert set(s.fiscal_year_list) == EXPECTED_FISCAL_YEARS
    assert s.embedding_model == "BAAI/bge-large-en-v1.5"
    assert s.reranker_model == "BAAI/bge-reranker-large"
    # Free-tier LLM strategy: Groq primary + Ollama fallback, no Anthropic.
    assert s.llm_primary_provider == "groq"
    assert s.llm_fallback_provider == "ollama"
    assert s.groq_model_primary == "llama-3.3-70b-versatile"
    assert s.ollama_model == "qwen2.5:7b-instruct-q5_K_M"


def test_sec_user_agent_guard_raises_on_default() -> None:
    """The placeholder SEC_USER_AGENT must trip the guard before any EDGAR request."""
    import pytest

    from backend.config import Settings

    s = Settings(sec_user_agent="REPLACE_ME placeholder")
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
        s.assert_sec_user_agent_set()


def test_health_endpoint() -> None:
    from backend.api.main import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_tickers_endpoint() -> None:
    from backend.api.main import app

    client = TestClient(app)
    resp = client.get("/tickers")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["tickers"]) == EXPECTED_TICKERS
    assert set(body["fiscal_years"]) == EXPECTED_FISCAL_YEARS


def test_query_endpoint_returns_501_in_phase_0() -> None:
    """Phase 0 contract: /query is wired but returns 501 until Phase 4."""
    from backend.api.main import app

    client = TestClient(app)
    resp = client.post("/query", json={"query": "test"})
    assert resp.status_code == 501


def test_trace_id_header_propagated() -> None:
    from backend.api.main import app

    client = TestClient(app)
    resp = client.get("/health")
    assert "x-trace-id" in {k.lower() for k in resp.headers.keys()}


def test_trace_id_header_respected_when_provided() -> None:
    """If the client supplies an X-Trace-Id, the server should echo it back."""
    from backend.api.main import app

    client = TestClient(app)
    provided = "11111111-2222-3333-4444-555555555555"
    resp = client.get("/health", headers={"X-Trace-Id": provided})
    assert resp.headers.get("X-Trace-Id") == provided


def test_agent_schemas_roundtrip() -> None:
    from backend.agent.schemas import Citation, Claim, Plan, SubTask, ToolName

    cit = Citation(
        filing_id="11111111-1111-1111-1111-111111111111",
        accession_number="0000320193-23-000106",
        ticker="AAPL",
        form="10-K",
        fiscal_year=2023,
        section="Item 7",
        char_offset_start=1000,
        char_offset_end=1100,
    )
    claim = Claim(text="Apple reported $96.995B net income.", is_numeric=True, numeric_value=96.995e9, numeric_unit="USD", citations=[cit])
    plan = Plan(query="x", sub_tasks=[SubTask(description="d", intended_tool=ToolName.XBRL_SQL)])

    # Round-trip through JSON to make sure all fields serialise.
    assert Claim.model_validate_json(claim.model_dump_json()) == claim
    assert Plan.model_validate_json(plan.model_dump_json()) == plan
