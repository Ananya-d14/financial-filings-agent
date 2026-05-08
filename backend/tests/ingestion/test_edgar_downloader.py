"""Unit tests for edgar_downloader, parsing logic (no network, no DB)."""

from __future__ import annotations

from datetime import date

import pytest

from backend.ingestion.edgar_downloader import (
    FilingRecord,
    _accession_to_path,
    _clean_accession,
    _infer_fiscal_year_and_period,
    _parse_filing_date,
)


def test_parse_filing_date_valid() -> None:
    assert _parse_filing_date("2024-01-28") == date(2024, 1, 28)


def test_parse_filing_date_empty() -> None:
    assert _parse_filing_date("") == date.min


def test_accession_to_path() -> None:
    assert _accession_to_path("0000950170-23-033960") == "000095017023033960"


def test_infer_fy_10k() -> None:
    fy, fp = _infer_fiscal_year_and_period(
        filed_date=date(2024, 2, 1),
        period_end=date(2024, 1, 28),
        form="10-K",
    )
    assert fy == 2024
    assert fp == "FY"


def test_infer_fy_10q_q1() -> None:
    fy, fp = _infer_fiscal_year_and_period(
        filed_date=date(2024, 5, 10),
        period_end=date(2024, 3, 31),
        form="10-Q",
    )
    assert fy == 2024
    assert fp == "Q1"


def test_infer_fy_10q_q3() -> None:
    fy, fp = _infer_fiscal_year_and_period(
        filed_date=date(2024, 11, 5),
        period_end=date(2024, 9, 30),
        form="10-Q",
    )
    assert fy == 2024
    assert fp == "Q3"


def test_infer_fy_8k() -> None:
    fy, fp = _infer_fiscal_year_and_period(
        filed_date=date(2024, 7, 15),
        period_end=date(2024, 7, 15),
        form="8-K",
    )
    assert fy == 2024
    assert fp == "FY"


def test_filing_record_is_dataclass() -> None:
    rec = FilingRecord(
        cik=1045810,
        ticker="NVDA",
        accession_number="0001045810-24-000013",
        form="10-K",
        filed_date=date(2024, 2, 21),
        period_end=date(2024, 1, 28),
        fiscal_year=2024,
        fiscal_period="FY",
        primary_doc_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581024000013/nvda-20240128.htm",
    )
    assert rec.ticker == "NVDA"
    assert rec.form == "10-K"
