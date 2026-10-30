# Spain Electricity Price vs. Weather

## Question and scope

**Decision maker:** an SME choosing between a fixed-price and a spot-indexed
electricity contract (a once-a-year, strategic decision — not a day-to-day one).

**Question:** do wind and sunshine conditions explain the level and the
month-to-month volatility of the Spanish day-ahead electricity price, and —
depending on the consumption profile (weekday-daytime vs. 24/7) — is a
spot-indexed contract worth the risk compared to a fixed-price one?
(REE's own production-by-technology series already tells us *how much*
renewable electricity was generated; weather is what lets an SME reason
about this *ahead of time*, and situates the 2023–2025 sample against the
WMO 1991–2020 climate normal — see the 1991–2025 comparison below.)

**Population / geography:** Spain mainland, national day-ahead market.

**Period:** January 2023 – September 2025 (hourly). Stopped at 30 September
2025 because the Spanish day-ahead market moved from hourly to 15-minute
pricing on 1 October 2025.

Verified directly against the REE `precios-mercados-tiempo-real` endpoint
(`Precio mercado spot` series), not assumed: on 15 September 2025 the four
15-minute values inside each hour are identical (e.g. 97.03 / 97.03 / 97.03
/ 97.03 — hourly data re-shaped into a 15-minute array), while on 1 October
2025 they genuinely differ (105.10 / 104.24 / 102.28 / 102.00) — proof the
real quarter-hour pricing starts there. See the collection script / Quality
note for the reproducible check.

A separate, smaller comparison uses Open-Meteo daily climatology from 1991
(the WMO 1991–2020 climate normal, extended through September 2025) to check
whether 2023–2025 was an unusually windy/calm sample — not part of the main
hourly join.

## Setup and run

*(to fill in once the collection scripts exist)*
### Open-Meteo
uv run python -m spain_electricity_weather.collect.open_meteo_cli collect   # online, skips files already collected
uv run python -m spain_electricity_weather.collect.open_meteo_cli rebuild   # offline, from the raw snapshot

## Sources and access

*(to fill in once the REE and Open-Meteo provenance sheets are written)*
### Open-Meteo (weather)
Historical Weather API (ERA5 reanalysis), no API key, CC BY 4.0 (attribution to Open-Meteo and Copernicus C3S).
Full provenance sheet: [docs/provenance_open_meteo.md](docs/provenance_open_meteo.md).

## Data locations

*(to fill in once the raw snapshots are collected)*
### Open-Meteo
- Raw snapshot (frozen, versioned): `data/raw/open_meteo/` — 14 JSON files, one hourly and one daily file per point.
- Points configuration: `config/weather_points.toml`.

## Tableau

*(to fill in once the workbook is built)*

## Contributions

*(to fill in — per-person branches/PRs, see git log)*
