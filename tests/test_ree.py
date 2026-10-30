from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from spain_electricity_weather.collect.ree import extract_spot_values, parse_hourly_series
from spain_electricity_weather.quality.ree import check_hourly_coverage


def _payload(
    values: list[dict[str, object]],
    series_type: str = "Spot market price",
    series_id: str = "600",
) -> dict[str, object]:
    return {
        "included": [
            {
                "id": "1001",
                "type": "PVPC",
                "attributes": {"values": [{"value": 999, "datetime": "irrelevant"}]},
            },
            {"id": series_id, "type": series_type, "attributes": {"values": values}},
        ]
    }


def test_extract_spot_values_returns_only_the_spot_series() -> None:
    values = [{"value": 42.0, "datetime": "2023-01-01T00:00:00.000+01:00"}]
    payload = _payload(values)

    assert extract_spot_values(payload) == values


def test_extract_spot_values_rejects_a_series_name_mismatch() -> None:
    values = [{"value": 42.0, "datetime": "2023-01-01T00:00:00.000+01:00"}]
    payload = _payload(values, series_type="Something else entirely")

    with pytest.raises(ValueError, match="name mismatch"):
        extract_spot_values(payload)


def test_extract_spot_values_rejects_a_missing_series() -> None:
    payload = {
        "included": [
            {"id": "1001", "type": "PVPC", "attributes": {"values": []}},
        ]
    }

    with pytest.raises(ValueError, match="Expected exactly one"):
        extract_spot_values(payload)


def test_check_hourly_coverage_detects_a_gap() -> None:
    frame = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(
                ["2023-01-01T00:00Z", "2023-01-01T01:00Z", "2023-01-01T03:00Z"]
            ),
            "price_eur_mwh": [10.0, 11.0, 13.0],
        }
    )

    report = check_hourly_coverage(
        frame, pd.Timestamp("2023-01-01T00:00Z"), pd.Timestamp("2023-01-01T03:00Z")
    )

    assert report["rows"] == 3
    assert report["expected_hours"] == 4
    assert report["missing_hours"] == [pd.Timestamp("2023-01-01T02:00Z")]


def test_check_hourly_coverage_detects_duplicate_timestamps() -> None:
    frame = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(
                ["2023-01-01T00:00Z", "2023-01-01T00:00Z", "2023-01-01T01:00Z"]
            ),
            "price_eur_mwh": [10.0, 10.0, 11.0],
        }
    )

    report = check_hourly_coverage(
        frame, pd.Timestamp("2023-01-01T00:00Z"), pd.Timestamp("2023-01-01T01:00Z")
    )

    assert report["duplicate_rows"] == 1


def test_check_hourly_coverage_flags_a_truncated_snapshot() -> None:
    """A frame missing its last 3 months must not look complete just because
    the expected grid is built from the project's own known period, not
    from the frame's own min/max."""
    frame = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(["2023-01-01T00:00Z", "2023-01-01T01:00Z"]),
            "price_eur_mwh": [10.0, 11.0],
        }
    )

    report = check_hourly_coverage(
        frame, pd.Timestamp("2023-01-01T00:00Z"), pd.Timestamp("2023-01-01T05:00Z")
    )

    assert report["expected_hours"] == 6
    assert len(report["missing_hours"]) == 4


def test_check_hourly_coverage_detects_null_prices() -> None:
    frame = pd.DataFrame(
        {
            "time_utc": pd.to_datetime(["2023-01-01T00:00Z", "2023-01-01T01:00Z"]),
            "price_eur_mwh": [10.0, None],
        }
    )

    report = check_hourly_coverage(
        frame, pd.Timestamp("2023-01-01T00:00Z"), pd.Timestamp("2023-01-01T01:00Z")
    )

    assert report["null_price_rows"] == 1


def test_parse_hourly_series_collapses_quarter_hour_duplicates_to_one_row_per_hour(
    tmp_path: Path,
) -> None:
    quarter_hour_values = [
        {"value": 50.0, "datetime": "2025-01-01T00:00:00.000+01:00"},
        {"value": 50.0, "datetime": "2025-01-01T00:15:00.000+01:00"},
        {"value": 50.0, "datetime": "2025-01-01T00:30:00.000+01:00"},
        {"value": 50.0, "datetime": "2025-01-01T00:45:00.000+01:00"},
        {"value": 60.0, "datetime": "2025-01-01T01:00:00.000+01:00"},
    ]
    payload = _payload(quarter_hour_values)
    (tmp_path / "2025-01.json").write_text(
        json.dumps([{"date": "2025-01-01", "payload": payload}]), encoding="utf-8"
    )

    frame = parse_hourly_series(tmp_path)

    assert len(frame) == 2
    assert frame["price_eur_mwh"].tolist() == [50.0, 60.0]


def test_parse_hourly_series_rejects_conflicting_prices_for_the_same_hour(
    tmp_path: Path,
) -> None:
    """A silent drop_duplicates would hide this instead of catching it."""
    day_one = _payload([{"value": 10.0, "datetime": "2023-01-01T00:00:00.000+01:00"}])
    day_two = _payload([{"value": 99.0, "datetime": "2023-01-01T00:00:00.000+01:00"}])
    (tmp_path / "2023-01.json").write_text(
        json.dumps(
            [
                {"date": "2023-01-01", "payload": day_one},
                {"date": "2023-01-01-again", "payload": day_two},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Conflicting prices"):
        parse_hourly_series(tmp_path)


def test_parse_hourly_series_keeps_negative_and_zero_prices(tmp_path: Path) -> None:
    values = [
        {"value": -15.0, "datetime": "2023-01-01T00:00:00.000+01:00"},
        {"value": 0.0, "datetime": "2023-01-01T01:00:00.000+01:00"},
    ]
    payload = _payload(values)
    (tmp_path / "2023-01.json").write_text(
        json.dumps([{"date": "2023-01-01", "payload": payload}]), encoding="utf-8"
    )

    frame = parse_hourly_series(tmp_path)

    assert frame["price_eur_mwh"].tolist() == [-15.0, 0.0]


def test_parse_hourly_series_handles_the_dst_spring_forward_gap(tmp_path: Path) -> None:
    """2023-03-26: Madrid local time skips 02:00-03:00. A real REE response
    for that day has 23 entries, not 24 — must not look like a missing hour
    once converted to UTC."""
    hours_before = [
        {"value": float(h), "datetime": f"2023-03-26T{h:02d}:00:00.000+01:00"} for h in (0, 1)
    ]
    hours_after = [
        {"value": float(h), "datetime": f"2023-03-26T{h:02d}:00:00.000+02:00"} for h in range(3, 24)
    ]
    payload = _payload(hours_before + hours_after)
    (tmp_path / "2023-03.json").write_text(
        json.dumps([{"date": "2023-03-26", "payload": payload}]), encoding="utf-8"
    )

    frame = parse_hourly_series(tmp_path)

    assert len(frame) == 23
    gaps = frame["time_utc"].diff().dropna()
    assert (gaps == pd.Timedelta(hours=1)).all()
