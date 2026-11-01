"""Final pipeline step: rebuild the analytical outputs from the frozen raw snapshots.

Offline, no network: REE and Open-Meteo raw files -> joined hourly table
(read by Tableau) + join report + EDA tables + climatology comparison.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from spain_electricity_weather.analysis.climatology import climatology_comparison
from spain_electricity_weather.analysis.eda import run_eda
from spain_electricity_weather.collect.open_meteo import load_daily_from_raw
from spain_electricity_weather.integrate.price_weather import build_join, save_join

JOINED_FILE = "price_weather_hourly.csv"
REPORT_FILE = "join_report.json"
EDA_DIR = "eda"


def _report_for_json(report: dict[str, object]) -> dict[str, object]:
    """Timestamps -> ISO strings, so the join report can be saved as JSON."""
    clean: dict[str, object] = {}
    for key, value in report.items():
        if isinstance(value, list):
            clean[key] = [str(item) for item in value]
        else:
            clean[key] = value
    return clean


def build_analysis(ree_raw_dir: Path, weather_raw_dir: Path, out_dir: Path) -> pd.DataFrame:
    """Run the join, save it with its report, then write the EDA tables.

    Returns the joined table. Fails before writing anything if the join
    raises (duplicate key, broken snapshot): no half-updated outputs.
    """
    joined = build_join(ree_raw_dir, weather_raw_dir)
    table: pd.DataFrame = joined["table"]
    report = _report_for_json(joined["report"])

    out_dir.mkdir(parents=True, exist_ok=True)
    save_join(table, out_dir / JOINED_FILE)
    (out_dir / REPORT_FILE).write_text(json.dumps(report, indent=2), encoding="utf-8")

    eda_dir = out_dir / EDA_DIR
    run_eda(table, eda_dir)
    climatology = climatology_comparison(load_daily_from_raw(weather_raw_dir))
    climatology.to_csv(eda_dir / "climatology.csv", index=False, float_format="%.3f")

    print(
        f"joined {report['joined_rows']} hours "
        f"(price {report['price_rows']}, weather {report['weather_rows']}); "
        f"outputs in {out_dir}"
    )
    return table
