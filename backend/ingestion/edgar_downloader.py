"""EDGAR filing downloader.

Uses the SEC EDGAR Submissions REST API directly for maximum reliability:
  https://data.sec.gov/submissions/CIK{cik:010d}.json
  https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{primary_doc}

edgartools is used for any higher-level helpers that are genuinely simpler,
but the core download loop goes through httpx to keep the rate limiter tight.

SEC fair-use rules:
  - Max 10 requests/second
  - User-Agent header with real contact info (settings.sec_user_agent)
  - Violating either = IP ban
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db.session import get_session
from backend.logging_config import get_logger

log = get_logger(__name__)

EDGAR_BASE = "https://data.sec.gov"
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
EDGAR_SUBMISSIONS_URL = EDGAR_BASE + "/submissions/CIK{cik:010d}.json"
EDGAR_SUBMISSIONS_MORE_URL = EDGAR_BASE + "/submissions/{filename}"

# Which sections of the submissions JSON to treat as the primary doc for each form.
PRIMARY_DOC_EXTENSIONS = {".htm", ".html", ".txt"}


# ---------------------------------------------------------------------------
# Rate limiter, token-bucket capped at max_rps requests per second.
# ---------------------------------------------------------------------------


class RateLimiter:
    """Simple async token-bucket rate limiter."""

    def __init__(self, max_rps: float = 8.0) -> None:
        # Stay at 8 rps, not 10. buffer for any network jitter.
        self._max_rps = max_rps
        self._min_interval = 1.0 / max_rps
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


# ---------------------------------------------------------------------------
# SEC client, thin async httpx wrapper with rate limiting.
# ---------------------------------------------------------------------------


@dataclass
class SecClient:
    user_agent: str
    rate_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(max_rps=8.0))
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self.user_agent},
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_json(self, url: str) -> dict[str, Any]:
        await self.rate_limiter.acquire()
        client = self._get_client()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def get_bytes(self, url: str) -> bytes:
        await self.rate_limiter.acquire()
        client = self._get_client()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Filing record, parsed from the submissions JSON.
# ---------------------------------------------------------------------------


@dataclass
class FilingRecord:
    cik: int
    ticker: str
    accession_number: str        # format: 0000950170-23-033960
    form: str                    # '10-K', '10-Q', '8-K'
    filed_date: date
    period_end: date             # periodOfReport
    fiscal_year: int
    fiscal_period: str           # 'FY', 'Q1', 'Q2', 'Q3'
    primary_doc_url: str         # full URL to primary HTML document


# ---------------------------------------------------------------------------
# Helpers, submissions JSON parsing.
# ---------------------------------------------------------------------------


def _parse_filing_date(s: str) -> date:
    return date.fromisoformat(s) if s else date.min


def _clean_accession(raw: str) -> str:
    """Normalise accession: '0000950170-23-033960' → already normalised; strip if needed."""
    return raw.replace("-", "", 0)  # keep hyphens; SEC path uses them


def _accession_to_path(accession: str) -> str:
    """'0000950170-23-033960' → '000095017023033960' (no hyphens, for URL path)."""
    return accession.replace("-", "")


def _infer_fiscal_year_and_period(
    filed_date: date, period_end: date, form: str
) -> tuple[int, str]:
    """Infer fiscal year and period from filing metadata.

    For 10-K: fiscal year = the year the period ends in.
    For 10-Q: fiscal year = year of period end, period = Q1/Q2/Q3.
    For 8-K: fiscal year = calendar year of filing date.
    """
    if form == "10-K":
        return period_end.year, "FY"
    if form == "10-Q":
        month = period_end.month
        # Most US fiscal quarters:
        # Q1 ends Jan/Mar/Apr, Q2 ends Apr/Jun/Jul, Q3 ends Jul/Sep/Oct
        # Map by calendar quarter of period end
        q = (month - 1) // 3 + 1
        return period_end.year, f"Q{q}"
    # 8-K
    return filed_date.year, "FY"


def _select_primary_document(index_json: dict[str, Any]) -> str | None:
    """Pick the primary human-readable document from a filing index.

    SEC filing index includes a list of documents with types. We prefer the
    document with type '10-K', '10-Q', '8-K' etc, over generic types.
    """
    docs = index_json.get("documents", [])
    # First try to find a doc whose type matches the form name
    for doc in docs:
        doc_type = (doc.get("type") or "").upper().strip()
        filename = doc.get("name") or doc.get("filename") or ""
        ext = Path(filename).suffix.lower()
        if ext in PRIMARY_DOC_EXTENSIONS and doc_type in {
            "10-K", "10-Q", "8-K", "10-K/A", "10-Q/A", "8-K/A",
            "10K", "10Q", "8K",
        }:
            return filename
    # Fallback: first HTML/HTM file
    for doc in docs:
        filename = doc.get("name") or doc.get("filename") or ""
        if Path(filename).suffix.lower() in {".htm", ".html"}:
            return filename
    return None


# ---------------------------------------------------------------------------
# Main downloader class.
# ---------------------------------------------------------------------------


class FilingDownloader:
    """Downloads and persists SEC filings for the locked ticker universe.

    Usage:
        async with get_session() as session:
            dl = FilingDownloader(session)
            await dl.run(tickers=settings.ticker_list, ...)
    """

    def __init__(self, session: AsyncSession) -> None:
        settings = get_settings()
        settings.assert_sec_user_agent_set()
        self.settings = settings
        self.session = session
        self.client = SecClient(user_agent=settings.sec_user_agent)
        self.raw_root = Path("data/raw")
        self.raw_root.mkdir(parents=True, exist_ok=True)

    async def close(self) -> None:
        await self.client.close()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        tickers: list[str],
        fiscal_years: list[int],
        forms: list[str] | None = None,
    ) -> dict[str, int]:
        """Download all qualifying filings; return stats dict."""
        if forms is None:
            forms = ["10-K", "10-Q", "8-K"]

        total_added = total_skipped = total_errors = 0

        for ticker in tickers:
            cik = await self._get_cik_for_ticker(ticker)
            if cik is None:
                log.warning("ingestion.ticker_not_found", ticker=ticker)
                continue

            log.info("ingestion.start_ticker", ticker=ticker, cik=cik)
            added, skipped, errors = await self._download_company(
                cik, ticker, fiscal_years, forms
            )
            total_added += added
            total_skipped += skipped
            total_errors += errors
            log.info(
                "ingestion.done_ticker",
                ticker=ticker,
                added=added,
                skipped=skipped,
                errors=errors,
            )

        try:
            await self.close()
        except Exception:
            pass

        return {"added": total_added, "skipped": total_skipped, "errors": total_errors}

    # ------------------------------------------------------------------
    # Per-company download loop
    # ------------------------------------------------------------------

    async def _download_company(
        self,
        cik: int,
        ticker: str,
        fiscal_years: list[int],
        forms: list[str],
    ) -> tuple[int, int, int]:
        added = skipped = errors = 0

        # Fetch submissions JSON (includes recent + older file references)
        try:
            submissions = await self._fetch_all_submissions(cik)
        except httpx.HTTPStatusError as exc:
            log.error("ingestion.submissions_fetch_error", cik=cik, status=exc.response.status_code)
            return 0, 0, 1

        # Build filing records from the flat parallel arrays
        records = self._parse_filing_records(submissions, cik, ticker, fiscal_years, forms)
        log.info("ingestion.records_found", ticker=ticker, count=len(records))

        for rec in records:
            already = await self._filing_exists(rec.accession_number)
            if already:
                log.debug("ingestion.skip_existing", accession=rec.accession_number)
                skipped += 1
                continue

            try:
                await self._download_and_persist(rec)
                added += 1
                log.info(
                    "ingestion.filing_saved",
                    ticker=ticker,
                    form=rec.form,
                    period=rec.period_end.isoformat(),
                    accession=rec.accession_number,
                )
            except Exception as exc:
                log.error(
                    "ingestion.filing_error",
                    accession=rec.accession_number,
                    error=str(exc),
                )
                errors += 1

        return added, skipped, errors

    # ------------------------------------------------------------------
    # Submissions JSON. handles pagination for companies with many filings.
    # ------------------------------------------------------------------

    async def _fetch_all_submissions(self, cik: int) -> dict[str, Any]:
        url = EDGAR_SUBMISSIONS_URL.format(cik=cik)
        data = await self.client.get_json(url)

        # Some large companies have overflow files referenced in `filings.files`.
        overflow_files = data.get("filings", {}).get("files", [])
        if not overflow_files:
            return data

        # Merge recent + overflow into a combined recent-style dict.
        # All overflow files use the same column names as `filings.recent`.
        combined: dict[str, list[Any]] = {}
        recent = data.get("filings", {}).get("recent", {})
        for key, vals in recent.items():
            combined[key] = list(vals)

        for file_entry in overflow_files:
            more_url = EDGAR_SUBMISSIONS_MORE_URL.format(filename=file_entry["name"])
            more_data = await self.client.get_json(more_url)
            for key, vals in more_data.items():
                combined.setdefault(key, []).extend(vals)

        data["filings"]["recent"] = combined
        return data

    # ------------------------------------------------------------------
    # Parse filing records from the submissions JSON.
    # ------------------------------------------------------------------

    def _parse_filing_records(
        self,
        submissions: dict[str, Any],
        cik: int,
        ticker: str,
        fiscal_years: list[int],
        forms: list[str],
    ) -> list[FilingRecord]:
        recent = submissions.get("filings", {}).get("recent", {})
        if not recent:
            return []

        accessions: list[str] = recent.get("accessionNumber", [])
        filing_dates: list[str] = recent.get("filingDate", [])
        report_dates: list[str] = recent.get("reportDate", [])
        form_types: list[str] = recent.get("form", [])
        primary_docs: list[str] = recent.get("primaryDocument", [])

        records: list[FilingRecord] = []
        forms_set = set(forms)

        for acc, fd_str, rd_str, form_type, primary_doc in zip(
            accessions, filing_dates, report_dates, form_types, primary_docs
        ):
            # Normalise form: strip /A variants for matching, keep for storage
            base_form = re.sub(r"/A$", "", form_type.strip(), flags=re.IGNORECASE).upper()
            if base_form not in forms_set:
                continue

            filed_date = _parse_filing_date(fd_str)
            period_end = _parse_filing_date(rd_str) if rd_str else filed_date
            if period_end == date.min:
                period_end = filed_date

            fy, fp = _infer_fiscal_year_and_period(filed_date, period_end, base_form)
            if fy not in fiscal_years:
                continue

            # Build the document URL
            acc_path = _accession_to_path(acc)
            primary_doc_url = (
                f"{EDGAR_ARCHIVES}/{cik}/{acc_path}/{primary_doc}"
                if primary_doc
                else ""
            )

            records.append(
                FilingRecord(
                    cik=cik,
                    ticker=ticker,
                    accession_number=acc,
                    form=base_form,
                    filed_date=filed_date,
                    period_end=period_end,
                    fiscal_year=fy,
                    fiscal_period=fp,
                    primary_doc_url=primary_doc_url,
                )
            )

        return records

    # ------------------------------------------------------------------
    # Download a single filing and persist to disk + DB.
    # ------------------------------------------------------------------

    async def _download_and_persist(self, rec: FilingRecord) -> None:
        # Fetch the primary HTML document
        if not rec.primary_doc_url:
            raise ValueError(f"No primary document URL for {rec.accession_number}")

        raw_bytes = await self.client.get_bytes(rec.primary_doc_url)
        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        # Save to disk: data/raw/{cik}/{accession_no_path}/primary.html
        acc_path = _accession_to_path(rec.accession_number)
        filing_dir = self.raw_root / str(rec.cik) / acc_path
        filing_dir.mkdir(parents=True, exist_ok=True)
        raw_path = filing_dir / "primary.html"
        raw_path.write_bytes(raw_bytes)

        # Persist to DB
        await self.session.execute(
            text(
                """
                INSERT INTO filings (
                    cik, accession_number, form, fiscal_year, fiscal_period,
                    filed_date, period_end, primary_doc_url, content_sha256, raw_path
                ) VALUES (
                    :cik, :accession, :form, :fiscal_year, :fiscal_period,
                    :filed_date, :period_end, :primary_doc_url, :sha256, :raw_path
                )
                ON CONFLICT (accession_number) DO NOTHING
                """
            ),
            {
                "cik": rec.cik,
                "accession": rec.accession_number,
                "form": rec.form,
                "fiscal_year": rec.fiscal_year,
                "fiscal_period": rec.fiscal_period,
                "filed_date": rec.filed_date,
                "period_end": rec.period_end,
                "primary_doc_url": rec.primary_doc_url,
                "sha256": sha256,
                "raw_path": str(raw_path),
            },
        )
        await self.session.flush()

    # ------------------------------------------------------------------
    # Dedup helpers
    # ------------------------------------------------------------------

    async def _filing_exists(self, accession_number: str) -> bool:
        row = await self.session.execute(
            text("SELECT 1 FROM filings WHERE accession_number = :acc LIMIT 1"),
            {"acc": accession_number},
        )
        return row.scalar() is not None

    async def _get_cik_for_ticker(self, ticker: str) -> int | None:
        row = await self.session.execute(
            text("SELECT cik FROM companies WHERE ticker = :t LIMIT 1"),
            {"t": ticker.upper()},
        )
        val = row.scalar()
        return int(val) if val is not None else None
