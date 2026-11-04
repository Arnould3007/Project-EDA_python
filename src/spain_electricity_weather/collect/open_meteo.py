"""Open-Meteo collection (ERA5 archive): hourly weather 2023-2025 and daily climatology.

Two modes:
- `collect_open_meteo` calls the API and freezes the raw responses in `data/raw/open_meteo/`;
- `load_hourly_from_raw` / `load_daily_from_raw` rebuild the tables from that snapshot,
  with no network call at all (reproducibility requirement of the brief).

`quality_report` / `quality_report_from_raw` produce the quality check for each point.
"""

from __future__ import annotations

import json
import re
import time
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Without `models`, the API serves `best_match`, which mixes IFS 9 km (since 2017), ERA5 and
# ERA5-Land: the grid would change mid-series and the 1991-2025 comparison would be inconsistent.
MODEL = "era5"

# Fixed order: wind, radiation, temperature (`_impossible_counts` relies on it).
# Note: shortwave_radiation is an average over the preceding hour (value at 12:00 =
# 11:00-12:00), whereas wind and temperature are instantaneous. Nothing is shifted here:
# the shift is handled at the join.
HOURLY_VARIABLES: tuple[str, ...] = ("wind_speed_100m", "shortwave_radiation", "temperature_2m")
DAILY_VARIABLES: tuple[str, ...] = (
    "wind_speed_10m_mean",
    "shortwave_radiation_sum",
    "temperature_2m_mean",
)

# Expected units: any mismatch raises an error rather than silently skewing the analysis.
EXPECTED_HOURLY_UNITS: dict[str, str] = {
    "wind_speed_100m": "m/s",
    "shortwave_radiation": "W/m²",
    "temperature_2m": "°C",
}
EXPECTED_DAILY_UNITS: dict[str, str] = {
    "wind_speed_10m_mean": "m/s",
    "shortwave_radiation_sum": "MJ/m²",
    "temperature_2m_mean": "°C",
}
EXPECTED_TIME_UNIT = "iso8601"

HOURLY_START = "2023-01-01"
HOURLY_END = "2025-09-30"
# WMO 1991-2020 climate normal, extended to the end of the study period.
DAILY_START = "1991-01-01"
DAILY_END = "2025-09-30"

REQUEST_TIMEOUT_S = 60
MAX_ATTEMPTS = 5
# Waits of 5, 10, 20 then 40 s: the total (75 s) exceeds the API's one-minute window.
RETRY_BASE_WAIT_S = 5.0
RETRY_MAX_WAIT_S = 120.0

# Free API limits: 600 calls/min, 5,000/h, 10,000/day. A long request counts as several
# calls: weight = nLocations × (nDays / 14) × (nVariables / 10), i.e. about 21.5 for an
# hourly request and 272 for a daily request.
FREE_CALLS_PER_HOUR = 5000

# Maximum allowed gap between the requested point and the returned cell (ERA5 cell: 0.25°).
MAX_GRID_DISTANCE_DEG = 0.5

ALLOWED_ROLES = frozenset({"wind", "solar", "demand"})
# Mainland Spain: catches, among other things, a lat/lon swap in the configuration.
MAINLAND_LAT_RANGE = (36.0, 44.0)
MAINLAND_LON_RANGE = (-10.0, 4.0)

# The name is used as a file name: lowercase is required to avoid collisions on a
# case-insensitive file system (macOS).
POINT_NAME_PATTERN = re.compile(r"[a-z0-9_]+")

TEMPERATURE_MIN_C = -30.0
TEMPERATURE_MAX_C = 50.0
# Hours (UTC, end-of-averaged-hour label) when the sun stays below -13° for the whole hour,
# for all 7 points and all year round: any non-zero value there is therefore abnormal.
NIGHT_HOURS_UTC = frozenset({23, 0, 1, 2, 3})


@dataclass(frozen=True)
class KindSpec:
    """What distinguishes an hourly collection from a daily one."""

    variables: tuple[str, ...]
    units: dict[str, str]
    start: str
    end: str
    time_col: str


KINDS: dict[str, KindSpec] = {
    "hourly": KindSpec(
        HOURLY_VARIABLES, EXPECTED_HOURLY_UNITS, HOURLY_START, HOURLY_END, "time_utc"
    ),
    "daily": KindSpec(DAILY_VARIABLES, EXPECTED_DAILY_UNITS, DAILY_START, DAILY_END, "date"),
}


def _spec(kind: str) -> KindSpec:
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind!r}")
    return KINDS[kind]


@dataclass(frozen=True)
class WeatherPoint:
    """Representative weather point, read from `config/weather_points.toml`."""

    name: str
    lat: float
    lon: float
    role: str
    region: str


def _validate_point(point: WeatherPoint, config_path: Path) -> None:
    if POINT_NAME_PATTERN.fullmatch(point.name) is None:
        raise ValueError(f"Invalid point name in {config_path}: {point.name!r}")
    if point.role not in ALLOWED_ROLES:
        raise ValueError(f"{point.name}: unknown role {point.role!r} in {config_path}")
    lat_min, lat_max = MAINLAND_LAT_RANGE
    lon_min, lon_max = MAINLAND_LON_RANGE
    if not (lat_min <= point.lat <= lat_max and lon_min <= point.lon <= lon_max):
        raise ValueError(
            f"{point.name}: coordinates outside mainland Spain "
            f"({point.lat}, {point.lon}) in {config_path}"
        )


def load_points(config_path: Path) -> list[WeatherPoint]:
    """Read the list of weather points from the TOML configuration file."""
    with config_path.open("rb") as f:
        config = tomllib.load(f)
    points = [
        WeatherPoint(
            name=str(p["name"]),
            lat=float(p["lat"]),
            lon=float(p["lon"]),
            role=str(p["role"]),
            region=str(p["region"]),
        )
        for p in config.get("points", [])
    ]
    if not points:
        raise ValueError(f"No point defined in {config_path}")
    names = [p.name for p in points]
    if len(names) != len(set(names)):
        raise ValueError(f"Duplicate point names in {config_path}: {names}")
    for point in points:
        _validate_point(point, config_path)
    return points


def build_params(point: WeatherPoint, kind: str) -> dict[str, str | float]:
    """Request parameters for one point; `kind` is 'hourly' or 'daily'."""
    spec = _spec(kind)
    return {
        "latitude": point.lat,
        "longitude": point.lon,
        "start_date": spec.start,
        "end_date": spec.end,
        kind: ",".join(spec.variables),
        "models": MODEL,
        "timezone": "GMT",  # timestamps directly in UTC
        "wind_speed_unit": "ms",  # m/s instead of the default km/h
    }


def expected_times(kind: str) -> pd.DatetimeIndex:
    """Expected time axis: 24,096 UTC hours for hourly, 12,692 days for daily."""
    spec = _spec(kind)
    if kind == "hourly":
        return pd.date_range(f"{spec.start}T00:00", f"{spec.end}T23:00", freq="h", tz="UTC")
    return pd.date_range(spec.start, spec.end, freq="D")


def api_call_weight(kind: str) -> float:
    """Weight of a request for one point, using the formula published by Open-Meteo."""
    spec = _spec(kind)
    n_days = (pd.Timestamp(spec.end) - pd.Timestamp(spec.start)).days + 1
    return n_days / 14 * len(spec.variables) / 10


def _retry_wait(attempt: int, response: requests.Response | None) -> float:
    """Increasing wait, or the one requested by the server through the Retry-After header."""
    retry_after = response.headers.get("Retry-After", "") if response is not None else ""
    wait = float(retry_after) if retry_after.isdigit() else RETRY_BASE_WAIT_S * 2 ** (attempt - 1)
    return min(wait, RETRY_MAX_WAIT_S)


def fetch(params: dict[str, str | float], session: requests.Session) -> dict[str, Any]:
    """GET call, retried on 429, 5xx and network errors; other 4xx fail immediately."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response: requests.Response | None = None
        try:
            response = session.get(ARCHIVE_URL, params=params, timeout=REQUEST_TIMEOUT_S)
            status = response.status_code
            if status == 429 or status >= 500:
                last_error = requests.HTTPError(f"HTTP {status}", response=response)
            elif status >= 400:
                # The body contains Open-Meteo's `reason` field, useful for diagnosis.
                detail = f"HTTP {status}: {response.text[:200]}"
                raise requests.HTTPError(detail, response=response)
            else:
                payload: dict[str, Any] = response.json()
                return payload
        except (
            requests.ConnectionError,
            requests.Timeout,
            requests.exceptions.ChunkedEncodingError,  # response cut off mid-transfer
            requests.exceptions.JSONDecodeError,  # truncated body despite a 200 status
        ) as err:
            last_error = err
        if attempt < MAX_ATTEMPTS:
            time.sleep(_retry_wait(attempt, response))
    raise RuntimeError(
        f"Failed after {MAX_ATTEMPTS} attempts: run again later, "
        "files already frozen will not be requested again"
    ) from last_error


def raw_path(raw_dir: Path, kind: str, point: WeatherPoint) -> Path:
    return raw_dir / "open_meteo" / f"{kind}_{point.name}.json"


def build_record(
    payload: dict[str, Any],
    point: WeatherPoint,
    kind: str,
    params: dict[str, str | float],
) -> dict[str, Any]:
    """Pair the unchanged raw response with its `_meta` provenance block."""
    times = expected_times(kind)
    return {
        "_meta": {
            "source": "Open-Meteo Historical Weather API (ERA5 reanalysis)",
            "owner": "OpenMeteo GmbH (Switzerland); ERA5 data: ECMWF / Copernicus C3S",
            "url": ARCHIVE_URL,
            "access_method": "HTTP GET, public REST API with no key, JSON response",
            "params": params,
            "point": point.name,
            "role": point.role,
            "region": point.region,
            "requested_lat": point.lat,
            "requested_lon": point.lon,
            "extracted_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "licence": "CC BY 4.0 — attribution to Open-Meteo required",
            "underlying_data_attribution": (
                "Generated using Copernicus Climate Change Service information"
            ),
            "expected_coverage": {
                "start": times[0].isoformat(),
                "end": times[-1].isoformat(),
                "frequency": kind,
                "n_rows": len(times),
            },
        },
        "response": payload,
    }


def save_raw(
    payload: dict[str, Any],
    point: WeatherPoint,
    kind: str,
    params: dict[str, str | float],
    raw_dir: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Freeze the raw response, after checking its contract (units, UTC, coverage...).

    A frozen snapshot is never rewritten without `overwrite=True`. An invalid response raises
    an error and is not written. Writing goes through a temporary file: an interruption cannot
    leave a truncated JSON that would pass for a point already collected.
    """
    path = raw_path(raw_dir, kind, point)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Snapshot already frozen, not rewritten: {path}")
    record = build_record(payload, point, kind, params)
    check_coverage(parse_response(record, kind), kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    return path


def collect_open_meteo(config_path: Path, raw_dir: Path, *, overwrite: bool = False) -> list[Path]:
    """Online collection: one hourly call and one daily call per point.

    Files already frozen are skipped: after a quota refusal, simply run it again. The pause
    between two requests spreads the weighted load over the API's hourly budget. One line per
    file shows whether it was written or skipped, and the pause planned before the next call.
    """
    points = load_points(config_path)
    written: list[Path] = []
    pause_s = 0.0
    with requests.Session() as session:
        for point in points:
            for kind in KINDS:
                path = raw_path(raw_dir, kind, point)
                if path.exists() and not overwrite:
                    written.append(path)
                    print(f"skipped {path.name} (already frozen) — next pause: {pause_s:.1f} s")
                    continue
                time.sleep(pause_s)
                params = build_params(point, kind)
                payload = fetch(params, session)
                written.append(save_raw(payload, point, kind, params, raw_dir, overwrite=overwrite))
                pause_s = api_call_weight(kind) * 3600 / FREE_CALLS_PER_HOUR
                print(f"wrote {path.name} — next pause: {pause_s:.1f} s")
    return written


def parse_response(record: dict[str, Any], kind: str) -> pd.DataFrame:
    """Turn a raw file into a long table: one row = one point × one time step.

    `null` values become NaN and are kept: `quality_report` counts them. Time coverage is
    handled by `check_coverage`.
    """
    spec = _spec(kind)
    meta = record["_meta"]
    response = record["response"]
    point = meta["point"]

    if response.get("error"):
        raise ValueError(f"{point}: error returned by the API: {response.get('reason')!r}")
    if response.get("utc_offset_seconds") != 0:
        raise ValueError(f"{point}: non-UTC response ({response.get('utc_offset_seconds')})")
    units = response.get(f"{kind}_units", {})
    for var, unit in {"time": EXPECTED_TIME_UNIT, **spec.units}.items():
        if units.get(var) != unit:
            raise ValueError(f"{point}: unexpected unit for {var}: {units.get(var)!r}")

    block = response.get(kind, {})
    missing = [var for var in ("time", *spec.variables) if var not in block]
    if missing:
        raise ValueError(f"{point}: variables missing from the response: {missing}")
    lengths = {var: len(block[var]) for var in ("time", *spec.variables)}
    if len(set(lengths.values())) != 1:
        raise ValueError(f"{point}: series of different lengths {lengths}")

    # Coordinates actually returned: ERA5 snaps the request to the nearest grid cell.
    grid_lat = float(response["latitude"])
    grid_lon = float(response["longitude"])
    requested_lat = float(meta["requested_lat"])
    requested_lon = float(meta["requested_lon"])
    distance = max(abs(grid_lat - requested_lat), abs(grid_lon - requested_lon))
    if distance > MAX_GRID_DISTANCE_DEG:
        raise ValueError(
            f"{point}: cell ({grid_lat}, {grid_lon}) too far from the requested point "
            f"({requested_lat}, {requested_lon})"
        )

    times = pd.to_datetime(block["time"])
    # Explicit dtype: an all-null column would otherwise stay of type object.
    df = pd.DataFrame({var: pd.Series(block[var], dtype="float64") for var in spec.variables})
    df.insert(0, spec.time_col, times.tz_localize("UTC") if kind == "hourly" else times)
    df.insert(1, "point", point)
    df.insert(2, "role", meta["role"])
    df.insert(3, "region", meta["region"])
    df["grid_lat"] = grid_lat
    df["grid_lon"] = grid_lon
    df["requested_lat"] = requested_lat
    df["requested_lon"] = requested_lon
    return df


def _coverage_counts(times: pd.Series[Any], kind: str) -> dict[str, int]:
    expected = expected_times(kind)
    actual = pd.DatetimeIndex(times)
    return {
        "expected_rows": len(expected),
        "actual_rows": len(actual),
        "duplicate_rows": int(actual.duplicated().sum()),
        "missing_timestamps": len(expected.difference(actual)),
        "unexpected_timestamps": len(actual.difference(expected)),
    }


def _coverage_ok(counts: dict[str, int]) -> bool:
    return counts["expected_rows"] == counts["actual_rows"] and not (
        counts["duplicate_rows"] or counts["missing_timestamps"] or counts["unexpected_timestamps"]
    )


def check_coverage(df: pd.DataFrame, kind: str) -> None:
    """Raise an error if a point's table does not have exactly the expected time axis."""
    counts = _coverage_counts(df[_spec(kind).time_col], kind)
    if not _coverage_ok(counts):
        points = sorted(df["point"].unique())
        raise ValueError(f"{points}: incorrect {kind} coverage {counts}")


def _load_from_raw(raw_dir: Path, kind: str, *, strict: bool) -> pd.DataFrame:
    files = sorted((raw_dir / "open_meteo").glob(f"{kind}_*.json"))
    if not files:
        raise FileNotFoundError(f"No raw file '{kind}_*.json' in {raw_dir / 'open_meteo'}")
    frames = []
    for file in files:
        record = json.loads(file.read_text(encoding="utf-8"))
        if file.stem != f"{kind}_{record['_meta']['point']}":
            raise ValueError(f"{file.name}: contains point {record['_meta']['point']!r}")
        df = parse_response(record, kind)
        if kind == "daily":
            # Files frozen before the switch to 1991 cover 1940-2025: they remain valid and
            # untouched on disk, and only the table is restricted before the check.
            df = df[df["date"] >= pd.Timestamp(DAILY_START)]
        if strict:
            check_coverage(df, kind)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def load_hourly_from_raw(raw_dir: Path, *, strict: bool = True) -> pd.DataFrame:
    """Hourly weather for all points, rebuilt from the raw snapshot (offline).

    `strict=False` tolerates incorrect coverage, so that it can be described in the report.
    """
    return _load_from_raw(raw_dir, "hourly", strict=strict)


def load_daily_from_raw(raw_dir: Path, *, strict: bool = True) -> pd.DataFrame:
    """Daily climatology for all points, rebuilt from the raw snapshot.

    Only dates >= `DAILY_START` are kept, including for a longer frozen file.
    `strict=False` tolerates incorrect coverage, so that it can be described in the report.
    """
    return _load_from_raw(raw_dir, "daily", strict=strict)


def _impossible_counts(rows: pd.DataFrame, kind: str) -> dict[str, int]:
    """Count physically impossible values; a NaN value is never counted here."""
    spec = _spec(kind)
    wind, radiation, temperature = (rows[var] for var in spec.variables)
    out_of_range = (temperature < TEMPERATURE_MIN_C) | (temperature > TEMPERATURE_MAX_C)
    counts = {
        "negative_wind": int((wind < 0).sum()),
        "negative_radiation": int((radiation < 0).sum()),
        "temperature_out_of_range": int(out_of_range.sum()),
    }
    if kind == "hourly":  # not applicable to a daily total
        at_night = rows[spec.time_col].dt.hour.isin(NIGHT_HOURS_UTC)
        counts["night_radiation"] = int((radiation[at_night].dropna() != 0).sum())
    return counts


def quality_report(
    df: pd.DataFrame, kind: str, expected_points: Sequence[str] | None = None
) -> pd.DataFrame:
    """Quality check: one row per point, without modifying or filtering the data.

    Columns: expected and actual rows, duplicates, missing or unexpected timestamps, time
    bounds, NaN per variable (`nan_<variable>`), impossible values and `coverage_ok`.
    `expected_points` makes a missing point appear (0 rows); a point that is present but not
    expected is added at the end, with `is_expected_point=False`.
    """
    spec = _spec(kind)
    present = [str(name) for name in df["point"].unique()]
    expected = list(expected_points) if expected_points is not None else sorted(present)
    extras = sorted(set(present) - set(expected))

    rows: list[dict[str, Any]] = []
    for name in [*expected, *extras]:
        subset = df[df["point"] == name]
        times = subset[spec.time_col]
        counts = _coverage_counts(times, kind)
        rows.append(
            {
                "point": name,
                "kind": kind,
                **counts,
                "first_time": times.min(),
                "last_time": times.max(),
                **{f"nan_{var}": int(subset[var].isna().sum()) for var in spec.variables},
                **_impossible_counts(subset, kind),
                "is_expected_point": name in expected,
                "coverage_ok": _coverage_ok(counts) and name in expected,
            }
        )
    return pd.DataFrame(rows)


def quality_report_from_raw(raw_dir: Path, config_path: Path, kind: str) -> pd.DataFrame:
    """Report on the raw snapshot, offline, checked against the configured points."""
    df = _load_from_raw(raw_dir, kind, strict=False)
    return quality_report(df, kind, [point.name for point in load_points(config_path)])
