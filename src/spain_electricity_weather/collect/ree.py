from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import date, timedelta
from itertools import groupby
from pathlib import Path
from typing import Any

import pandas as pd
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
            time.sleep(RETRY_DELAY_SECONDS * attempt)  # linear backoff: 5 s, then 10 s

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


def parse_hourly_series(raw_dir: Path) -> pd.DataFrame:
    """Build a clean hourly spot-price series from the frozen raw snapshots.

    From 2025 onward the raw files hold quarter-hour-spaced duplicates of
    the same hourly value (see PROVENANCE.md); keeping only the values that
    land on the hour mark collapses every month to one row per hour,
    regardless of whether that month's raw file has 24 or ~93 entries/day.
    Timestamps are converted to UTC so the series has no DST-related gap or
    duplicate around the March/October clock changes. Column name
    (`time_utc`) matches the Open-Meteo side, for a simpler join later.

    Negative and zero prices are real market outcomes (oversupply) and are
    kept, not filtered.
    """
    rows: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob("*.json")):
        days = json.loads(path.read_text(encoding="utf-8"))
        for day in days:
            for value in extract_spot_values(day["payload"]):
                timestamp = pd.Timestamp(value["datetime"])
                if timestamp.minute == 0:
                    rows.append({"time_utc": timestamp, "price_eur_mwh": value["value"]})

    frame = pd.DataFrame(rows)
    frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
    frame = frame.sort_values("time_utc").reset_index(drop=True)

    # A dedup alone (keep the first, drop the rest) would silently hide a
    # genuine conflict, e.g. the same hour priced twice differently across
    # two raw responses. Checked before any row is dropped, not after.
    conflicting = frame.groupby("time_utc")["price_eur_mwh"].nunique()
    conflicting = conflicting[conflicting > 1]
    if not conflicting.empty:
        raise ValueError(f"Conflicting prices for the same hour: {list(conflicting.index)}")

    frame = frame.drop_duplicates("time_utc").reset_index(drop=True)
    return frame
