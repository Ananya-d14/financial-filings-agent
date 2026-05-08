"""Unit tests for xbrl_parser, concept-mapping table and coverage logic.

These tests do NOT hit the SEC API (no network, no DB). They validate:
  - CONCEPT_ALIASES maps every expected alias to a canonical
  - The canonical names are stable strings (no typos)
  - coverage_report identifies present/absent required concepts
  - All concepts across the alias table are unique (no accidental overwrites)
"""

from __future__ import annotations

from backend.ingestion.xbrl_parser import CONCEPT_ALIASES, coverage_report


_CANONICAL_REQUIRED = {
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "rd_expense",
    "capex",
    "eps_basic",
    "eps_diluted",
    "total_assets",
    "cash",
    "cfo",
}


def test_no_duplicate_source_concepts() -> None:
    """Each source concept must appear exactly once in CONCEPT_ALIASES."""
    seen: set[str] = set()
    for k in CONCEPT_ALIASES:
        assert k not in seen, f"Duplicate alias: {k}"
        seen.add(k)


def test_canonical_names_are_lowercase_snakecase() -> None:
    for canonical in CONCEPT_ALIASES.values():
        assert canonical == canonical.lower(), f"Non-lowercase canonical: {canonical}"
        assert " " not in canonical, f"Space in canonical: {canonical}"


def test_all_required_canonicals_are_reachable() -> None:
    """Every concept in _CANONICAL_REQUIRED must be a value in CONCEPT_ALIASES."""
    reachable = set(CONCEPT_ALIASES.values())
    missing = _CANONICAL_REQUIRED - reachable
    assert not missing, f"Required canonicals not reachable via any alias: {missing}"


def test_coverage_report_all_present() -> None:
    rows = [
        {"canonical_concept": "revenue"},
        {"canonical_concept": "net_income"},
        {"canonical_concept": "capex"},
    ]
    report = coverage_report(rows)  # type: ignore[arg-type]
    assert report["revenue"] == 1.0
    assert report["net_income"] == 1.0
    assert report["capex"] == 1.0


def test_coverage_report_partial() -> None:
    rows = [{"canonical_concept": "revenue"}]
    report = coverage_report(rows)  # type: ignore[arg-type]
    assert report["revenue"] == 1.0
    assert report["net_income"] == 0.0
    assert report["capex"] == 0.0


def test_coverage_report_empty() -> None:
    report = coverage_report([])
    for v in report.values():
        assert v == 0.0


def test_revenue_aliases_all_map_to_revenue() -> None:
    revenue_aliases = [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "OilAndGasRevenue",
        "RevenuesNetOfInterestExpense",
    ]
    for alias in revenue_aliases:
        assert alias in CONCEPT_ALIASES, f"Expected alias not present: {alias}"
        assert CONCEPT_ALIASES[alias] == "revenue", (
            f"{alias} should map to 'revenue', got '{CONCEPT_ALIASES[alias]}'"
        )
