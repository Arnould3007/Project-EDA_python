"""Commandes Open-Meteo : collecte en ligne et reconstruction hors ligne depuis le snapshot brut.

Usage, depuis la racine du projet :
    uv run python -m spain_electricity_weather.collect.open_meteo_cli collect
    uv run python -m spain_electricity_weather.collect.open_meteo_cli rebuild

`collect` interroge l'API et gèle les réponses dans data/raw/open_meteo/ (les fichiers déjà
présents sont conservés). `rebuild` relit uniquement le snapshot, sans appel réseau, et
affiche la couverture obtenue par point.
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
    """Une ligne par point : nombre de lignes, premier et dernier pas de temps."""
    summary = df.groupby("point")[time_col].agg(["count", "min", "max"])
    return summary.rename(columns={"count": "rows", "min": "first", "max": "last"})


def collect(config_path: Path, raw_dir: Path) -> None:
    """Collecte en ligne ; les fichiers bruts déjà présents sont conservés."""
    written = collect_open_meteo(config_path, raw_dir)
    print(f"{len(written)} raw files in {raw_dir / 'open_meteo'}")


def rebuild(raw_dir: Path) -> None:
    """Reconstruction hors ligne depuis le snapshot brut, avec la couverture par point."""
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
