"""Tests for the Open-Meteo collection: reading the raw snapshot, with no network."""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from spain_electricity_weather.collect import open_meteo
from spain_electricity_weather.collect.open_meteo import (
    ARCHIVE_URL,
    MAX_ATTEMPTS,
    RETRY_MAX_WAIT_S,
    WeatherPoint,
    api_call_weight,
    build_params,
    build_record,
    check_coverage,
    collect_open_meteo,
    expected_times,
    fetch,
    load_daily_from_raw,
    load_hourly_from_raw,
    load_points,
    parse_response,
    quality_report,
    quality_report_from_raw,
    raw_path,
    save_raw,
)

POINT = WeatherPoint(name="soria", lat=41.77, lon=-2.47, role="wind", region="Castilla y León")
OTHER_POINT = WeatherPoint(name="lugo", lat=43.01, lon=-7.56, role="wind", region="Galicia")
CONFIG = Path(__file__).parents[1] / "config" / "weather_points.toml"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any attempt at a real connection makes the test fail."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Network calls are forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace `time.sleep` and record the requested waits."""
    recorded: list[float] = []
    monkeypatch.setattr(time, "sleep", recorded.append)
    return recorded


def _hourly_payload(n_hours: int = 3) -> dict[str, Any]:
    return {
        "latitude": 41.75,
        "longitude": -2.5,
        "utc_offset_seconds": 0,
        "hourly_units": {
            "time": "iso8601",
            "wind_speed_100m": "m/s",
            "shortwave_radiation": "W/m²",
            "temperature_2m": "°C",
        },
        "hourly": {
            "time": [f"2024-04-01T{h:02d}:00" for h in range(n_hours)],
            "wind_speed_100m": [7.5] * n_hours,
            "shortwave_radiation": [0.0] * n_hours,
            "temperature_2m": [2.9] * n_hours,
        },
    }


def _full_hourly_payload() -> dict[str, Any]:
    """Response covering exactly the expected period, with no anomaly at all."""
    times = expected_times("hourly").strftime("%Y-%m-%dT%H:%M").tolist()
    payload = _hourly_payload(0)
    payload["hourly"] = {
        "time": times,
        "wind_speed_100m": [7.5] * len(times),
        "shortwave_radiation": [0.0] * len(times),
        "temperature_2m": [2.9] * len(times),
    }
    return payload


def _full_daily_payload() -> dict[str, Any]:
    times = expected_times("daily").strftime("%Y-%m-%d").tolist()
    return {
        "latitude": 41.75,
        "longitude": -2.5,
        "utc_offset_seconds": 0,
        "daily_units": {
            "time": "iso8601",
            "wind_speed_10m_mean": "m/s",
            "shortwave_radiation_sum": "MJ/m²",
            "temperature_2m_mean": "°C",
        },
        "daily": {
            "time": times,
            "wind_speed_10m_mean": [3.4] * len(times),
            "shortwave_radiation_sum": [None, *[2.7] * (len(times) - 1)],
            "temperature_2m_mean": [0.8] * len(times),
        },
    }


def _full_daily_payload_at(point: WeatherPoint) -> dict[str, Any]:
    return {**_full_daily_payload(), "latitude": point.lat, "longitude": point.lon}


def _record(payload: dict[str, Any], point: WeatherPoint = POINT) -> dict[str, Any]:
    meta = {
        "point": point.name,
        "role": point.role,
        "region": point.region,
        "requested_lat": point.lat,
        "requested_lon": point.lon,
    }
    return {"_meta": meta, "response": payload}


def _response(
    status: int, body: bytes = b"{}", headers: dict[str, str] | None = None
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body  # only way to set a response body without a server
    response.headers.update(headers or {})
    return response


def _session(*outcomes: requests.Response | Exception) -> Mock:
    session = Mock(spec=requests.Session)
    session.get.side_effect = list(outcomes)
    return session


def _write_point_toml(path: Path, name: str, lat: float, lon: float, role: str) -> None:
    path.write_text(
        f'[[points]]\nname = "{name}"\nlat = {lat}\nlon = {lon}\nrole = "{role}"\nregion = "CyL"\n',
        encoding="utf-8",
    )


# --- Configuration ---------------------------------------------------------------------------


def test_project_config_loads() -> None:
    points = load_points(CONFIG)

    assert len(points) == 7
    assert {p.role for p in points} == {"wind", "solar", "demand"}


def test_load_points_rejects_duplicate_names(tmp_path: Path) -> None:
    config = tmp_path / "points.toml"
    block = 'name = "soria"\nlat = 41.77\nlon = -2.47\nrole = "wind"\nregion = "CyL"\n'
    config.write_text(f"[[points]]\n{block}\n[[points]]\n{block}", encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate point names"):
        load_points(config)


@pytest.mark.parametrize(
    ("name", "lat", "lon", "role", "message"),
    [
        ("Soria", 41.77, -2.47, "wind", "Invalid"),  # case collision on macOS
        ("../soria", 41.77, -2.47, "wind", "Invalid"),  # would escape data/raw
        ("soria", 41.77, -2.47, "hydro", "unknown role"),
        ("soria", -2.47, 41.77, "wind", "outside mainland Spain"),  # lat and lon swapped
    ],
)
def test_load_points_rejects_invalid_point(
    tmp_path: Path, name: str, lat: float, lon: float, role: str, message: str
) -> None:
    config = tmp_path / "points.toml"
    _write_point_toml(config, name, lat, lon, role)

    with pytest.raises(ValueError, match=message):
        load_points(config)


def test_load_points_rejects_empty_config(tmp_path: Path) -> None:
    config = tmp_path / "points.toml"
    config.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="No point"):
        load_points(config)


# --- Parameters, expected period and quotas ---------------------------------------------------


def test_archive_endpoint() -> None:
    assert ARCHIVE_URL == "https://archive-api.open-meteo.com/v1/archive"


@pytest.mark.parametrize(
    ("kind", "start", "variables"),
    [
        ("hourly", "2023-01-01", "wind_speed_100m,shortwave_radiation,temperature_2m"),
        ("daily", "1991-01-01", "wind_speed_10m_mean,shortwave_radiation_sum,temperature_2m_mean"),
    ],
)
def test_build_params(kind: str, start: str, variables: str) -> None:
    params = build_params(POINT, kind)

    assert params["timezone"] == "GMT"
    assert params["wind_speed_unit"] == "ms"
    assert params["models"] == "era5"  # otherwise best_match mixes IFS, ERA5 and ERA5-Land
    assert params["start_date"] == start
    assert params["end_date"] == "2025-09-30"
    assert params[kind] == variables
    assert (params["latitude"], params["longitude"]) == (POINT.lat, POINT.lon)


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        build_params(POINT, "monthly")
    with pytest.raises(ValueError, match="unknown kind"):
        parse_response(_record(_hourly_payload()), "monthly")


def test_expected_hourly_axis() -> None:
    times = expected_times("hourly")

    assert len(times) == 24_096
    assert times[0] == pd.Timestamp("2023-01-01T00:00", tz="UTC")
    assert times[-1] == pd.Timestamp("2025-09-30T23:00", tz="UTC")


def test_expected_daily_axis() -> None:
    times = expected_times("daily")

    assert len(times) == 12_692
    assert times[0] == pd.Timestamp("1991-01-01")
    assert times[-1] == pd.Timestamp("2025-09-30")


def test_api_call_weight_follows_published_formula() -> None:
    assert api_call_weight("hourly") == pytest.approx(1004 / 14 * 3 / 10)
    assert api_call_weight("daily") == pytest.approx(12_692 / 14 * 3 / 10)


# --- fetch : retries ---------------------------------------------------------------------------


def test_fetch_returns_payload_without_waiting(sleeps: list[float]) -> None:
    session = _session(_response(200, b'{"latitude": 41.75}'))

    assert fetch({"latitude": 41.77}, session) == {"latitude": 41.75}
    assert session.get.call_count == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_fetch_retries_429_and_5xx_with_growing_wait(status: int, sleeps: list[float]) -> None:
    session = _session(_response(status), _response(status), _response(200, b'{"ok": true}'))

    assert fetch({}, session) == {"ok": True}
    assert session.get.call_count == 3
    assert sleeps == [5.0, 10.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_fetch_does_not_retry_other_4xx(status: int, sleeps: list[float]) -> None:
    body = b'{"error": true, "reason": "Cannot initialize"}'
    session = _session(_response(status, body))

    with pytest.raises(requests.HTTPError, match="Cannot initialize"):
        fetch({}, session)
    assert session.get.call_count == 1
    assert sleeps == []


def test_fetch_gives_up_without_waiting_after_last_attempt(sleeps: list[float]) -> None:
    session = _session(*[_response(429) for _ in range(MAX_ATTEMPTS)])

    with pytest.raises(RuntimeError, match="Failed after"):
        fetch({}, session)
    assert session.get.call_count == MAX_ATTEMPTS
    assert sleeps == [5.0, 10.0, 20.0, 40.0]
    assert sum(sleeps) > 60  # the API's one-minute window is necessarily exceeded


def test_fetch_honours_retry_after_up_to_a_cap(sleeps: list[float]) -> None:
    session = _session(
        _response(429, headers={"Retry-After": "61"}),
        _response(429, headers={"Retry-After": "3600"}),
        _response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        _response(200),
    )

    fetch({}, session)

    assert sleeps == [61.0, RETRY_MAX_WAIT_S, 20.0]


@pytest.mark.parametrize(
    "failure",
    [
        requests.ConnectionError("coupure"),
        requests.Timeout("timeout"),
        requests.exceptions.ChunkedEncodingError("response cut off"),
        _response(200, b'{"hourly": [1, 2'),  # truncated body despite a 200 status
    ],
)
def test_fetch_retries_transport_failures(
    failure: requests.Response | Exception, sleeps: list[float]
) -> None:
    session = _session(failure, _response(200, b'{"ok": true}'))

    assert fetch({}, session) == {"ok": True}
    assert sleeps == [5.0]


# --- parse_response: response contract -----------------------------------------------------------


def test_parse_hourly_is_utc_and_keeps_grid_and_requested_coordinates() -> None:
    df = parse_response(_record(_hourly_payload()), "hourly")

    assert len(df) == 3
    assert str(df["time_utc"].dt.tz) == "UTC"
    assert df["time_utc"].iloc[0] == pd.Timestamp("2024-04-01T00:00", tz="UTC")
    # The returned coordinates (ERA5 cell) are kept, in addition to the requested ones.
    assert df["grid_lat"].iloc[0] == 41.75
    assert df["grid_lon"].iloc[0] == -2.5
    assert df["requested_lat"].iloc[0] == POINT.lat
    assert df["requested_lon"].iloc[0] == POINT.lon


def test_parse_does_not_shift_radiation() -> None:
    payload = _hourly_payload(13)
    payload["hourly"]["shortwave_radiation"][12] = 761.0

    df = parse_response(_record(payload), "hourly")

    noon = df[df["time_utc"] == pd.Timestamp("2024-04-01T12:00", tz="UTC")]
    assert noon["shortwave_radiation"].item() == 761.0


@pytest.mark.parametrize("variable", ["wind_speed_100m", "shortwave_radiation", "temperature_2m"])
def test_parse_rejects_unexpected_unit(variable: str) -> None:
    payload = _hourly_payload()
    payload["hourly_units"][variable] = "km/h"  # e.g. forgetting wind_speed_unit=ms

    with pytest.raises(ValueError, match="unexpected unit"):
        parse_response(_record(payload), "hourly")


def test_parse_rejects_unexpected_daily_unit() -> None:
    payload = _full_daily_payload()
    payload["daily_units"]["shortwave_radiation_sum"] = "kWh/m²"

    with pytest.raises(ValueError, match="unexpected unit"):
        parse_response(_record(payload), "daily")


def test_parse_rejects_non_iso_time_unit() -> None:
    payload = _hourly_payload()
    payload["hourly_units"]["time"] = "unixtime"

    with pytest.raises(ValueError, match="unexpected unit for time"):
        parse_response(_record(payload), "hourly")


def test_parse_rejects_non_utc_response() -> None:
    payload = _hourly_payload()
    payload["utc_offset_seconds"] = 7200  # response in Spanish local time

    with pytest.raises(ValueError, match="non-UTC"):
        parse_response(_record(payload), "hourly")


def test_parse_rejects_series_of_different_lengths() -> None:
    payload = _hourly_payload()
    payload["hourly"]["temperature_2m"] = [2.9, 3.0]  # one value missing

    with pytest.raises(ValueError, match="different lengths"):
        parse_response(_record(payload), "hourly")


def test_parse_rejects_variable_series_shorter_than_time_only() -> None:
    payload = _hourly_payload()
    for variable in ("wind_speed_100m", "shortwave_radiation", "temperature_2m"):
        payload["hourly"][variable] = [1.0, 2.0]  # consistent with each other, not with `time`

    with pytest.raises(ValueError, match="different lengths"):
        parse_response(_record(payload), "hourly")


def test_parse_rejects_missing_variable() -> None:
    payload = _hourly_payload()
    del payload["hourly"]["temperature_2m"]

    with pytest.raises(ValueError, match="variables missing"):
        parse_response(_record(payload), "hourly")


def test_parse_rejects_api_error_body() -> None:
    payload = {"error": True, "reason": "Data corrupted"}

    with pytest.raises(ValueError, match="Data corrupted"):
        parse_response(_record(payload), "hourly")


def test_parse_rejects_grid_cell_far_from_requested_point() -> None:
    with pytest.raises(ValueError, match="too far"):
        parse_response(_record(_hourly_payload(), OTHER_POINT), "hourly")


def test_parse_keeps_null_as_nan() -> None:
    payload = _hourly_payload()
    payload["hourly"]["shortwave_radiation"][1] = None

    df = parse_response(_record(payload), "hourly")

    assert len(df) == 3
    assert df["shortwave_radiation"].isna().sum() == 1


def test_parse_entirely_null_column_stays_numeric() -> None:
    payload = _hourly_payload()
    payload["hourly"]["wind_speed_100m"] = [None, None, None]

    df = parse_response(_record(payload), "hourly")

    assert df["wind_speed_100m"].dtype == "float64"
    assert df["wind_speed_100m"].isna().all()


# --- Couverture --------------------------------------------------------------------------------


def test_check_coverage_accepts_exact_axis() -> None:
    check_coverage(parse_response(_record(_full_hourly_payload()), "hourly"), "hourly")
    check_coverage(parse_response(_record(_full_daily_payload()), "daily"), "daily")


def _drop_row(payload: dict[str, Any], kind: str, index: int) -> None:
    for series in payload[kind].values():
        del series[index]


def _duplicate_row(payload: dict[str, Any], kind: str, index: int) -> None:
    for series in payload[kind].values():
        series[index] = series[index - 1]


@pytest.mark.parametrize("index", [0, 5000, -1])
def test_check_coverage_rejects_missing_hour(index: int) -> None:
    payload = _full_hourly_payload()
    _drop_row(payload, "hourly", index)

    with pytest.raises(ValueError, match="incorrect hourly coverage"):
        check_coverage(parse_response(_record(payload), "hourly"), "hourly")


def test_check_coverage_rejects_duplicate_even_with_right_row_count() -> None:
    payload = _full_hourly_payload()
    _duplicate_row(payload, "hourly", 100)  # 24,096 rows, but one duplicate and one gap

    with pytest.raises(ValueError, match="'duplicate_rows': 1"):
        check_coverage(parse_response(_record(payload), "hourly"), "hourly")


def test_check_coverage_rejects_local_time_axis() -> None:
    payload = _full_hourly_payload()
    payload["hourly"]["time"] = (
        (expected_times("hourly") + pd.Timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M").tolist()
    )

    with pytest.raises(ValueError, match="incorrect hourly coverage"):
        check_coverage(parse_response(_record(payload), "hourly"), "hourly")


def test_check_coverage_rejects_missing_day() -> None:
    payload = _full_daily_payload()
    _drop_row(payload, "daily", -1)

    with pytest.raises(ValueError, match="incorrect daily coverage"):
        check_coverage(parse_response(_record(payload), "daily"), "daily")


# --- Snapshot brut -----------------------------------------------------------------------------


def test_rebuild_from_raw_round_trip(tmp_path: Path) -> None:
    params = build_params(POINT, "hourly")
    save_raw(_full_hourly_payload(), POINT, "hourly", params, tmp_path)

    df = load_hourly_from_raw(tmp_path)

    assert len(df) == 24_096
    assert set(df["point"]) == {"soria"}
    assert not df.duplicated(subset=["point", "time_utc"]).any()


def test_daily_round_trip_keeps_null_radiation_as_nan(tmp_path: Path) -> None:
    save_raw(_full_daily_payload(), POINT, "daily", build_params(POINT, "daily"), tmp_path)

    df = load_daily_from_raw(tmp_path)

    assert len(df) == 12_692
    assert df["shortwave_radiation_sum"].isna().sum() == 1
    assert pd.isna(df["shortwave_radiation_sum"].iloc[0])


def test_save_raw_freezes_response_with_provenance(tmp_path: Path) -> None:
    payload = _full_hourly_payload()
    params = build_params(POINT, "hourly")

    path = save_raw(payload, POINT, "hourly", params, tmp_path)

    assert path == tmp_path / "open_meteo" / "hourly_soria.json"
    frozen = json.loads(path.read_text(encoding="utf-8"))
    assert frozen["response"] == payload  # raw response unchanged
    meta = frozen["_meta"]
    assert "OpenMeteo GmbH" in meta["owner"]
    assert meta["url"] == ARCHIVE_URL
    assert "GET" in meta["access_method"]
    assert meta["extracted_at_utc"].endswith("+00:00")
    assert "CC BY 4.0" in meta["licence"]
    assert "Open-Meteo" in meta["licence"]
    assert "Copernicus" in meta["underlying_data_attribution"]
    assert meta["params"] == params
    assert (meta["requested_lat"], meta["requested_lon"]) == (POINT.lat, POINT.lon)
    assert meta["expected_coverage"] == {
        "start": "2023-01-01T00:00:00+00:00",
        "end": "2025-09-30T23:00:00+00:00",
        "frequency": "hourly",
        "n_rows": 24_096,
    }


def test_save_raw_never_overwrites_frozen_snapshot_by_default(tmp_path: Path) -> None:
    params = build_params(POINT, "hourly")
    path = save_raw(_full_hourly_payload(), POINT, "hourly", params, tmp_path)
    original = path.read_bytes()

    with pytest.raises(FileExistsError, match="already frozen"):
        save_raw(_full_hourly_payload(), POINT, "hourly", params, tmp_path)
    assert path.read_bytes() == original

    changed = _full_hourly_payload()
    changed["hourly"]["temperature_2m"][0] = 9.9
    save_raw(changed, POINT, "hourly", params, tmp_path, overwrite=True)
    assert path.read_bytes() != original


def test_save_raw_writes_nothing_for_invalid_response(tmp_path: Path) -> None:
    payload = _full_hourly_payload()
    payload["hourly_units"]["wind_speed_100m"] = "km/h"
    params = build_params(POINT, "hourly")

    with pytest.raises(ValueError, match="unexpected unit"):
        save_raw(payload, POINT, "hourly", params, tmp_path)
    assert not list(tmp_path.rglob("*.json*"))


def test_save_raw_rejects_incomplete_response(tmp_path: Path) -> None:
    params = build_params(POINT, "hourly")

    with pytest.raises(ValueError, match="coverage"):
        save_raw(_hourly_payload(24), POINT, "hourly", params, tmp_path)
    assert not list(tmp_path.rglob("*.json*"))


def test_load_rejects_file_whose_content_is_another_point(tmp_path: Path) -> None:
    params = build_params(POINT, "hourly")
    path = save_raw(_full_hourly_payload(), POINT, "hourly", params, tmp_path)
    path.rename(path.with_name("hourly_lugo.json"))

    with pytest.raises(ValueError, match="contains point 'soria'"):
        load_hourly_from_raw(tmp_path)


def test_load_without_snapshot_fails_explicitly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No raw file"):
        load_daily_from_raw(tmp_path)


def _freeze_altered_snapshot(payload: dict[str, Any], raw_dir: Path) -> None:
    """Write a raw file without going through the checks of `save_raw`."""
    params = build_params(POINT, "hourly")
    path = raw_path(raw_dir, "hourly", POINT)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = build_record(payload, POINT, "hourly", params)
    path.write_text(json.dumps(record), encoding="utf-8")


def test_load_is_strict_by_default_on_coverage(tmp_path: Path) -> None:
    _freeze_altered_snapshot(_hourly_payload(24), tmp_path)

    with pytest.raises(ValueError, match="coverage"):
        load_hourly_from_raw(tmp_path)
    assert len(load_hourly_from_raw(tmp_path, strict=False)) == 24


def _freeze_legacy_daily(raw_dir: Path, dropped_day: str | None = None) -> Path:
    """Write a daily file frozen before the switch to 1991: it covers 1940-2025."""
    days = pd.date_range("1940-01-01", "2025-09-30", freq="D").strftime("%Y-%m-%d").tolist()
    if dropped_day is not None:
        days.remove(dropped_day)
    payload = _full_daily_payload()
    payload["daily"] = {
        "time": days,
        "wind_speed_10m_mean": [3.4] * len(days),
        "shortwave_radiation_sum": [None, *[2.7] * (len(days) - 1)],  # null du 1940-01-01
        "temperature_2m_mean": [0.8] * len(days),
    }
    path = raw_path(raw_dir, "daily", POINT)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = build_record(payload, POINT, "daily", build_params(POINT, "daily"))
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_load_daily_filters_legacy_file_without_touching_it(tmp_path: Path) -> None:
    path = _freeze_legacy_daily(tmp_path)
    frozen = path.read_bytes()

    df = load_daily_from_raw(tmp_path)

    assert len(df) == 12_692
    assert df["date"].iloc[0] == pd.Timestamp("1991-01-01")
    assert df["date"].iloc[-1] == pd.Timestamp("2025-09-30")
    assert pd.Timestamp("1990-12-31") not in set(df["date"])
    assert df.index.tolist() == list(range(12_692))
    assert path.read_bytes() == frozen


def test_load_daily_accepts_mix_of_legacy_and_new_files(tmp_path: Path) -> None:
    _freeze_legacy_daily(tmp_path)
    save_raw(
        _full_daily_payload_at(OTHER_POINT),
        OTHER_POINT,
        "daily",
        build_params(OTHER_POINT, "daily"),
        tmp_path,
    )

    df = load_daily_from_raw(tmp_path)

    assert df.groupby("point").size().to_dict() == {"lugo": 12_692, "soria": 12_692}


def test_load_daily_filter_does_not_hide_a_gap_after_1991(tmp_path: Path) -> None:
    _freeze_legacy_daily(tmp_path, dropped_day="2000-06-15")

    with pytest.raises(ValueError, match="'missing_timestamps': 1"):
        load_daily_from_raw(tmp_path)


def test_load_hourly_keeps_all_rows(tmp_path: Path) -> None:
    _freeze_altered_snapshot(_hourly_payload(24), tmp_path)

    assert len(load_hourly_from_raw(tmp_path, strict=False)) == 24


# --- Collection, with no network thanks to a fake `fetch` ---------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "points.toml"
    _write_point_toml(path, POINT.name, POINT.lat, POINT.lon, POINT.role)
    return path


def _fake_fetch(calls: list[dict[str, str | float]]) -> Any:
    def fetch_stub(params: dict[str, str | float], _session: object) -> dict[str, Any]:
        calls.append(params)
        return _full_hourly_payload() if "hourly" in params else _full_daily_payload()

    return fetch_stub


def test_collect_freezes_hourly_and_daily_and_paces_calls(
    tmp_path: Path, config: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    calls: list[dict[str, str | float]] = []
    monkeypatch.setattr(open_meteo, "fetch", _fake_fetch(calls))

    written = collect_open_meteo(config, tmp_path / "raw")

    assert [path.name for path in written] == ["hourly_soria.json", "daily_soria.json"]
    assert len(calls) == 2
    # Pause before the 2nd call, based on the weight of the 1st and the hourly budget.
    assert sleeps == [0.0, pytest.approx(api_call_weight("hourly") * 3600 / 5000)]


def test_collect_resumes_without_refetching_frozen_files(
    tmp_path: Path, config: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    raw_dir = tmp_path / "raw"
    hourly_path = save_raw(
        _full_hourly_payload(), POINT, "hourly", build_params(POINT, "hourly"), raw_dir
    )
    frozen = hourly_path.read_bytes()
    calls: list[dict[str, str | float]] = []
    monkeypatch.setattr(open_meteo, "fetch", _fake_fetch(calls))

    written = collect_open_meteo(config, raw_dir)

    assert len(calls) == 1
    assert "daily" in calls[0]
    assert len(written) == 2
    assert hourly_path.read_bytes() == frozen


def test_collect_overwrite_refetches_everything(
    tmp_path: Path, config: Path, monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    raw_dir = tmp_path / "raw"
    save_raw(_full_hourly_payload(), POINT, "hourly", build_params(POINT, "hourly"), raw_dir)
    calls: list[dict[str, str | float]] = []
    monkeypatch.setattr(open_meteo, "fetch", _fake_fetch(calls))

    collect_open_meteo(config, raw_dir, overwrite=True)

    assert len(calls) == 2


def test_collect_prints_one_line_per_file_with_upcoming_pause(
    tmp_path: Path,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    sleeps: list[float],
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_dir = tmp_path / "raw"
    calls: list[dict[str, str | float]] = []
    monkeypatch.setattr(open_meteo, "fetch", _fake_fetch(calls))

    collect_open_meteo(config, raw_dir)

    pause_hourly = api_call_weight("hourly") * 3600 / 5000
    pause_daily = api_call_weight("daily") * 3600 / 5000
    assert capsys.readouterr().out.splitlines() == [
        f"wrote hourly_soria.json — next pause: {pause_hourly:.1f} s",
        f"wrote daily_soria.json — next pause: {pause_daily:.1f} s",
    ]

    collect_open_meteo(config, raw_dir)

    assert len(calls) == 2  # second run: no call
    assert capsys.readouterr().out.splitlines() == [
        "skipped hourly_soria.json (already frozen) — next pause: 0.0 s",
        "skipped daily_soria.json (already frozen) — next pause: 0.0 s",
    ]


def test_collect_skips_legacy_daily_file_without_refetching(
    tmp_path: Path,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    sleeps: list[float],
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_dir = tmp_path / "raw"
    daily_path = _freeze_legacy_daily(raw_dir)
    frozen = daily_path.read_bytes()
    calls: list[dict[str, str | float]] = []
    monkeypatch.setattr(open_meteo, "fetch", _fake_fetch(calls))

    collect_open_meteo(config, raw_dir)

    assert ["hourly" in params for params in calls] == [True]
    assert daily_path.read_bytes() == frozen
    assert "skipped daily_soria.json" in capsys.readouterr().out


# --- Quality check -----------------------------------------------------------------------------


def _hourly_table(payload: dict[str, Any] | None = None) -> pd.DataFrame:
    return parse_response(_record(payload or _full_hourly_payload()), "hourly")


def test_quality_report_on_clean_hourly_data() -> None:
    report = quality_report(_hourly_table(), "hourly", ["soria"])

    assert len(report) == 1
    row = report.iloc[0]
    assert row["point"] == "soria"
    assert row["kind"] == "hourly"
    assert row["expected_rows"] == row["actual_rows"] == 24_096
    assert row["duplicate_rows"] == 0
    assert row["missing_timestamps"] == 0
    assert row["unexpected_timestamps"] == 0
    assert row["first_time"] == pd.Timestamp("2023-01-01T00:00", tz="UTC")
    assert row["last_time"] == pd.Timestamp("2025-09-30T23:00", tz="UTC")
    assert row["nan_wind_speed_100m"] == 0
    assert row["nan_shortwave_radiation"] == 0
    assert row["nan_temperature_2m"] == 0
    assert row["negative_wind"] == 0
    assert row["negative_radiation"] == 0
    assert row["night_radiation"] == 0
    assert row["temperature_out_of_range"] == 0
    assert row["coverage_ok"]


def test_quality_report_counts_nan_per_variable_without_dropping_rows() -> None:
    payload = _full_hourly_payload()
    payload["hourly"]["wind_speed_100m"][10] = None
    payload["hourly"]["shortwave_radiation"][10] = None
    payload["hourly"]["shortwave_radiation"][11] = None
    df = _hourly_table(payload)

    report = quality_report(df, "hourly")

    row = report.iloc[0]
    assert row["nan_wind_speed_100m"] == 1
    assert row["nan_shortwave_radiation"] == 2
    assert row["nan_temperature_2m"] == 0
    assert row["actual_rows"] == 24_096
    assert row["coverage_ok"]  # NaN values are not a coverage defect
    assert len(df) == 24_096


def test_quality_report_flags_each_impossible_value() -> None:
    payload = _full_hourly_payload()
    hourly = payload["hourly"]
    # 2023-01-01 12:00 UTC: broad daylight; 02:00 UTC: dead of night.
    hourly["wind_speed_100m"][12] = -0.1
    hourly["shortwave_radiation"][12] = -1.0
    hourly["shortwave_radiation"][2] = 3.0
    hourly["temperature_2m"][12] = -30.1
    hourly["temperature_2m"][13] = 50.1

    row = quality_report(_hourly_table(payload), "hourly").iloc[0]

    assert row["negative_wind"] == 1
    assert row["negative_radiation"] == 1
    assert row["night_radiation"] == 1
    assert row["temperature_out_of_range"] == 2


def test_quality_report_temperature_bounds_are_inclusive() -> None:
    payload = _full_hourly_payload()
    payload["hourly"]["temperature_2m"][0] = -30.0
    payload["hourly"]["temperature_2m"][1] = 50.0

    assert quality_report(_hourly_table(payload), "hourly").iloc[0]["temperature_out_of_range"] == 0


def test_quality_report_ignores_daytime_radiation_and_night_nan() -> None:
    payload = _full_hourly_payload()
    payload["hourly"]["shortwave_radiation"][12] = 500.0  # 12:00 UTC: broad daylight
    payload["hourly"]["shortwave_radiation"][21] = 2.0  # possible summer twilight: tolerated
    payload["hourly"]["shortwave_radiation"][2] = None  # night-time NaN: already counted as NaN

    row = quality_report(_hourly_table(payload), "hourly").iloc[0]

    assert row["night_radiation"] == 0
    assert row["nan_shortwave_radiation"] == 1


def test_quality_report_detects_duplicates_and_gaps() -> None:
    payload = _full_hourly_payload()
    _duplicate_row(payload, "hourly", 100)

    row = quality_report(_hourly_table(payload), "hourly").iloc[0]

    assert row["actual_rows"] == row["expected_rows"]  # the count alone would not be enough
    assert row["duplicate_rows"] == 1
    assert row["missing_timestamps"] == 1
    assert not row["coverage_ok"]


def test_quality_report_lists_missing_and_unexpected_points() -> None:
    report = quality_report(_hourly_table(), "hourly", ["lugo"])

    assert report["point"].tolist() == ["lugo", "soria"]
    absent, unexpected = report.iloc[0], report.iloc[1]
    assert absent["actual_rows"] == 0
    assert absent["missing_timestamps"] == 24_096
    assert not absent["coverage_ok"]
    assert absent["is_expected_point"]
    assert unexpected["actual_rows"] == 24_096
    assert not unexpected["is_expected_point"]
    assert not unexpected["coverage_ok"]


def test_quality_report_separates_points() -> None:
    other = _record(_full_hourly_payload())
    other["_meta"]["point"] = "ecija"
    other["response"]["hourly"]["temperature_2m"][5] = None
    df = pd.concat([_hourly_table(), parse_response(other, "hourly")], ignore_index=True)

    report = quality_report(df, "hourly").set_index("point")

    assert report.loc["ecija", "nan_temperature_2m"] == 1
    assert report.loc["soria", "nan_temperature_2m"] == 0
    assert report["actual_rows"].tolist() == [24_096, 24_096]


def test_quality_report_on_daily_data_has_no_night_check() -> None:
    df = parse_response(_record(_full_daily_payload()), "daily")

    report = quality_report(df, "daily", ["soria"])

    row = report.iloc[0]
    assert row["expected_rows"] == row["actual_rows"] == 12_692
    assert row["nan_shortwave_radiation_sum"] == 1
    assert row["coverage_ok"]
    assert "night_radiation" not in report.columns


def test_quality_report_does_not_modify_input() -> None:
    df = _hourly_table()
    before = df.copy()

    quality_report(df, "hourly", ["soria"])

    pd.testing.assert_frame_equal(df, before)


def test_quality_report_from_raw_describes_incomplete_snapshot(tmp_path: Path) -> None:
    _freeze_altered_snapshot(_hourly_payload(24), tmp_path)

    report = quality_report_from_raw(tmp_path, CONFIG, "hourly").set_index("point")

    assert len(report) == 7  # the 6 uncollected points also appear
    assert report.loc["soria", "actual_rows"] == 24
    assert not report.loc["soria", "coverage_ok"]
    assert report.loc["madrid", "actual_rows"] == 0


def test_quality_report_from_raw_applies_daily_filter_to_legacy_file(tmp_path: Path) -> None:
    _freeze_legacy_daily(tmp_path)

    report = quality_report_from_raw(tmp_path, CONFIG, "daily").set_index("point")

    row = report.loc["soria"]
    assert row["actual_rows"] == 12_692
    assert row["unexpected_timestamps"] == 0
    assert row["nan_shortwave_radiation_sum"] == 0  # the null of 1940-01-01 is outside the period
    assert row["coverage_ok"]
