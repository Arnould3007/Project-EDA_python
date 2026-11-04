"""Climatology check: was 2023-2025 atypical?

Three years of prices can't say on their own whether the study period was
unusually windy/calm or sunny/dull. This compares the daily weather during
the project's study window against the WMO 1991-2020 climate normal, per
point, so the main hourly analysis can be read in that context.

Seasonal matching: the sample (Jan 2023 - Sep 2025) holds three
January-September seasons but only two October-December ones. A plain mean
over the sample over-weights spring and summer, which alone makes it look
sunnier and warmer than a plain annual normal. The comparison is therefore
made month by month: each sample month is compared with the normal of the
same calendar month, and the deviations are averaged.
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
# Celsius has an arbitrary zero: a % deviation would be meaningless, only the
# absolute deviation (degrees C) is reported for temperature.
NO_PERCENT_VARIABLES = frozenset({"temperature_2m_mean"})


def climatology_comparison(daily: pd.DataFrame) -> pd.DataFrame:
    """Per point and variable, on the same calendar months as the sample:
    the 1991-2020 normal, the 2023-2025 sample mean, the absolute deviation
    (sample - normal) and the deviation in % (NaN for temperature).

    `daily` is `load_daily_from_raw`'s output (one row per point x day).
    """
    variables = list(DAILY_VARIABLES)
    normal_mask = daily["date"].between(NORMAL_START, NORMAL_END, inclusive="left")
    sample_mask = daily["date"].between(SAMPLE_START, SAMPLE_END, inclusive="left")

    normal = daily.loc[normal_mask].assign(month=daily["date"].dt.month)
    monthly_normal = normal.groupby(["point", "month"])[variables].mean()

    sample = daily.loc[sample_mask].assign(period=daily["date"].dt.to_period("M"))
    sample_months = sample.groupby(["point", "period"])[variables].mean().reset_index()
    sample_months["month"] = sample_months["period"].dt.month

    keys = pd.MultiIndex.from_frame(sample_months[["point", "month"]])
    matched_normal = monthly_normal.reindex(keys)
    if matched_normal.isna().any().any():
        raise ValueError("a sample month has no 1991-2020 normal for the same point and month")
    matched_normal = matched_normal.reset_index()
    matched_normal["period"] = sample_months["period"]

    normal_means = matched_normal.groupby("point")[variables].mean()
    sample_means = sample_months.groupby("point")[variables].mean()

    rows = []
    for var in DAILY_VARIABLES:
        comparison = pd.DataFrame(
            {"normal": normal_means[var], "sample_2023_2025": sample_means[var]}
        )
        comparison["deviation"] = comparison["sample_2023_2025"] - comparison["normal"]
        comparison["deviation_pct"] = (
            float("nan")
            if var in NO_PERCENT_VARIABLES
            else comparison["deviation"] / comparison["normal"] * 100
        )
        comparison.insert(0, "variable", var)
        rows.append(comparison.reset_index())
    return pd.concat(rows, ignore_index=True)
