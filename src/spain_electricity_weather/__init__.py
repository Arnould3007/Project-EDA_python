from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from spain_electricity_weather.collect.ree import collect_raw, parse_hourly_series
from spain_electricity_weather.quality.ree import check_hourly_coverage

REE_START = date(2023, 1, 1)
REE_END = date(2025, 9, 30)
# The analytical grid is UTC; Madrid midnight-to-23:00 does not line up with
# UTC midnight. Derived from the local bounds, not hardcoded, so it stays
# correct if the project period ever changes.
REE_START_UTC = pd.Timestamp(REE_START, tz="Europe/Madrid").tz_convert("UTC")
REE_END_UTC = pd.Timestamp(f"{REE_END}T23:00", tz="Europe/Madrid").tz_convert("UTC")


def collect_ree(raw_dir: Path) -> None:
    written = collect_raw(REE_START, REE_END, raw_dir)
    print(f"REE collection done: {len(written)} month files in {raw_dir}")


def parse_ree(raw_dir: Path, out_path: Path) -> None:
    frame = parse_hourly_series(raw_dir)
    report = check_hourly_coverage(frame, REE_START_UTC, REE_END_UTC)
    print(
        f"parsed {report['rows']} rows, {report['duplicate_rows']} duplicate rows, "
        f"{len(report['missing_hours'])} missing hours, "
        f"{len(report['unexpected_hours'])} unexpected hours, "
        f"{report['null_price_rows']} null prices"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_path, index=False)
    print(f"saved to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="spain-electricity-weather")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser(
        "collect-ree", help="Fetch REE hourly day-ahead prices, Jan 2023 - Sep 2025"
    )
    collect_parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/ree"))

    parse_parser = subparsers.add_parser(
        "parse-ree", help="Build the clean hourly price series from the frozen raw snapshot"
    )
    parse_parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/ree"))
    parse_parser.add_argument(
        "--out", type=Path, default=Path("data/interim/ree_hourly_prices.csv")
    )

    args = parser.parse_args()

    if args.command == "collect-ree":
        collect_ree(args.raw_dir)
    elif args.command == "parse-ree":
        parse_ree(args.raw_dir, args.out)
