from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import spain_electricity_weather
from spain_electricity_weather.analysis import pipeline
from spain_electricity_weather.analysis.eda import (
    hourly_profile,
    indexed_risk,
    monthly_table,
    price_by_wind_bin,
    price_by_wind_bin_at_night,
    profile_spread_by_year,
    run_eda,
    weather_regression,
    windy_vs_calm_months,
)


def _frame(times: pd.DatetimeIndex, price: np.ndarray, wind: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time_utc": times,
            "price_eur_mwh": price,
            "wind_100m_ms": wind,
            "solar_wm2": np.zeros(len(times)),
            "temp_c": np.full(len(times), 15.0),
        }
    )


def _year_of_hours(seed: int = 0) -> pd.DataFrame:
    """Local year 2024, hourly, price = 100 - 5 x wind + a daily shape + noise."""
    rng = np.random.default_rng(seed)
    times = pd.date_range(
        "2024-01-01", "2024-12-31 23:00", freq="h", tz="Europe/Madrid"
    ).tz_convert("UTC")
    wind = rng.uniform(0, 12, len(times))
    shape = 10 * np.sin(np.arange(len(times)) * 2 * np.pi / 24)
    price = 100 - 5 * wind + shape + rng.normal(0, 1, len(times))
    frame = _frame(times, price, wind)
    frame["temp_c"] = rng.normal(15, 5, len(times))
    frame["solar_wm2"] = rng.uniform(0, 800, len(times))
    return frame


def test_hourly_profile_uses_madrid_local_hours() -> None:
    # 2024-07-01 12:00 UTC is 14:00 in Madrid (CEST, UTC+2).
    times = pd.DatetimeIndex(["2024-07-01T12:00Z", "2024-07-02T12:00Z"])
    frame = _frame(times, np.array([10.0, -2.0]), np.array([5.0, 5.0]))

    profile = hourly_profile(frame)

    assert profile["hour"].tolist() == [14]
    assert profile["season"].tolist() == ["summer"]
    assert profile["mean_price_eur_mwh"].iloc[0] == pytest.approx(4.0)
    assert profile["non_positive_share_pct"].iloc[0] == pytest.approx(50.0)


def test_wind_bins_keep_every_hour_and_order_by_wind() -> None:
    frame = _year_of_hours()

    table = price_by_wind_bin(frame, bins=10)

    assert table["hours"].sum() == len(frame)  # no hour lost or double-counted
    assert table["mean_wind_ms"].is_monotonic_increasing
    # Price = 100 - 5 x wind: windier bins must be cheaper.
    assert table["mean_price_eur_mwh"].is_monotonic_decreasing


def test_night_bins_only_use_hours_without_sun() -> None:
    times = pd.date_range("2024-01-01", periods=6, freq="h", tz="UTC")
    frame = _frame(times, np.arange(6.0), np.arange(6.0))
    frame["solar_wm2"] = [0.0, 0.0, 0.0, 300.0, 300.0, 300.0]

    table = price_by_wind_bin_at_night(frame, bins=3)

    assert table["hours"].sum() == 3
    assert table["max_wind_ms"].max() == pytest.approx(2.0)


def test_weather_regression_recovers_a_known_wind_effect() -> None:
    frame = _year_of_hours()

    result = weather_regression(frame)

    assert result["coef_wind_100m_ms"] == pytest.approx(-5.0, abs=0.05)
    assert result["coef_solar_wm2"] == pytest.approx(0.0, abs=0.01)
    assert result["r2_with_weather"] > result["r2_calendar_only"]


def test_monthly_table_spread_is_always_on_minus_weekday_daytime() -> None:
    # One Monday in January (CET): 10:00 Madrid at 20, 22:00 Madrid at 80.
    times = pd.DatetimeIndex(["2024-01-15T09:00Z", "2024-01-15T21:00Z"])
    frame = _frame(times, np.array([20.0, 80.0]), np.array([5.0, 5.0]))

    table = monthly_table(frame)

    assert len(table) == 1
    assert table["weekday_daytime"].iloc[0] == pytest.approx(20.0)  # only the 10:00 hour
    assert table["always_on"].iloc[0] == pytest.approx(50.0)
    assert table["spread_eur_mwh"].iloc[0] == pytest.approx(30.0)


def test_profile_spread_by_year_has_one_row_per_local_year() -> None:
    # 2023-12-31 23:00 UTC is already 2024 in Madrid.
    times = pd.DatetimeIndex(["2023-06-05T09:00Z", "2023-12-31T23:00Z", "2024-01-15T09:00Z"])
    frame = _frame(times, np.array([10.0, 30.0, 50.0]), np.array([5.0, 5.0, 5.0]))

    table = profile_spread_by_year(frame)

    assert table["year"].tolist() == [2023, 2024]
    assert table.loc[table["year"] == 2024, "always_on"].iloc[0] == pytest.approx(40.0)


def test_profile_spread_by_year_compares_the_same_months_every_year() -> None:
    """2025 stops in September: October-December of earlier years must be
    left out, or a partial year would be compared with full ones."""
    times = pd.DatetimeIndex(["2024-06-05T09:00Z", "2024-11-05T09:00Z", "2025-06-05T09:00Z"])
    frame = _frame(times, np.array([10.0, 500.0, 20.0]), np.array([5.0, 5.0, 5.0]))

    table = profile_spread_by_year(frame).set_index("year")

    assert table.loc[2024, "always_on"] == pytest.approx(10.0)  # November excluded


def test_windy_vs_calm_splits_months_within_each_year() -> None:
    # Four months of 2024, one hour each: the two windiest are the cheapest.
    times = pd.DatetimeIndex(
        ["2024-01-15T09:00Z", "2024-02-15T09:00Z", "2024-03-15T09:00Z", "2024-04-15T09:00Z"]
    )
    frame = _frame(times, np.array([100.0, 80.0, 40.0, 20.0]), np.array([2.0, 3.0, 8.0, 9.0]))

    table = windy_vs_calm_months(frame).set_index("year")

    assert table.loc[2024, "calm"] == pytest.approx(90.0)
    assert table.loc[2024, "windy"] == pytest.approx(30.0)
    assert table.loc[2024, "windy_minus_calm_eur_mwh"] == pytest.approx(-60.0)


def test_indexed_risk_reports_monthly_extremes() -> None:
    frame = _year_of_hours()

    risk = indexed_risk(frame).set_index("profile")

    assert set(risk.index) == {"weekday_daytime", "always_on"}
    assert risk.loc["always_on", "months"] == 12
    row = risk.loc["always_on"]
    assert row["monthly_min_eur_mwh"] <= row["monthly_p90_eur_mwh"] <= row["monthly_max_eur_mwh"]


def test_run_eda_writes_one_csv_per_table(tmp_path: Path) -> None:
    written = run_eda(_year_of_hours(), tmp_path)

    assert {path.name for path in written.values()} == {
        "hourly_profile.csv",
        "price_by_wind_bin.csv",
        "price_by_wind_bin_night.csv",
        "weather_regression.csv",
        "monthly_table.csv",
        "profile_spread_by_year.csv",
        "windy_vs_calm_months.csv",
        "indexed_risk.csv",
    }
    assert all(path.exists() for path in written.values())


def test_main_routes_build_analysis_with_default_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, Path, Path]] = []
    monkeypatch.setattr(
        spain_electricity_weather,
        "build_analysis",
        lambda ree, weather, out: calls.append((ree, weather, out)),
    )
    monkeypatch.setattr(sys, "argv", ["spain-electricity-weather", "build-analysis"])

    spain_electricity_weather.main()

    assert calls == [(Path("data/raw/ree"), Path("data/raw"), Path("data/processed"))]


def test_join_report_timestamps_are_saved_as_text() -> None:
    report = {"joined_rows": 2, "price_only": [pd.Timestamp("2022-12-31T23:00Z")]}

    clean = pipeline._report_for_json(report)

    assert json.loads(json.dumps(clean))["price_only"] == ["2022-12-31 23:00:00+00:00"]
