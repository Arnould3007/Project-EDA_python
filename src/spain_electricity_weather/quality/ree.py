from __future__ import annotations

from typing import Any

import pandas as pd


def check_hourly_coverage(frame: pd.DataFrame) -> dict[str, Any]:
    """Compare a parsed hourly series against a complete hourly grid.

    Reports row counts, duplicate timestamps and any missing hour, so gaps
    or accidental row multiplication are caught before the series is used
    anywhere downstream, instead of silently producing a wrong KPI.
    """
    expected = pd.date_range(frame["datetime"].min(), frame["datetime"].max(), freq="h")
    missing = expected.difference(pd.DatetimeIndex(frame["datetime"]))

    return {
        "rows": len(frame),
        "expected_hours": len(expected),
        "duplicate_rows": int(frame["datetime"].duplicated().sum()),
        "missing_hours": list(missing),
    }
