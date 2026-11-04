"""Tests for the rebuild command: offline rebuild and coverage summary."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from spain_electricity_weather.collect.open_meteo_cli import coverage_summary, run


def test_coverage_summary_counts_rows_per_point() -> None:
    times = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame({"point": ["a", "a", "b"], "time_utc": times})

    summary = coverage_summary(df, "time_utc")

    assert summary.loc["a", "rows"] == 2
    assert summary.loc["b", "rows"] == 1
    assert summary.loc["a", "first"] == times[0]


def test_rebuild_fails_clearly_without_snapshot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run(["rebuild", "--raw-dir", str(tmp_path)])
