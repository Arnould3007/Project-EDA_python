from __future__ import annotations

import pandas as pd
import pytest

from spain_electricity_weather.analysis.kpi import (
    break_even_price,
    consumption_weight,
    monthly_break_even_price,
    sensitivity_comparison,
)


def _times(*iso: str) -> pd.Series:
    return pd.Series(pd.to_datetime(list(iso), utc=True))


def test_consumption_weight_weekday_daytime_flags_active_and_inactive_hours() -> None:
    # 2024-01-15 is a Monday, 2024-01-20 a Saturday, both in winter (CET, UTC+1).
    times = _times(
        "2024-01-15T09:00Z",  # Monday 10:00 Madrid -> active
        "2024-01-15T19:00Z",  # Monday 20:00 Madrid -> inactive (after 19:00)
        "2024-01-20T09:00Z",  # Saturday 10:00 Madrid -> inactive (weekend)
    )

    weight = consumption_weight(times, "weekday_daytime")

    assert weight.tolist() == [1, 0, 0]


def test_consumption_weight_always_on_is_all_ones() -> None:
    times = _times("2024-01-15T09:00Z", "2024-01-20T09:00Z")

    weight = consumption_weight(times, "always_on")

    assert weight.tolist() == [1, 1]


def test_consumption_weight_rejects_an_unknown_profile() -> None:
    times = _times("2024-01-15T09:00Z")

    with pytest.raises(ValueError, match="Unknown profile"):
        consumption_weight(times, "weekends_only")


def test_break_even_price_matches_a_hand_computed_weighted_average() -> None:
    price = pd.Series([10.0, 20.0, 30.0, 40.0])
    weight = pd.Series([1, 1, 0, 0])  # only the first two hours "consume"

    assert break_even_price(price, weight) == pytest.approx(15.0)  # (10 + 20) / 2


def test_break_even_price_raises_when_the_profile_never_runs() -> None:
    price = pd.Series([10.0, 20.0])
    weight = pd.Series([0, 0])

    with pytest.raises(ValueError, match="zero weight"):
        break_even_price(price, weight)


def test_monthly_break_even_price_has_one_row_per_calendar_month() -> None:
    frame = pd.DataFrame(
        {
            "time_utc": _times("2024-01-15T09:00Z", "2024-01-20T09:00Z", "2024-02-15T09:00Z"),
            "price_eur_mwh": [10.0, 20.0, 30.0],
        }
    )

    monthly = monthly_break_even_price(frame, "always_on")

    assert len(monthly) == 2  # January and February, not one row per input row
    assert [str(period) for period in monthly.index] == ["2024-01", "2024-02"]
    assert monthly.tolist() == pytest.approx([15.0, 30.0])


def test_sensitivity_comparison_isolates_the_2024_2025_window() -> None:
    frame = pd.DataFrame(
        {
            "time_utc": _times(
                "2023-06-15T09:00Z",  # excluded from the "2024-2025 only" window
                "2024-06-15T09:00Z",
                "2024-07-15T09:00Z",
            ),
            "price_eur_mwh": [100.0, 10.0, 20.0],
        }
    )

    result = sensitivity_comparison(frame, "always_on")

    assert result["full_period_break_even_eur_mwh"] == pytest.approx((100 + 10 + 20) / 3)
    assert result["2024_2025_break_even_eur_mwh"] == pytest.approx((10 + 20) / 2)
