from __future__ import annotations

import pandas as pd
import pytest

from spain_electricity_weather.analysis.climatology import climatology_comparison


def test_climatology_comparison_reports_the_deviation_from_normal() -> None:
    # One point, two normal-period days (mean wind 4.0) and two sample-period
    # days (mean wind 6.0): a known +50% deviation to check against.
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["1995-01-01", "1995-01-02", "2024-01-01", "2024-01-02"]),
            "point": ["soria"] * 4,
            "wind_speed_10m_mean": [3.0, 5.0, 5.0, 7.0],
            "shortwave_radiation_sum": [10.0, 10.0, 10.0, 10.0],
            "temperature_2m_mean": [1.0, 1.0, 1.0, 1.0],
        }
    )

    result = climatology_comparison(daily)
    wind_row = result[(result["point"] == "soria") & (result["variable"] == "wind_speed_10m_mean")]

    assert wind_row["normal"].iloc[0] == pytest.approx(4.0)
    assert wind_row["sample_2023_2025"].iloc[0] == pytest.approx(6.0)
    assert wind_row["deviation"].iloc[0] == pytest.approx(2.0)
    assert wind_row["deviation_pct"].iloc[0] == pytest.approx(50.0)


def test_climatology_comparison_has_one_row_per_point_and_variable() -> None:
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["1995-01-01", "2024-01-01"] * 2),
            "point": ["soria", "soria", "lugo", "lugo"],
            "wind_speed_10m_mean": [3.0, 5.0, 4.0, 6.0],
            "shortwave_radiation_sum": [10.0, 12.0, 8.0, 9.0],
            "temperature_2m_mean": [1.0, 2.0, 0.0, 1.0],
        }
    )

    result = climatology_comparison(daily)

    assert len(result) == 6  # 2 points x 3 variables
    assert set(result["point"]) == {"soria", "lugo"}


def test_uneven_seasons_in_the_sample_do_not_create_a_fake_anomaly() -> None:
    """D24: a sample identical to the normal month by month, but holding two
    summer months for one winter month, must show no deviation. A plain mean
    over the sample would report it as sunnier than normal."""
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["1995-01-15", "1995-07-15", "2023-07-15", "2024-01-15", "2024-07-15"]
            ),
            "point": ["soria"] * 5,
            "wind_speed_10m_mean": [5.0] * 5,
            "shortwave_radiation_sum": [6.0, 30.0, 30.0, 6.0, 30.0],
            "temperature_2m_mean": [2.0, 24.0, 24.0, 2.0, 24.0],
        }
    )

    result = climatology_comparison(daily).set_index("variable")

    assert result.loc["shortwave_radiation_sum", "deviation_pct"] == pytest.approx(0.0)
    assert result.loc["temperature_2m_mean", "deviation"] == pytest.approx(0.0)


def test_temperature_has_no_percent_deviation() -> None:
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["1995-01-01", "2024-01-01"]),
            "point": ["soria", "soria"],
            "wind_speed_10m_mean": [4.0, 4.0],
            "shortwave_radiation_sum": [10.0, 10.0],
            "temperature_2m_mean": [2.0, 3.0],
        }
    )

    temp = climatology_comparison(daily).set_index("variable").loc["temperature_2m_mean"]

    assert temp["deviation"] == pytest.approx(1.0)  # +1 degree C
    assert pd.isna(temp["deviation_pct"])  # +50% would be meaningless in Celsius


def test_sample_month_without_a_normal_is_rejected() -> None:
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["1995-01-01", "2024-02-01"]),
            "point": ["soria", "soria"],
            "wind_speed_10m_mean": [4.0, 4.0],
            "shortwave_radiation_sum": [10.0, 10.0],
            "temperature_2m_mean": [2.0, 3.0],
        }
    )

    with pytest.raises(ValueError, match="no 1991-2020 normal"):
        climatology_comparison(daily)
