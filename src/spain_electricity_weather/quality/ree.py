from __future__ import annotations

from typing import Any

import pandas as pd


def check_hourly_coverage(
    frame: pd.DataFrame, expected_start: pd.Timestamp, expected_end: pd.Timestamp
) -> dict[str, Any]:
    """Compare a parsed hourly series against a complete hourly grid.

    `expected_start`/`expected_end` must be the project's own known period,
    not derived from the frame's own min/max: a frame built from a
    truncated snapshot would otherwise always look complete, since its own
    boundaries would silently become the "expected" ones.

    Reports row counts, duplicate timestamps, and any missing or
    out-of-range hour, so gaps or accidental row multiplication are caught
    before the series is used anywhere downstream, instead of silently
    producing a wrong KPI.
    """
    expected = pd.date_range(expected_start, expected_end, freq="h")
    actual = pd.DatetimeIndex(frame["time_utc"])

    return {
        "rows": len(frame),
        "expected_hours": len(expected),
        "duplicate_rows": int(frame["time_utc"].duplicated().sum()),
        "missing_hours": list(expected.difference(actual)),
        "unexpected_hours": list(actual.difference(expected)),
        "null_price_rows": int(frame["price_eur_mwh"].isna().sum()),
        "not_numeric_price": not pd.api.types.is_numeric_dtype(frame["price_eur_mwh"]),
    }
