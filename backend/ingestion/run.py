"""Ingestion orchestrator.

Runs the full pipeline for specified tickers and fiscal years:
  1. FilingDownloader  -> download filing HTML from EDGAR, persist to filings table.
  2. xbrl_parser       -> fetch companyfacts JSON, upsert to xbrl_facts.
  3. narrative_parser  -> parse HTML -> sections + chunks (text + char offsets).

All three steps are idempotent: re-running against an already-ingested corpus
is a no-op (content-hash dedup for downloads; ON CONFLICT DO NOTHING everywhere).

Usage:
    uv run python -m backend.ingestion.run --tickers all
    uv run python -m backend.ingestion.run --tickers MSFT,AAPL --years 2024
    uv run python -m backend.ingestion.run --tickers NVDA --years 2023 --forms 10-K

  COST GATE: Full ingest (20 tickers × 5 years) downloads ~400 files from
   EDGAR. This takes several hours (rate limited to 8 req/s). The script
   checkpoints progress; re-starting after interruption only fetches missing
   filings. Run with --dry-run to see what would be fetched without downloading.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from backend.config import get_settings
from backend.db.session import get_session
from backend.ingestion.edgar_downloader import FilingDownloader
from backend.ingestion.narrative_parser import parse_and_persist
from backend.ingestion.xbrl_parser import parse_companyfacts_for_cik
from backend.logging_config import configure_logging, get_logger
from sqlalchemy import text

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Full pipeline for a single ticker
# ---------------------------------------------------------------------------


async def run_ticker(
    ticker: str,
    fiscal_years: list[int],
    forms: list[str],
    session_factory: object,  # async_sessionmaker
    dry_run: bool = False,
) -> dict[str, int]:
    stats: dict[str, int] = {
        "filings_added": 0,
        "filings_skipped": 0,
        "xbrl_inserted": 0,
        "sections": 0,
        "chunks": 0,
        "errors": 0,
    }
    settings = get_settings()

    async with get_session() as session:
        # --- Step 1: download filings ---
        dl = FilingDownloader(session)
        if dry_run:
            log.info("run.dry_run", ticker=ticker, note="skipping actual downloads")
        else:
            result = await dl.run(
                tickers=[ticker], fiscal_years=fiscal_years, forms=forms
            )
            stats["filings_added"] += result["added"]
            stats["filings_skipped"] += result["skipped"]
            stats["errors"] += result["errors"]

        # --- Step 2: XBRL companyfacts ---
        cik_row = await session.execute(
            text("SELECT cik FROM companies WHERE ticker = :t"), {"t": ticker}
        )
        cik = cik_row.scalar()
        if cik is None:
            log.error("run.no_cik", ticker=ticker)
            stats["errors"] += 1
            return stats

        if not dry_run:
            # Reuse the same SecClient from FilingDownloader's rate limiter
            # by creating a fresh one (rate limiters are per-run, not global)
            from backend.ingestion.edgar_downloader import SecClient

            sec_client = SecClient(user_agent=settings.sec_user_agent)
            try:
                xbrl_result = await parse_companyfacts_for_cik(
                    cik=int(cik),
                    ticker=ticker,
                    fiscal_years=fiscal_years,
                    session=session,
                    sec_client=sec_client,
                )
                stats["xbrl_inserted"] += xbrl_result["inserted"]
            finally:
                await sec_client.close()

        # --- Step 3: narrative parsing (parse any filings not yet parsed) ---
        unparsed = await session.execute(
            text(
                """
                SELECT f.id, f.form, f.raw_path
                FROM filings f
                JOIN companies c ON c.cik = f.cik
                LEFT JOIN filing_sections fs ON fs.filing_id = f.id
                WHERE c.ticker = :ticker
                  AND f.fiscal_year = ANY(:years)
                  AND f.form = ANY(:forms)
                  AND f.raw_path IS NOT NULL
                  AND fs.id IS NULL
                """
            ),
            {
                "ticker": ticker,
                "years": fiscal_years,
                "forms": forms,
            },
        )
        rows = unparsed.fetchall()
        if not rows:
            log.info("run.no_unparsed_narratives", ticker=ticker)
        else:
            log.info("run.parsing_narratives", ticker=ticker, count=len(rows))
            for row in rows:
                filing_id, form, raw_path_str = row
                if not raw_path_str:
                    continue
                raw_path = Path(raw_path_str)
                if not raw_path.exists():
                    log.warning(
                        "run.raw_path_missing",
                        filing_id=filing_id,
                        path=raw_path_str,
                    )
                    continue
                if dry_run:
                    continue
                try:
                    result = await parse_and_persist(
                        filing_id=str(filing_id),
                        raw_html_path=raw_path,
                        form=form,
                        session=session,
                    )
                    stats["sections"] += result["sections"]
                    stats["chunks"] += result["chunks"]
                except Exception as exc:
                    log.error(
                        "run.parse_error",
                        filing_id=filing_id,
                        error=str(exc),
                    )
                    stats["errors"] += 1

    return stats


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------


async def run_all(
    tickers: list[str],
    fiscal_years: list[int],
    forms: list[str],
    dry_run: bool = False,
) -> None:
    settings = get_settings()
    from backend.db.session import get_session_factory

    factory = get_session_factory()

    total: dict[str, int] = {
        "filings_added": 0,
        "filings_skipped": 0,
        "xbrl_inserted": 0,
        "sections": 0,
        "chunks": 0,
        "errors": 0,
    }

    for ticker in tickers:
        log.info("run.ticker_start", ticker=ticker, dry_run=dry_run)
        result = await run_ticker(
            ticker=ticker,
            fiscal_years=fiscal_years,
            forms=forms,
            session_factory=factory,
            dry_run=dry_run,
        )
        for key, val in result.items():
            total[key] = total.get(key, 0) + val
        log.info("run.ticker_done", ticker=ticker, **result)

    log.info("run.complete", **total)
    print("\n=== Ingestion summary ===")
    for key, val in total.items():
        print(f"  {key}: {val}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest SEC filings for the locked 20-ticker universe."
    )
    parser.add_argument(
        "--tickers",
        default="all",
        help="Comma-separated tickers (e.g. MSFT,AAPL) or 'all' for the full universe.",
    )
    parser.add_argument(
        "--years",
        default="all",
        help="Comma-separated fiscal years (e.g. 2023,2024) or 'all' for FY2020-2024.",
    )
    parser.add_argument(
        "--forms",
        default="10-K,10-Q,8-K",
        help="Comma-separated form types.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be fetched without downloading anything.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=False)

    # Validate User-Agent before touching EDGAR
    if not args.dry_run:
        settings.assert_sec_user_agent_set()

    tickers = settings.ticker_list if args.tickers == "all" else [
        t.strip().upper() for t in args.tickers.split(",") if t.strip()
    ]
    fiscal_years = settings.fiscal_year_list if args.years == "all" else [
        int(y.strip()) for y in args.years.split(",") if y.strip()
    ]
    forms = [f.strip() for f in args.forms.split(",") if f.strip()]

    # Validate tickers are in the locked universe
    allowed = set(settings.ticker_list)
    unknown = set(tickers) - allowed
    if unknown:
        parser.error(
            f"Unknown tickers (not in the locked universe): {sorted(unknown)}. "
            f"Scope-creep guard: add them to TICKERS env var and schema.sql first."
        )

    log.info(
        "run.starting",
        tickers=tickers,
        fiscal_years=fiscal_years,
        forms=forms,
        dry_run=args.dry_run,
    )

    asyncio.run(
        run_all(
            tickers=tickers,
            fiscal_years=fiscal_years,
            forms=forms,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
