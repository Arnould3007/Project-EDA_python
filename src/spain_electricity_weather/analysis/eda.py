"""Exploratory analysis: how wind and sun relate to the Spanish day-ahead price.

Input: the joined hourly table from `integrate.price_weather` (one row per UTC
hour: `time_utc`, `price_eur_mwh`, `wind_100m_ms`, `solar_wm2`, `temp_c`).

Each function returns a small table that answers one part of the question:
- `hourly_profile`: the price shape over the day, by season (solar dip, F1);
- `price_by_wind_bin`: the price level by wind-speed bin (wind effect, F2);
- `weather_regression`: the weather effect once hour, month and year are held
  fixed, and how much fit weather adds (F2);
- `monthly_table` / `profile_spread_by_year` / `indexed_risk`: the monthly
  view an SME signs a contract on (F3).

All calendar grouping is done in Europe/Madrid local time, like the KPIs.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from spain_electricity_weather.analysis.kpi import (
    PROFILES,
    break_even_price,
    consumption_weight,
    monthly_break_even_price,
    sensitivity_comparison,
)

WEATHER_COLUMNS = ("wind_100m_ms", "solar_wm2", "temp_c")
SEASONS = {
    12: "winter",
    1: "winter",
    2: "winter",
    3: "spring",
    4: "spring",
    5: "spring",
    6: "summer",
    7: "summer",
    8: "summer",
    9: "autumn",
    10: "autumn",
    11: "autumn",
}


def _local(frame: pd.DataFrame) -> pd.Series:
    local: pd.Series = frame["time_utc"].dt.tz_convert("Europe/Madrid")
    return local


def hourly_profile(frame: pd.DataFrame) -> pd.DataFrame:
    """Mean price, share of zero-or-negative prices and mean radiation per
    local hour (0-23) and season. One row per hour x season."""
    local = _local(frame)
    working = pd.DataFrame(
        {
            "hour": local.dt.hour,
            "season": local.dt.month.map(SEASONS),
            "price_eur_mwh": frame["price_eur_mwh"],
            "non_positive": (frame["price_eur_mwh"] <= 0).astype("float64"),
            "solar_wm2": frame["solar_wm2"],
        }
    )
    grouped = working.groupby(["season", "hour"]).agg(
        mean_price_eur_mwh=("price_eur_mwh", "mean"),
        non_positive_share_pct=("non_positive", "mean"),
        mean_solar_wm2=("solar_wm2", "mean"),
        hours=("price_eur_mwh", "size"),
    )
    grouped["non_positive_share_pct"] *= 100
    return grouped.reset_index()


def price_by_wind_bin(frame: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    """Price by wind-speed quantile bin (equal number of hours per bin), on
    all hours. See `price_by_wind_bin_at_night` for the version that
    separates wind from the solar dip."""
    return _wind_bins(frame, bins)


def price_by_wind_bin_at_night(frame: pd.DataFrame, bins: int = 5) -> pd.DataFrame:
    """Same as `price_by_wind_bin`, on hours with no sun (radiation < 1 W/m2),
    so the solar dip cannot drive the result."""
    return _wind_bins(frame.loc[frame["solar_wm2"] < 1], bins)


def _wind_bins(frame: pd.DataFrame, bins: int) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("no hours to bin")
    bin_index = pd.qcut(frame["wind_100m_ms"], bins, labels=False, duplicates="drop")
    working = pd.DataFrame(
        {
            "wind_bin": bin_index,
            "wind_100m_ms": frame["wind_100m_ms"],
            "price_eur_mwh": frame["price_eur_mwh"],
        }
    )
    table = working.groupby("wind_bin").agg(
        mean_wind_ms=("wind_100m_ms", "mean"),
        min_wind_ms=("wind_100m_ms", "min"),
        max_wind_ms=("wind_100m_ms", "max"),
        mean_price_eur_mwh=("price_eur_mwh", "mean"),
        median_price_eur_mwh=("price_eur_mwh", "median"),
        hours=("price_eur_mwh", "size"),
    )
    return table.reset_index()


def _r_squared(y: np.ndarray, design: np.ndarray) -> tuple[np.ndarray, float]:
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    residuals = y - design @ coefficients
    total = float(((y - y.mean()) ** 2).sum())
    return coefficients, 1.0 - float((residuals**2).sum()) / total


def weather_regression(frame: pd.DataFrame) -> dict[str, float]:
    """OLS of price on wind, radiation and temperature, with hour-of-day,
    month and year fixed effects (local time).

    The fixed effects absorb the daily shape, the season and the 2023 gas
    cap, so each weather coefficient compares hours that share the same
    hour, month and year. Descriptive only: hourly residuals are strongly
    autocorrelated, so no standard errors or p-values are reported.

    Returns one coefficient per weather variable (EUR/MWh per unit), the R2
    with fixed effects only, and the R2 once weather is added.
    """
    local = _local(frame)
    calendar = pd.DataFrame(
        {
            "hour": local.dt.hour.astype(str),
            "month": local.dt.month.astype(str),
            "year": local.dt.year.astype(str),
        }
    )
    fixed_effects = pd.get_dummies(calendar, drop_first=True).astype("float64")
    fixed_effects.insert(0, "intercept", 1.0)
    y = frame["price_eur_mwh"].to_numpy(dtype="float64")

    _, r2_calendar = _r_squared(y, fixed_effects.to_numpy())
    design = pd.concat(
        [frame[list(WEATHER_COLUMNS)].reset_index(drop=True), fixed_effects.reset_index(drop=True)],
        axis=1,
    )
    coefficients, r2_full = _r_squared(y, design.to_numpy(dtype="float64"))

    result = {
        f"coef_{name}": float(coef)
        for name, coef in zip(WEATHER_COLUMNS, coefficients[: len(WEATHER_COLUMNS)], strict=True)
    }
    result["r2_calendar_only"] = r2_calendar
    result["r2_with_weather"] = r2_full
    return result


def monthly_table(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per local calendar month: break-even price of each profile,
    their spread (24/7 minus weekday-daytime) and mean weather."""
    month = _local(frame).dt.tz_localize(None).dt.to_period("M")
    weather = frame.groupby(month)[list(WEATHER_COLUMNS)].mean()
    table = pd.DataFrame({p: monthly_break_even_price(frame, p) for p in PROFILES})
    table = table.join(weather, validate="one_to_one")
    table["spread_eur_mwh"] = table["always_on"] - table["weekday_daytime"]
    table.index.name = "month"
    return table.reset_index()


COMPARABLE_MONTHS = tuple(range(1, 10))  # January-September, covered every year


def profile_spread_by_year(
    frame: pd.DataFrame, months: tuple[int, ...] = COMPARABLE_MONTHS
) -> pd.DataFrame:
    """Break-even price of each profile per local calendar year, and the
    spread between them: how much a daytime profile gains from the sun.

    Only `months` are kept (January-September by default): 2025 stops in
    September, and October-December is when the daytime profile gains least
    (short days, evening peak). Comparing full 2023-2024 with a partial 2025
    would inflate the 2025 spread on its own.
    """
    local = _local(frame)
    kept = local.dt.month.isin(months)
    frame = frame.loc[kept]
    year = local.loc[kept].dt.year
    rows = []
    for value, group in frame.groupby(year):
        row: dict[str, float] = {"year": float(value)}
        for profile in PROFILES:
            weight = consumption_weight(group["time_utc"], profile)
            row[profile] = break_even_price(group["price_eur_mwh"], weight)
        rows.append(row)
    table = pd.DataFrame(rows)
    table["year"] = table["year"].astype("int64")
    table["spread_eur_mwh"] = table["always_on"] - table["weekday_daytime"]
    return table


def windy_vs_calm_months(frame: pd.DataFrame) -> pd.DataFrame:
    """Per local year, months split at that year's median monthly wind into a
    calm and a windy half; mean 24/7 break-even price of each half.

    Splitting within each year keeps the 2023 gas cap and year-to-year price
    levels out of the comparison. A month exactly at the median counts as
    calm.
    """
    months = monthly_table(frame)
    months["year"] = months["month"].dt.year
    median_wind = months.groupby("year")["wind_100m_ms"].transform("median")
    months["half"] = np.where(months["wind_100m_ms"] > median_wind, "windy", "calm")
    table = months.pivot_table(
        index="year", columns="half", values="always_on", aggfunc="mean"
    ).reset_index()
    table.columns.name = None
    table["windy_minus_calm_eur_mwh"] = table["windy"] - table["calm"]
    return table


def indexed_risk(frame: pd.DataFrame) -> pd.DataFrame:
    """Per profile: the break-even fixed price over the full period and over
    2024-2025, and the spread of the monthly indexed price:
    standard deviation, cheapest month, 90th percentile and dearest month."""
    rows = []
    for profile in PROFILES:
        monthly = monthly_break_even_price(frame, profile)
        sensitivity = sensitivity_comparison(frame, profile)
        rows.append(
            {
                "profile": profile,
                "break_even_full_eur_mwh": sensitivity["full_period_break_even_eur_mwh"],
                "break_even_2024_2025_eur_mwh": sensitivity["2024_2025_break_even_eur_mwh"],
                "monthly_std_eur_mwh": float(monthly.std()),
                "monthly_min_eur_mwh": float(monthly.min()),
                "monthly_p90_eur_mwh": float(monthly.quantile(0.9)),
                "monthly_max_eur_mwh": float(monthly.max()),
                "months": float(len(monthly)),
            }
        )
    return pd.DataFrame(rows)


def run_eda(frame: pd.DataFrame, out_dir: Path) -> dict[str, Path]:
    """Compute every EDA table and write each one as CSV in `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    regression = pd.DataFrame([weather_regression(frame)])
    tables = {
        "hourly_profile": hourly_profile(frame),
        "price_by_wind_bin": price_by_wind_bin(frame),
        "price_by_wind_bin_night": price_by_wind_bin_at_night(frame),
        "weather_regression": regression,
        "monthly_table": monthly_table(frame),
        "profile_spread_by_year": profile_spread_by_year(frame),
        "windy_vs_calm_months": windy_vs_calm_months(frame),
        "indexed_risk": indexed_risk(frame),
    }
    written: dict[str, Path] = {}
    for name, table in tables.items():
        path = out_dir / f"{name}.csv"
        table.to_csv(path, index=False, float_format="%.3f")
        written[name] = path
    return written
