from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from .models import parse_iso_z
from .result_scraper import FifaResultSource, FifaSourceConfig, scrape_results_once
from .runner import RunnerConfig, run_due_once
from .storage import get_store


def add_fifa_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fifa-api-base-url", default=FifaSourceConfig.api_base_url)
    parser.add_argument("--fifa-competition-id", default=FifaSourceConfig.competition_id)
    parser.add_argument("--fifa-season-id", default=FifaSourceConfig.season_id)
    parser.add_argument("--fifa-locale", default=FifaSourceConfig.locale)
    parser.add_argument("--fifa-count", type=int, default=FifaSourceConfig.count)
    parser.add_argument("--result-timeout-seconds", type=float, default=FifaSourceConfig.timeout_seconds)


def fifa_source_from_args(args: argparse.Namespace) -> FifaResultSource:
    return FifaResultSource(
        FifaSourceConfig(
            api_base_url=args.fifa_api_base_url,
            competition_id=args.fifa_competition_id,
            season_id=args.fifa_season_id,
            locale=args.fifa_locale,
            count=args.fifa_count,
            timeout_seconds=args.result_timeout_seconds,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="World Cup tipping cron workflow.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_due = subparsers.add_parser("run-due", help="Call due endpoints and score completed fixtures.")
    run_due.add_argument("--data-dir", type=Path, default=None)
    run_due.add_argument("--now", default=None, help="UTC ISO timestamp override, for tests or dry runs.")
    run_due.add_argument("--lock-minutes", type=int, default=30)
    run_due.add_argument("--lookahead-hours", type=int, default=24)
    run_due.add_argument("--timeout-seconds", type=float, default=15.0)
    run_due.add_argument("--retries", type=int, default=1)
    run_due.add_argument("--no-scrape-results", action="store_true", help="Skip automatic FIFA result scraping.")
    add_fifa_source_args(run_due)

    scrape_results = subparsers.add_parser("scrape-results", help="Scrape FIFA results into fixtures.json.")
    scrape_results.add_argument("--data-dir", type=Path, default=None)
    scrape_results.add_argument("--dry-run", action="store_true", help="Fetch and report updates without writing JSON files.")
    add_fifa_source_args(scrape_results)
    args = parser.parse_args()

    if args.command == "run-due":
        now: datetime | None = parse_iso_z(args.now) if args.now else None
        config = RunnerConfig(
            lock_minutes=args.lock_minutes,
            lookahead_hours=args.lookahead_hours,
            timeout_seconds=args.timeout_seconds,
            retries=args.retries,
            scrape_results=not args.no_scrape_results,
        )
        result = asyncio.run(run_due_once(get_store(args.data_dir), config, now, fifa_source_from_args(args)))
        print(result)
    elif args.command == "scrape-results":
        result = asyncio.run(
            scrape_results_once(
                get_store(args.data_dir),
                source=fifa_source_from_args(args),
                dry_run=args.dry_run,
            )
        )
        print(result)


if __name__ == "__main__":
    main()
