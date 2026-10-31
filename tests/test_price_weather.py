from __future__ import annotations

import pandas as pd
import pytest

from spain_electricity_weather.integrate.price_weather import (
    align_radiation_to_hour_start,
    join_price_weather,
)


def _national_weather(times: list[str], wind: list[float], solar: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time_utc": pd.to_datetime(times, utc=True),
            "wind_100m_ms": wind,
            "solar_wm2": solar,
            "temp_c": [15.0] * len(times),
        }
    )


def test_align_radiation_pulls_the_next_hours_value_back() -> None:
    weather = _national_weather(
        ["2023-01-01T11:00Z", "2023-01-01T12:00Z", "2023-01-01T13:00Z"],
        wind=[1.0, 2.0, 3.0],
        solar=[100.0, 200.0, 300.0],
    )

    aligned = align_radiation_to_hour_start(weather)

    # Row "12:00" now holds the radiation that was originally at "13:00",
    # i.e. the measurement that actually covers [12:00, 13:00).
    assert aligned["solar_wm2"].iloc[:2].tolist() == [200.0, 300.0]
    assert pd.isna(aligned["solar_wm2"].iloc[2])  # nothing to shift in for the last row
    # Only radiation moves; wind and temperature stay as instantaneous readings.
    assert aligned["wind_100m_ms"].tolist() == [1.0, 2.0, 3.0]


def test_join_price_weather_matches_every_hour_when_ranges_align() -> None:
    times = ["2023-01-01T00:00Z", "2023-01-01T01:00Z", "2023-01-01T02:00Z"]
    price = pd.DataFrame(
        {"time_utc": pd.to_datetime(times, utc=True), "price_eur_mwh": [10.0, 20.0, 30.0]}
    )
    weather = _national_weather(times, wind=[1.0, 2.0, 3.0], solar=[100.0, 200.0, 300.0])

    result = join_price_weather(price, weather)

    # The last hour's radiation became NaN from the shift (nothing to pull
    # it from); even though its time_utc key matches on both sides, it is
    # dropped from the table, not kept with a hole in it.
    assert result["report"]["joined_rows"] == 2
    assert result["report"]["price_only"] == []
    assert result["report"]["weather_only"] == []
    assert result["report"]["dropped_for_missing_values"] == [pd.Timestamp("2023-01-01T02:00Z")]
    assert len(result["table"]) == 2
    assert not result["table"].isna().any().any()


def test_join_price_weather_reports_boundary_mismatch_without_dropping_it_silently() -> None:
    """Price starts one hour before weather; weather ends one hour after price —
    exactly the project's D23 boundary case, on a tiny example."""
    price = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(["2022-12-31T23:00Z", "2023-01-01T00:00Z"], utc=True),
            "price_eur_mwh": [5.0, 10.0],
        }
    )
    weather = _national_weather(
        ["2023-01-01T00:00Z", "2023-01-01T01:00Z"], wind=[1.0, 2.0], solar=[100.0, 200.0]
    )

    result = join_price_weather(price, weather)
    report = result["report"]

    assert report["price_only"] == [pd.Timestamp("2022-12-31T23:00Z")]
    assert report["weather_only"] == [pd.Timestamp("2023-01-01T01:00Z")]
    assert report["joined_rows"] == 1  # only 2023-01-01T00:00Z is in both


def test_join_price_weather_rejects_a_duplicated_hour() -> None:
    """A real join bug (duplicate key on either side) must fail loudly,
    not silently multiply rows."""
    price = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(["2023-01-01T00:00Z", "2023-01-01T00:00Z"], utc=True),
            "price_eur_mwh": [10.0, 11.0],
        }
    )
    weather = _national_weather(["2023-01-01T00:00Z"], wind=[1.0], solar=[100.0])

    with pytest.raises(pd.errors.MergeError):
        join_price_weather(price, weather)
