"""National aggregation of hourly weather: one row per UTC hour.

Input: the long table produced by ``load_hourly_from_raw`` (one row = one point x one hour).
Output: a wide table indexed by ``time_utc``, ready for the join with REE prices.

Two versions coexist:
- the simple mean (default version, used in the main join);
- the mean weighted by the installed capacity of each point's region (sensitivity),
  computed only if the REE capacity table is provided.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from spain_electricity_weather.collect.open_meteo import load_hourly_from_raw

# National column -> (source Open-Meteo variable, role of the points used; None = all)
AGGREGATES: dict[str, tuple[str, str | None]] = {
    "wind_100m_ms": ("wind_speed_100m", "wind"),
    "solar_wm2": ("shortwave_radiation", "solar"),
    "temp_c": ("temperature_2m", None),
}

# Weightable columns -> REE technology whose capacity is used as the weight
WEIGHTED: dict[str, str] = {
    "wind_100m_ms": "wind",
    "solar_wm2": "solar",
}

CAPACITY_COLUMNS = ("region", "technology", "year", "capacity_mw")


def check_complete_grid(hourly: pd.DataFrame) -> None:
    """Reject any incomplete table: duplicate, hourly gap, missing point or NaN value."""
    if hourly.duplicated(["time_utc", "point"]).any():
        raise ValueError("duplicate (time_utc, point) rows in the hourly weather")

    variables = [var for var, _ in AGGREGATES.values()]
    n_missing = int(hourly[variables].isna().sum().sum())
    if n_missing:
        raise ValueError(f"{n_missing} missing values in the hourly weather")

    n_points = hourly["point"].nunique()
    points_per_hour = hourly.groupby("time_utc")["point"].nunique()
    incomplete = points_per_hour[points_per_hour != n_points]
    if not incomplete.empty:
        first = incomplete.index[0]
        raise ValueError(f"{len(incomplete)} hours without all {n_points} points (first: {first})")

    times = points_per_hour.index
    expected = pd.date_range(times.min(), times.max(), freq="h", tz="UTC")
    if len(times) != len(expected):
        raise ValueError(f"{len(expected) - len(times)} missing hours in the hourly grid")


def simple_national(hourly: pd.DataFrame) -> pd.DataFrame:
    """Simple mean of the points of each role, hour by hour."""
    out: dict[str, pd.Series] = {}
    for name, (var, role) in AGGREGATES.items():
        subset = hourly if role is None else hourly[hourly["role"] == role]
        if subset.empty:
            raise ValueError(f"no point with role {role!r} for {name}")
        out[name] = subset.groupby("time_utc")[var].mean()
    return pd.DataFrame(out)


def weighted_national(hourly: pd.DataFrame, capacity: pd.DataFrame) -> pd.DataFrame:
    """Mean weighted by the installed capacity (MW) of each point's region, per year.

    ``capacity``: one row per region x technology x year, columns ``CAPACITY_COLUMNS``.
    """
    missing_cols = set(CAPACITY_COLUMNS) - set(capacity.columns)
    if missing_cols:
        raise ValueError(f"missing columns in the capacity table: {sorted(missing_cols)}")
    if capacity.duplicated(["region", "technology", "year"]).any():
        raise ValueError("duplicate (region, technology, year) rows in the capacity table")

    out: dict[str, pd.Series] = {}
    for name, tech in WEIGHTED.items():
        var, role = AGGREGATES[name]
        subset = hourly.loc[hourly["role"] == role, ["time_utc", "region", var]].copy()
        subset["year"] = subset["time_utc"].dt.year
        caps = capacity.loc[capacity["technology"] == tech, ["region", "year", "capacity_mw"]]
        merged = subset.merge(caps, on=["region", "year"], how="left", validate="many_to_one")

        if len(merged) != len(subset):
            raise ValueError(f"{name}: the join with the capacity table changed the number of rows")
        no_cap = merged.loc[merged["capacity_mw"].isna(), ["region", "year"]].drop_duplicates()
        if not no_cap.empty:
            pairs = list(no_cap.itertuples(index=False, name=None))
            raise ValueError(f"{name}: {tech} capacity missing for {pairs}")
        if (merged["capacity_mw"] <= 0).any():
            raise ValueError(f"{name}: {tech} capacity is zero or negative")

        merged["weighted"] = merged[var] * merged["capacity_mw"]
        sums = merged.groupby("time_utc")[["weighted", "capacity_mw"]].sum()
        out[f"{name}_w"] = sums["weighted"] / sums["capacity_mw"]
    return pd.DataFrame(out)


def build_national_weather(
    hourly: pd.DataFrame, capacity: pd.DataFrame | None = None
) -> pd.DataFrame:
    """National table: simple mean, plus the weighted version if the capacity is provided."""
    check_complete_grid(hourly)
    national = simple_national(hourly)
    if capacity is not None:
        national = national.join(weighted_national(hourly, capacity), validate="one_to_one")
    national.index.name = "time_utc"
    return national.reset_index()


def build_from_raw(
    raw_dir: Path, out_path: Path, capacity: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Rebuild the national table from the raw snapshot and write it as Parquet."""
    national = build_national_weather(load_hourly_from_raw(raw_dir), capacity)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    national.to_parquet(out_path, index=False)
    return national
