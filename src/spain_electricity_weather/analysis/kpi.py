"""Consumption-profile KPIs: break-even fixed price and monthly volatility.

Two consumption profiles (decision journal C6):
- "weekday_daytime": Mon-Fri 08:00-18:59, Europe/Madrid local time
- "always_on": every hour (24/7)

The break-even price (C5) is a weight-averaged spot price, which is
scale-invariant to the weights' absolute level: no separate normalisation
step is needed to compare the two profiles fairly in EUR/MWh.
"""

from __future__ import annotations

import pandas as pd

WEEKDAY_DAYTIME_START_HOUR = 8
WEEKDAY_DAYTIME_END_HOUR = 19  # exclusive: last active slot is 18:00-19:00
PROFILES = ("weekday_daytime", "always_on")


def consumption_weight(time_utc: pd.Series, profile: str) -> pd.Series:
    """0/1 weight per hour for `profile`, aligned to `time_utc`.

    Evaluated in Europe/Madrid local time, not UTC: "8am" means 8am for
    the SME, not 8am UTC (which would drift by 1-2h with the seasons).
    """
    if profile == "always_on":
        return pd.Series(1, index=time_utc.index, dtype="int64")
    if profile != "weekday_daytime":
        raise ValueError(f"Unknown profile: {profile!r}. Expected one of {PROFILES}")

    local = time_utc.dt.tz_convert("Europe/Madrid")
    is_weekday = local.dt.day_of_week < 5  # Monday=0 ... Sunday=6
    is_daytime = local.dt.hour.between(WEEKDAY_DAYTIME_START_HOUR, WEEKDAY_DAYTIME_END_HOUR - 1)
    weight: pd.Series = (is_weekday & is_daytime).astype("int64")
    return weight


def break_even_price(price: pd.Series, weight: pd.Series) -> float:
    """Fixed price at which a fixed contract costs the same as the indexed
    price, for the given consumption profile: total indexed cost divided
    by total energy, i.e. the weight-averaged spot price (EUR/MWh).
    """
    total_weight = weight.sum()
    if total_weight == 0:
        raise ValueError("Profile has zero weight over this period; cannot compute a price")
    return float((price * weight).sum() / total_weight)


def monthly_break_even_price(frame: pd.DataFrame, profile: str) -> pd.Series:
    """Break-even price per calendar month (Europe/Madrid), ~33 points over
    the project period: enough for a meaningful month-to-month volatility
    read (C7), unlike a 3-point annual series.
    """
    weight = consumption_weight(frame["time_utc"], profile)
    # tz-naive after this point on purpose: the Madrid offset has already
    # done its job (correct local calendar month), a Period has no tz.
    month = frame["time_utc"].dt.tz_convert("Europe/Madrid").dt.tz_localize(None).dt.to_period("M")
    working = pd.DataFrame({"price": frame["price_eur_mwh"], "weight": weight, "month": month})

    result: dict[object, float] = {}
    for period, group in working.groupby("month"):
        result[period] = break_even_price(group["price"], group["weight"])
    return pd.Series(result, name="break_even_price_eur_mwh")


def monthly_volatility(monthly_prices: pd.Series) -> float:
    """Std dev of the monthly break-even price (EUR/MWh): the risk measure
    behind C7, month-to-month, not a near-useless 3-point annual series."""
    return float(monthly_prices.std())


SENSITIVITY_SPLIT = pd.Timestamp("2024-01-01", tz="UTC")


def sensitivity_comparison(frame: pd.DataFrame, profile: str) -> dict[str, float]:
    """C8: full 2023-2025 sample vs 2024-2025 only.

    Spain's Iberian gas price cap mechanism covered 2023, so that year's
    day-ahead prices are not directly comparable to 2024-2025. Comparing
    both windows shows whether the full-period result is an artefact of
    including 2023, or holds regardless.
    """
    recent = frame.loc[frame["time_utc"] >= SENSITIVITY_SPLIT].reset_index(drop=True)

    full_weight = consumption_weight(frame["time_utc"], profile)
    recent_weight = consumption_weight(recent["time_utc"], profile)

    return {
        "full_period_break_even_eur_mwh": break_even_price(frame["price_eur_mwh"], full_weight),
        "2024_2025_break_even_eur_mwh": break_even_price(recent["price_eur_mwh"], recent_weight),
        "full_period_monthly_volatility": monthly_volatility(
            monthly_break_even_price(frame, profile)
        ),
        "2024_2025_monthly_volatility": monthly_volatility(
            monthly_break_even_price(recent, profile)
        ),
    }
