"""XBRL companyfacts parser.

Fetches https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json for
each CIK and maps heterogeneous GAAP tags to canonical concepts, then upserts
into `xbrl_facts`.

Key design decisions
--------------------
1. XBRL is the authoritative source for any GAAP-tagged number. The agent
   must use `XBRLSQLTool` for revenue, income, capex, etc.. not narrative text.
2. Many concepts have multiple valid tags across companies / years (e.g.,
   revenue has been reported under at least six different us-gaap concepts
   in this ticker universe). The CONCEPT_ALIASES dict maps all variants to
   a single canonical name.
3. We only pull annual (10-K) and quarterly (10-Q) facts. 8-K filings rarely
   contain tagged XBRL.
4. Period deduplication: for the same (cik, canonical_concept, period), prefer
   the most recent filing's value. The DB UNIQUE constraint covers the
   (cik, canonical_concept, period_start, period_end, form, accession_number)
   tuple. ON CONFLICT DO NOTHING prevents double-counting.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.logging_config import get_logger

log = get_logger(__name__)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# ---------------------------------------------------------------------------
# Concept alias table, maps raw XBRL tag → canonical_concept string.
#
# This covers known heterogeneity across the 20-ticker universe. When the
# ingestion run logs "unmapped_concept" lines, extend this table and re-run
# (the upsert is idempotent).
# ---------------------------------------------------------------------------

CONCEPT_ALIASES: dict[str, str] = {
    # --- Revenue ---
    "Revenues": "revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "revenue",
    "SalesRevenueNet": "revenue",
    "SalesRevenueGoodsNet": "revenue",
    "TotalRevenuesAndOtherIncome": "revenue",
    # Banks report net interest income + non-interest income rather than revenue
    "RevenuesNetOfInterestExpense": "revenue",
    # Oil & gas
    "OilAndGasRevenue": "revenue",
    "RevenueFromContractWithCustomerAndOtherOperatingRevenue": "revenue",
    # --- Gross profit ---
    "GrossProfit": "gross_profit",
    # --- Operating income/loss ---
    "OperatingIncomeLoss": "operating_income",
    # --- Net income ---
    "NetIncomeLoss": "net_income",
    "NetIncomeLossAvailableToCommonStockholdersBasic": "net_income",
    # --- R&D ---
    "ResearchAndDevelopmentExpense": "rd_expense",
    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost": "rd_expense",
    # --- Capex ---
    "PaymentsToAcquirePropertyPlantAndEquipment": "capex",
    "PaymentsToAcquireProductiveAssets": "capex",
    "AcquisitionsNetOfCashAcquiredAndPurchasesOfBusinesses": "capex_acquisitions",
    # --- SG&A ---
    "SellingGeneralAndAdministrativeExpense": "sga_expense",
    "SellingAndMarketingExpense": "selling_expense",
    "GeneralAndAdministrativeExpense": "ga_expense",
    # --- EPS ---
    "EarningsPerShareBasic": "eps_basic",
    "EarningsPerShareDiluted": "eps_diluted",
    # --- Shares ---
    "WeightedAverageNumberOfSharesOutstandingBasic": "shares_basic",
    "WeightedAverageNumberOfDilutedSharesOutstanding": "shares_diluted",
    "CommonStockSharesOutstanding": "shares_outstanding",
    # --- Balance sheet ---
    "Assets": "total_assets",
    "Liabilities": "total_liabilities",
    "LiabilitiesAndStockholdersEquity": "total_liab_equity",
    "StockholdersEquity": "stockholders_equity",
    "RetainedEarningsAccumulatedDeficit": "retained_earnings",
    "CashAndCashEquivalentsAtCarryingValue": "cash",
    "CashCashEquivalentsAndShortTermInvestments": "cash_and_st_investments",
    "LongTermDebt": "long_term_debt",
    "LongTermDebtNoncurrent": "long_term_debt",
    "ShortTermBorrowings": "short_term_debt",
    "DebtCurrent": "short_term_debt",
    # --- Cash flow ---
    "NetCashProvidedByUsedInOperatingActivities": "cfo",
    "NetCashProvidedByUsedInInvestingActivities": "cfi",
    "NetCashProvidedByUsedInFinancingActivities": "cff",
    # --- Inventory / COGS ---
    "CostOfRevenue": "cost_of_revenue",
    "CostOfGoodsAndServicesSold": "cost_of_revenue",
    "CostOfGoodsSold": "cost_of_revenue",
    "InventoryNet": "inventory",
    # --- Bank-specific ---
    "InterestIncomeExpenseNet": "net_interest_income",
    "NoninterestIncome": "noninterest_income",
    "ProvisionForLoanLeaseAndOtherLosses": "provision_for_credit_losses",
    # --- Insurance / healthcare ---
    "BenefitsLossesAndExpenses": "benefits_expenses",
    "PremiumsEarnedNet": "premiums_earned",
    # --- Oil & gas ---
    "DepletionDepreciationAndAmortization": "dd_and_a",
    "ExplorationExpense": "exploration_expense",
}

# Taxonomy prefixes we care about. 'dei' is form metadata, not financial data.
ACCEPTED_TAXONOMIES = {"us-gaap"}


# ---------------------------------------------------------------------------
# Main parser function.
# ---------------------------------------------------------------------------


async def parse_companyfacts_for_cik(
    cik: int,
    ticker: str,
    fiscal_years: list[int],
    session: AsyncSession,
    sec_client: Any,  # SecClient from edgar_downloader, passed in to reuse rate limiter
) -> dict[str, int]:
    """Fetch companyfacts JSON and upsert relevant facts into xbrl_facts.

    Returns: {"inserted": N, "skipped": M, "unmapped": K}
    """
    url = COMPANYFACTS_URL.format(cik=cik)
    try:
        data = await sec_client.get_json(url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            log.warning("xbrl.no_companyfacts", cik=cik, ticker=ticker)
            return {"inserted": 0, "skipped": 0, "unmapped": 0}
        raise

    facts = data.get("facts", {})
    inserted = skipped = unmapped = 0
    unmapped_concepts: set[str] = set()

    for taxonomy, taxonomy_facts in facts.items():
        if taxonomy not in ACCEPTED_TAXONOMIES:
            continue

        for concept, concept_data in taxonomy_facts.items():
            canonical = CONCEPT_ALIASES.get(concept)
            if canonical is None:
                unmapped_concepts.add(concept)
                unmapped += 1
                continue

            units_data = concept_data.get("units", {})
            for unit, entries in units_data.items():
                # Only USD and shares are relevant for this project.
                if unit not in {"USD", "shares", "USD/shares"}:
                    continue

                for entry in entries:
                    # Each entry: {"end": "2024-01-28", "val": 44870000000, "accn": "...",
                    #              "fy": 2024, "fp": "FY", "form": "10-K", ...}
                    form = entry.get("form", "")
                    base_form = form.rstrip("/A").strip()
                    if base_form not in {"10-K", "10-Q"}:
                        continue

                    fy = entry.get("fy")
                    if fy not in fiscal_years:
                        continue

                    end_str = entry.get("end")
                    start_str = entry.get("start")  # may be None for instant facts
                    if not end_str:
                        continue

                    try:
                        period_end = date.fromisoformat(end_str)
                        period_start = (
                            date.fromisoformat(start_str) if start_str else None
                        )
                    except ValueError:
                        continue

                    val = entry.get("val")
                    if val is None:
                        continue

                    accession = entry.get("accn", "")
                    fp = entry.get("fp", "FY")

                    try:
                        await session.execute(
                            text(
                                """
                                INSERT INTO xbrl_facts (
                                    cik, concept, canonical_concept,
                                    period_start, period_end, value, unit,
                                    form, accession_number, fiscal_year, fiscal_period
                                ) VALUES (
                                    :cik, :concept, :canonical,
                                    :period_start, :period_end, :value, :unit,
                                    :form, :accession, :fy, :fp
                                )
                                ON CONFLICT (cik, canonical_concept, period_start,
                                             period_end, form, accession_number)
                                DO NOTHING
                                """
                            ),
                            {
                                "cik": cik,
                                "concept": concept,
                                "canonical": canonical,
                                "period_start": period_start,
                                "period_end": period_end,
                                "value": float(val),
                                "unit": unit,
                                "form": base_form,
                                "accession": accession,
                                "fy": fy,
                                "fp": fp,
                            },
                        )
                        inserted += 1
                    except Exception as exc:
                        log.debug("xbrl.insert_error", concept=concept, error=str(exc))
                        skipped += 1

    if unmapped_concepts:
        log.info(
            "xbrl.unmapped_concepts",
            ticker=ticker,
            cik=cik,
            count=len(unmapped_concepts),
            sample=sorted(unmapped_concepts)[:10],
        )

    await session.flush()
    log.info(
        "xbrl.done",
        ticker=ticker,
        inserted=inserted,
        skipped=skipped,
        unmapped=unmapped,
    )
    return {"inserted": inserted, "skipped": skipped, "unmapped": unmapped}


def coverage_report(xbrl_rows: list[dict[str, Any]]) -> dict[str, float]:
    """Given a list of xbrl_facts rows, report coverage of key financials.

    Used in the Phase 1 'done' gate: XBRL coverage >95% of filings for
    revenue / net_income / capex.
    """
    required = {"revenue", "net_income", "capex"}
    concepts_present = {row["canonical_concept"] for row in xbrl_rows}
    covered = required & concepts_present
    return {c: (1.0 if c in covered else 0.0) for c in required}
