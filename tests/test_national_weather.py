"""Tests for the national weather aggregation, on a small known dataset."""

from __future__ import annotations

import pandas as pd
import pytest

from spain_electricity_weather.integrate.national_weather import build_national_weather

# (point, role, region, wind, radiation, temperature)
POINTS = [
    ("soria", "wind", "Castilla y León", 6.0, 0.0, 10.0),
    ("lugo", "wind", "Galicia", 10.0, 0.0, 12.0),
    ("ecija", "solar", "Andalucía", 4.0, 500.0, 20.0),
    ("madrid", "demand", "Comunidad de Madrid", 2.0, 300.0, 18.0),
]


def _hourly(n_hours: int = 3) -> pd.DataFrame:
    times = pd.date_range("2024-04-01", periods=n_hours, freq="h", tz="UTC")
    rows = [
        {
            "time_utc": t,
            "point": p,
            "role": role,
            "region": region,
            "wind_speed_100m": wind,
            "shortwave_radiation": rad,
            "temperature_2m": temp,
        }
        for t in times
        for p, role, region, wind, rad, temp in POINTS
    ]
    return pd.DataFrame(rows)


def _capacity() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "region": ["Castilla y León", "Galicia", "Andalucía"],
            "technology": ["wind", "wind", "solar"],
            "year": [2024, 2024, 2024],
            "capacity_mw": [3000.0, 1000.0, 8000.0],
        }
    )


def test_one_row_per_hour_with_simple_means() -> None:
    national = build_national_weather(_hourly(n_hours=3))

    assert len(national) == 3  # grain: one row per hour, no loss and no duplication
    assert national["time_utc"].is_unique
    # Wind averages only the wind points, radiation only the solar points.
    assert national["wind_100m_ms"].iloc[0] == pytest.approx(8.0)
    assert national["solar_wm2"].iloc[0] == pytest.approx(500.0)
    # Temperature averages all 4 points.
    assert national["temp_c"].iloc[0] == pytest.approx(15.0)
    assert "wind_100m_ms_w" not in national.columns


def test_weighted_mean_uses_regional_capacity() -> None:
    national = build_national_weather(_hourly(), capacity=_capacity())

    # (6 x 3000 + 10 x 1000) / 4000 = 7.0, versus 8.0 for the simple mean
    assert national["wind_100m_ms_w"].iloc[0] == pytest.approx(7.0)
    assert national["solar_wm2_w"].iloc[0] == pytest.approx(500.0)


def test_missing_capacity_year_is_rejected() -> None:
    capacity = _capacity()
    capacity["year"] = 2023  # no capacity for 2024

    with pytest.raises(ValueError, match="wind capacity missing"):
        build_national_weather(_hourly(), capacity=capacity)


def test_hour_with_missing_point_is_rejected() -> None:
    hourly = _hourly()
    hourly = hourly.drop(hourly.index[0])  # soria missing in the first hour

    with pytest.raises(ValueError, match="hours without all 4 points"):
        build_national_weather(hourly)


def test_gap_in_hourly_grid_is_rejected() -> None:
    hourly = _hourly(n_hours=3)
    hourly = hourly[hourly["time_utc"] != hourly["time_utc"].unique()[1]]

    with pytest.raises(ValueError, match="missing hours"):
        build_national_weather(hourly)


def test_nan_value_is_rejected() -> None:
    hourly = _hourly()
    hourly.loc[0, "wind_speed_100m"] = float("nan")

    with pytest.raises(ValueError, match="missing values"):
        build_national_weather(hourly)
