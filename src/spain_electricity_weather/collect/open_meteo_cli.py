"""Open-Meteo commands: online collection and offline rebuild from the raw snapshot.

Usage, from the project root:
    uv run python -m spain_electricity_weather.collect.open_meteo_cli collect
    uv run python -m spain_electricity_weather.collect.open_meteo_cli rebuild

`collect` queries the API and freezes the responses in data/raw/open_meteo/ (files already
present are kept). `rebuild` only reads the snapshot back, with no network call, and
prints the coverage obtained for each point.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from spain_electricity_weather.collect.open_meteo import (
    collect_open_meteo,
    load_daily_from_raw,
    load_hourly_from_raw,
)

DEFAULT_CONFIG = Path("config/weather_points.toml")
DEFAULT_RAW_DIR = Path("data/raw")


def coverage_summary(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    """One row per point: number of rows, first and last time step."""
    summary = df.groupby("point")[time_col].agg(["count", "min", "max"])
    return summary.rename(columns={"count": "rows", "min": "first", "max": "last"})


def collect(config_path: Path, raw_dir: Path) -> None:
    """Online collection; raw files already present are kept."""
    written = collect_open_meteo(config_path, raw_dir)
    print(f"{len(written)} raw files in {raw_dir / 'open_meteo'}")


def rebuild(raw_dir: Path) -> None:
    """Offline rebuild from the raw snapshot, with the coverage for each point."""
    hourly = load_hourly_from_raw(raw_dir)
    daily = load_daily_from_raw(raw_dir)
    print("Hourly weather (UTC):")
    print(coverage_summary(hourly, "time_utc").to_string())
    print("\nDaily climatology:")
    print(coverage_summary(daily, "date").to_string())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open-Meteo collection and offline rebuild")
    parser.add_argument("command", choices=["collect", "rebuild"])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "collect":
        collect(args.config, args.raw_dir)
    else:
        rebuild(args.raw_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
