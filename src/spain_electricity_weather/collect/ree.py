from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import date, timedelta
from itertools import groupby
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://apidatos.ree.es/en/datos/mercados/precios-mercados-tiempo-real"
SPOT_SERIES_ID = "600"  # "Precio mercado spot": confirmed against a live API response
SPOT_SERIES_NAMES = {"Precio mercado spot", "Spot market price"}
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5.0
REQUEST_DELAY_SECONDS = 0.4


def fetch_day_raw(day: date) -> dict[str, Any]:
    """Call the REE API for one calendar day, retrying on transient errors."""
    params = {
        "start_date": f"{day.isoformat()}T00:00",
        "end_date": f"{day.isoformat()}T23:00",
        "time_trunc": "hour",
    }

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        response = requests.get(BASE_URL, params=params, timeout=30)
        try:
            payload: dict[str, Any] = response.json()
        except ValueError:
            response.raise_for_status()
            raise
        if "errors" not in payload:
            return payload
        last_error = RuntimeError(f"REE API error for {day}: {payload['errors']}")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS * attempt)  # exponential backoff

    assert last_error is not None
    raise last_error


def extract_spot_values(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the 'Precio mercado spot' series values out of a raw REE API response.

    The payload also contains the unrelated PVPC series (id 1001). Matching
    on id alone would silently mix the two if REE ever reused an id, so the
    series name is checked too: mixing PVPC into the spot price series would
    make every downstream price KPI wrong.
    """
    matches = [series for series in payload["included"] if series["id"] == SPOT_SERIES_ID]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one spot series (id={SPOT_SERIES_ID}), got {len(matches)}"
        )

    series = matches[0]
    if series["type"] not in SPOT_SERIES_NAMES:
        raise ValueError(
            f"Spot series name mismatch: expected one of {SPOT_SERIES_NAMES}, "
            f"got {series['type']!r}"
        )

    values: list[dict[str, Any]] = series["attributes"]["values"]
    return values


def daterange(start: date, end: date) -> Iterator[date]:
    """Yield each calendar day from start to end, inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def collect_raw(start: date, end: date, out_dir: Path) -> list[Path]:
    """Fetch every day in [start, end] and save one raw JSON file per month.

    Months already present in out_dir are skipped, so an interrupted run
    can be resumed without re-fetching days already saved.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for (year, month), days in groupby(daterange(start, end), key=lambda d: (d.year, d.month)):
        out_path = out_dir / f"{year:04d}-{month:02d}.json"
        if out_path.exists():
            written.append(out_path)
            continue

        month_payloads = []
        for day in days:
            payload = fetch_day_raw(day)
            month_payloads.append({"date": day.isoformat(), "payload": payload})
            time.sleep(REQUEST_DELAY_SECONDS)

        out_path.write_text(json.dumps(month_payloads), encoding="utf-8")
        written.append(out_path)
        print(f"saved {out_path} ({len(month_payloads)} days)")

    return written