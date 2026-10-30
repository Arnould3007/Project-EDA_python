# Provenance sheet — Open-Meteo (weather)

## Source

| Field | Value |
|---|---|
| Owner | Open-Meteo (open-source weather API). Underlying data: ERA5 reanalysis, produced by ECMWF for the Copernicus Climate Change Service (C3S) |
| Endpoint | `https://archive-api.open-meteo.com/v1/archive` (Historical Weather API) |
| Documentation | https://open-meteo.com/en/docs/historical-weather-api |
| Access method | REST API, HTTP GET, JSON response, no API key, no account |
| Extraction date | 30 October 2026 (exact UTC timestamp stored per file in `_meta.extracted_at_utc`) |
| Collection code | `src/spain_electricity_weather/collect/open_meteo.py` |
| Raw snapshot | `data/raw/open_meteo/` — 14 JSON files (`hourly_<point>.json`, `daily_<point>.json`) |

## Reuse conditions

- Licence: **CC BY 4.0**. Attribution required to Open-Meteo and to the Copernicus Climate Change Service (ERA5). Use the exact attribution wording given in the Open-Meteo documentation in the final deliverables.
- The free API is for **non-commercial use** only, which covers this student project.
- Free-tier limits: fewer than 600 calls per minute, 5,000 per hour and 10,000 per day. Calls are **weighted** by period length and number of variables, so a long request counts as several calls.

## Request parameters (filters)

| Parameter | Hourly request | Daily request (climatology) |
|---|---|---|
| Period | 2023-01-01 → 2025-09-30 | 1991-01-01 → 2025-09-30 |
| Variables | `wind_speed_100m`, `shortwave_radiation`, `temperature_2m` | `wind_speed_10m_mean`, `shortwave_radiation_sum`, `temperature_2m_mean` |
| Units | m/s, W/m², °C | m/s, MJ/m², °C |
| `timezone` | `GMT` (timestamps in UTC) | `GMT` |
| `wind_speed_unit` | `ms` (m/s instead of the default km/h) | `ms` |
| `models` | `era5` (single homogeneous source, instead of the default `best_match`) | `era5` |

## Points (entities)

Defined in `config/weather_points.toml`. The coordinates actually used by the API (nearest ERA5 grid cell, about 25 km) are stored in each raw file and in the `grid_lat` / `grid_lon` columns.

| Point | Role | Autonomous community | Requested lat, lon |
|---|---|---|---|
| soria | wind | Castilla y León | 41.77, −2.47 |
| lugo | wind | Galicia | 43.01, −7.56 |
| zaragoza | wind | Aragón | 41.65, −0.88 |
| ecija | solar | Andalucía | 37.54, −5.08 |
| merida | solar | Extremadura | 38.92, −6.34 |
| puertollano | solar | Castilla-La Mancha | 38.69, −4.11 |
| madrid | demand | Comunidad de Madrid | 40.42, −3.70 |

## Expected coverage

- Hourly: **24,096 hours per point** (2023-01-01T00:00 → 2025-09-30T23:00 UTC), no duplicates, no gaps.
- Daily: **12,692 days per point** from 1991-01-01 to 2025-09-30.
- Checked by the quality report and by a contract check at load time (UTC offset, units, series lengths, coverage, distance between requested point and grid cell).

## Known particularities

- `shortwave_radiation` is an **average over the preceding hour** (value at 12:00 = 11:00–12:00), while wind and temperature are instantaneous. Not shifted at collection; alignment with prices is handled in the join.
- Wind is at **100 m** in the hourly data (turbine hub height) but at **10 m** in the daily climatology (only daily wind variable available). Climatology comparisons use the 10 m wind.
- The daily files for **soria, lugo and zaragoza** were collected for 1940–2025 before the period was reduced to 1991. They are kept unchanged (frozen snapshot) and filtered to ≥ 1991-01-01 when loaded.
- Daily aggregates follow **UTC days**, not Spanish local days.
- Missing values are kept as NaN and counted in the quality report; they are never dropped silently.

## How to reproduce

```
uv sync
uv run python -m spain_electricity_weather.collect.open_meteo_cli collect   # online, skips existing files
uv run python -m spain_electricity_weather.collect.open_meteo_cli rebuild   # offline, from the raw snapshot
```
