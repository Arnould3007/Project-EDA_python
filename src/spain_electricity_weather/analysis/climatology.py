"""Climatology check (decision journal C4, D16, L5): was 2023-2025 atypical?

Three years of prices can't say on their own whether the study period was
unusually windy/calm or sunny/dull. This compares the daily weather during
the project's study window against the WMO 1991-2020 climate normal, per
point, so the main hourly analysis can be read in that context.
"""

from __future__ import annotations

import pandas as pd

# WMO 1991-2020 normal; end bounds are exclusive.
NORMAL_START = pd.Timestamp("1991-01-01")
NORMAL_END = pd.Timestamp("2021-01-01")
# The project's own study window (same as the hourly price x weather join).
SAMPLE_START = pd.Timestamp("2023-01-01")
SAMPLE_END = pd.Timestamp("2025-10-01")

DAILY_VARIABLES = ("wind_speed_10m_mean", "shortwave_radiation_sum", "temperature_2m_mean")


def climatology_comparison(daily: pd.DataFrame) -> pd.DataFrame:
    """Per point and variable: the 1991-2020 normal mean, the 2023-2025
    sample mean, and the sample's deviation from normal (%).

    `daily` is `load_daily_from_raw`'s output (one row per point x day).
    """
    normal_mask = daily["date"].between(NORMAL_START, NORMAL_END, inclusive="left")
    sample_mask = daily["date"].between(SAMPLE_START, SAMPLE_END, inclusive="left")

    variables = list(DAILY_VARIABLES)
    normal_means = daily.loc[normal_mask].groupby("point")[variables].mean()
    sample_means = daily.loc[sample_mask].groupby("point")[variables].mean()

    rows = []
    for var in DAILY_VARIABLES:
        comparison = pd.DataFrame(
            {"normal": normal_means[var], "sample_2023_2025": sample_means[var]}
        )
        comparison["deviation_pct"] = (
            (comparison["sample_2023_2025"] - comparison["normal"]) / comparison["normal"] * 100
        )
        comparison.insert(0, "variable", var)
        rows.append(comparison.reset_index())
    return pd.concat(rows, ignore_index=True)
