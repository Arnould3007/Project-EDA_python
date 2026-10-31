"""Join REE hourly prices with the national weather aggregate.

PROVISIONAL: the radiation alignment below is our best-judgement call,
not yet confirmed with the Open-Meteo side. Flagged clearly so it is easy
to review or correct.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from spain_electricity_weather.collect.open_meteo import load_hourly_from_raw
from spain_electricity_weather.collect.ree import parse_hourly_series
from spain_electricity_weather.integrate.national_weather import build_national_weather


def align_radiation_to_hour_start(national_weather: pd.DataFrame) -> pd.DataFrame:
    """Shift solar radiation so it covers the same hour as the price and wind.

    REE's price at `time_utc` T applies to the hour [T, T+1) (day-ahead
    market convention). Open-Meteo's wind/temperature at T are instantaneous
    at T, already consistent with that. Its radiation at T is instead the
    *average over the previous hour*, [T-1, T) — so the value that actually
    covers [T, T+1) is the one timestamped at T+1. `shift(-1)` pulls that
    next row's value back onto the current one; the last row has nothing to
    shift in and becomes NaN (dropped at the join, not silently kept).
    """
    aligned = national_weather.sort_values("time_utc").reset_index(drop=True).copy()
    aligned["solar_wm2"] = aligned["solar_wm2"].shift(-1)
    return aligned


def join_price_weather(price: pd.DataFrame, national_weather: pd.DataFrame) -> dict[str, Any]:
    """Inner-join REE prices with the aligned national weather, by hour.

    Returns the joined table plus a report (row counts on each side, rows
    after the join, and which timestamps did not match or had to be
    dropped) rather than just the table, so the join's quality is visible,
    not assumed.
    """
    weather = align_radiation_to_hour_start(national_weather)

    joined = price.merge(weather, on="time_utc", how="inner", validate="one_to_one")

    # A matching time_utc key is not enough: the radiation shift leaves a
    # NaN on the last weather row, which would otherwise sit silently in
    # the output if that hour happened to still be inside price's range.
    has_null = joined.isna().any(axis=1)
    dropped_for_nulls = sorted(joined.loc[has_null, "time_utc"])
    table = joined.loc[~has_null].reset_index(drop=True)

    # By time_utc key, not by whether a value is present: a timestamp missing
    # solar_wm2 only because of the shift above is still a real weather hour
    # and must still show up as unmatched, not silently disappear from the
    # report along with it.
    price_times = set(price["time_utc"])
    weather_times = set(weather["time_utc"])

    report: dict[str, Any] = {
        "price_rows": len(price),
        "weather_rows": len(weather),
        "joined_rows": len(table),
        "price_only": sorted(price_times - weather_times),
        "weather_only": sorted(weather_times - price_times),
        "dropped_for_missing_values": dropped_for_nulls,
    }
    return {"table": table, "report": report}


def build_join(ree_raw_dir: Path, weather_raw_dir: Path) -> dict[str, Any]:
    """Load both raw snapshots and run the join. Pure plumbing, no new decisions."""
    price = parse_hourly_series(ree_raw_dir)
    national_weather = build_national_weather(load_hourly_from_raw(weather_raw_dir))
    return join_price_weather(price, national_weather)


def save_join(table: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_path, index=False)
