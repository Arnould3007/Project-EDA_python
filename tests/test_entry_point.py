from __future__ import annotations

import sys
from pathlib import Path

import pytest

import spain_electricity_weather
from spain_electricity_weather.collect import open_meteo_cli


def test_main_routes_collect_open_meteo_with_default_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        open_meteo_cli, "collect", lambda config, raw_dir: calls.append((config, raw_dir))
    )
    monkeypatch.setattr(sys, "argv", ["spain-electricity-weather", "collect-open-meteo"])

    spain_electricity_weather.main()

    assert calls == [(Path("config/weather_points.toml"), Path("data/raw"))]


def test_main_routes_rebuild_open_meteo_with_default_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    monkeypatch.setattr(open_meteo_cli, "rebuild", calls.append)
    monkeypatch.setattr(sys, "argv", ["spain-electricity-weather", "rebuild-open-meteo"])

    spain_electricity_weather.main()

    assert calls == [Path("data/raw")]


def test_rebuild_open_meteo_rejects_the_collect_only_config_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(open_meteo_cli, "rebuild", lambda raw_dir: None)
    monkeypatch.setattr(
        sys, "argv", ["spain-electricity-weather", "rebuild-open-meteo", "--config", "x.toml"]
    )

    with pytest.raises(SystemExit):
        spain_electricity_weather.main()
