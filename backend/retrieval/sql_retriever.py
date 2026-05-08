"""Structured XBRL facts retriever.

Used by the XBRLSQLTool when the agent needs precise financial numbers, not
narrative text. Returns rows from `xbrl_facts` with full provenance so every
value can be cited back to a specific filing.

Design rules (critical, do not remove)
------------------------------------------
* XBRL is the source of truth for any GAAP-tagged financial figure.
* The agent must call this tool for revenue, net income, capex, R&D, etc.
  Never try to extract these from narrative chunk text.
* All returned values include (cik, accession_number, period_end, form) so
  the Synthesizer can construct a Citation without a separate lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.logging_config import get_logger

log = get_logger(__name__)


@dataclass
class XbrlFactRow:
    ticker: str
    cik: int
    canonical_concept: str
    fiscal_year: int
    fiscal_period: str   # 'FY', 'Q1', 'Q2', 'Q3'
    form: str
    period_start: date | None
    period_end: date
    value: Decimal
    unit: str
    accession_number: str


async def query_facts(
    session: AsyncSession,
    canonical_concept: str,
    tickers: list[str] | None = None,
    fiscal_years: list[int] | None = None,
    fiscal_period: str | None = None,   # 'FY', 'Q1', 'Q2', 'Q3'
    form: str = "10-K",
) -> list[XbrlFactRow]:
    """Query xbrl_facts for a canonical concept across companies / years.

    Args:
        canonical_concept: Normalised concept name from xbrl_parser.CONCEPT_ALIASES,
                           e.g. 'revenue', 'net_income', 'capex'.
        tickers:           Filter to specific tickers; None = all 20.
        fiscal_years:      Filter to specific years; None = FY2020-2024.
        fiscal_period:     'FY' for annual, 'Q1'-'Q3' for quarterly; None = all.
        form:              '10-K' or '10-Q'.

    Returns:
        List of XbrlFactRow sorted by (ticker, period_end).
    """
    where_clauses = ["xf.canonical_concept = :concept", "xf.form = :form"]
    params: dict[str, Any] = {"concept": canonical_concept, "form": form}

    if tickers:
        where_clauses.append("co.ticker = ANY(:tickers)")
        params["tickers"] = tickers

    if fiscal_years:
        where_clauses.append("xf.fiscal_year = ANY(:years)")
        params["years"] = fiscal_years

    if fiscal_period:
        where_clauses.append("xf.fiscal_period = :fp")
        params["fp"] = fiscal_period

    where_sql = " AND ".join(where_clauses)

    sql = text(
        f"""
        SELECT
            co.ticker,
            co.cik,
            xf.canonical_concept,
            xf.fiscal_year,
            xf.fiscal_period,
            xf.form,
            xf.period_start,
            xf.period_end,
            xf.value,
            xf.unit,
            xf.accession_number
        FROM xbrl_facts xf
        JOIN companies co ON co.cik = xf.cik
        WHERE {where_sql}
        ORDER BY co.ticker, xf.period_end
        """
    )

    rows = (await session.execute(sql, params)).fetchall()

    return [
        XbrlFactRow(
            ticker=r[0],
            cik=r[1],
            canonical_concept=r[2],
            fiscal_year=r[3],
            fiscal_period=r[4],
            form=r[5],
            period_start=r[6],
            period_end=r[7],
            value=r[8],
            unit=r[9],
            accession_number=r[10],
        )
        for r in rows
    ]


async def query_multi_concept(
    session: AsyncSession,
    canonical_concepts: list[str],
    tickers: list[str] | None = None,
    fiscal_years: list[int] | None = None,
    form: str = "10-K",
) -> dict[str, list[XbrlFactRow]]:
    """Convenience wrapper: fetch multiple concepts in one call.

    Returns: {canonical_concept: [XbrlFactRow, ...]}
    """
    result: dict[str, list[XbrlFactRow]] = {}
    for concept in canonical_concepts:
        rows = await query_facts(
            session=session,
            canonical_concept=concept,
            tickers=tickers,
            fiscal_years=fiscal_years,
            form=form,
        )
        result[concept] = rows
    return result


def rows_to_comparison_table(
    rows: list[XbrlFactRow],
    value_scale: float = 1e9,
    value_label: str = "$B",
) -> list[dict[str, Any]]:
    """Convert a flat list of XbrlFactRows into a pivot-style table for the UI.

    Returns a list of dicts: [{ticker, fiscal_year, value_scaled, unit, ...}, ...]
    Useful for Tier-3 multi-company comparison answers.
    """
    return [
        {
            "ticker": r.ticker,
            "fiscal_year": r.fiscal_year,
            "fiscal_period": r.fiscal_period,
            "concept": r.canonical_concept,
            "value": float(r.value),
            "value_scaled": float(r.value) / value_scale,
            "value_label": value_label,
            "unit": r.unit,
            "period_end": r.period_end.isoformat(),
            "accession_number": r.accession_number,
        }
        for r in rows
    ]
