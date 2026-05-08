"""Unit tests for sql_retriever helpers (no DB, no network)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.retrieval.sql_retriever import XbrlFactRow, rows_to_comparison_table


def _make_row(ticker: str, year: int, value: float) -> XbrlFactRow:
    return XbrlFactRow(
        ticker=ticker,
        cik=123456,
        canonical_concept="revenue",
        fiscal_year=year,
        fiscal_period="FY",
        form="10-K",
        period_start=date(year - 1, 2, 1),
        period_end=date(year, 1, 31),
        value=Decimal(str(value)),
        unit="USD",
        accession_number="0000123456-24-000001",
    )


def test_rows_to_comparison_table_basic():
    rows = [_make_row("MSFT", 2023, 211_915_000_000)]
    table = rows_to_comparison_table(rows, value_scale=1e9, value_label="$B")
    assert len(table) == 1
    assert table[0]["ticker"] == "MSFT"
    assert abs(table[0]["value_scaled"] - 211.915) < 0.01


def test_rows_to_comparison_table_multi():
    rows = [
        _make_row("MSFT", 2023, 211_915_000_000),
        _make_row("AAPL", 2023, 383_285_000_000),
        _make_row("GOOGL", 2023, 307_394_000_000),
    ]
    table = rows_to_comparison_table(rows)
    tickers = [r["ticker"] for r in table]
    assert set(tickers) == {"MSFT", "AAPL", "GOOGL"}


def test_rows_to_comparison_table_empty():
    assert rows_to_comparison_table([]) == []


def test_xbrl_fact_row_value_is_decimal():
    row = _make_row("NVDA", 2024, 44_870_000_000)
    assert isinstance(row.value, Decimal)
