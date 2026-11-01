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
real quarter-hour pricing starts there. The two raw API responses and the
exact request that produced them are frozen in
`data/raw/ree/_verification/` (captured 2026-10-29), since the `time_trunc`
parameter this check depends on is not reliably available on demand.

A separate, smaller comparison uses Open-Meteo daily climatology from 1991
(the WMO 1991–2020 climate normal, extended through September 2025) to check
whether 2023–2025 was an unusually windy/calm sample — not part of the main
hourly join.

## Setup and run

Requirements: [uv](https://docs.astral.sh/uv/) only. The project pins
Python 3.13 (`.python-version`, `requires-python = ">=3.13"`); `uv sync`
downloads it if needed. All commands run from the repository root; no
absolute paths.

**Rebuild everything from the saved raw data (offline, ~2 seconds):**

```bash
uv sync
uv run spain-electricity-weather build-analysis
```

This reads the frozen snapshots in `data/raw/`, joins prices and weather,
and writes the analytical table, the join report and the EDA tables to
`data/processed/` (see *Data locations*). No network access is needed.

**Code quality and tests:**

```bash
uv sync                          # install the environment (incl. dev tools)
uv run ruff check .              # lint
uv run ruff format --check .     # formatting
uv run mypy src tests            # type-check
uv run pytest -q                 # run the test suite
```

**REE (electricity prices)**

```bash
uv run spain-electricity-weather collect-ree   # fetch the raw snapshot (several minutes; resumable)
uv run spain-electricity-weather parse-ree     # build the clean hourly series from the saved snapshot
```

`parse-ree` works directly from the raw snapshot already committed in
`data/raw/ree/` — `collect-ree` does not need to be re-run to rebuild the
analytical output.

**Open-Meteo (weather)**

```bash
uv run spain-electricity-weather collect-open-meteo   # fetch the raw snapshot (online; skips files already collected)
uv run spain-electricity-weather rebuild-open-meteo   # rebuild hourly and daily tables from the saved snapshot (offline)
```

`rebuild-open-meteo` works directly from the raw snapshot already committed in
`data/raw/open_meteo/` — `collect-open-meteo` does not need to be re-run.
The detailed quality report is available from
`quality_report_from_raw` in `src/spain_electricity_weather/collect/open_meteo.py`.

**Quality-tool scope.** Ruff (rules `E`, `F`, `I`, `UP`, `B`, `SIM`, line
length 100) and `ruff format` run on the whole repository (`src/` and
`tests/`). mypy runs in `strict` mode on `src/` and `tests/`, with
`pandas-stubs` and `types-requests` installed: no global
`ignore_missing_imports`, no per-module exclusion, no `# type: ignore`.

## Analysis outputs

- **EDA** — three findings, the bounded answer and the EDA limitations:
  [`docs/eda.md`](docs/eda.md). Every figure in it comes from a CSV in
  `data/processed/eda/`, produced by
  `src/spain_electricity_weather/analysis/eda.py`.
- **Quality note** (one page, including *What cannot be trusted*):
  [`docs/quality_note.md`](docs/quality_note.md).

## Sources and access

- **REE** (electricity prices): public REST API, no key required. Full
  details — owner, endpoint, access method, filters, extraction dates,
  coverage — in [`data/raw/ree/PROVENANCE.md`](data/raw/ree/PROVENANCE.md).
- **Open-Meteo** (weather, 7 points — see `config/weather_points.toml`):
  Historical Weather API (ERA5 reanalysis), public REST API, no key required,
  CC BY 4.0 (attribution to Open-Meteo and Copernicus C3S). Full details in
  [`docs/provenance_open_meteo.md`](docs/provenance_open_meteo.md).

## Data locations

| Path | Contents |
|---|---|
| `data/raw/ree/YYYY-MM.json` | Frozen REE API responses, one file per month, unmodified |
| `data/raw/ree/_verification/` | Frozen proof of the Oct 2025 15-minute pricing switch |
| `data/raw/ree/PROVENANCE.md` | REE source documentation and quality decisions |
| `data/raw/open_meteo/{hourly,daily}_<point>.json` | Frozen Open-Meteo responses, one hourly and one daily file per point (14 files) |
| `docs/provenance_open_meteo.md` | Open-Meteo source documentation and quality decisions |
| `config/weather_points.toml` | Coordinates, role and region of the 7 weather points |
| `data/interim/ree_hourly_prices.csv` | Parsed REE hourly price series (regenerated by `parse-ree`, not committed) |
| `data/processed/price_weather_hourly.csv` | **Final analytical table** read by Tableau: one row per UTC hour, price (EUR/MWh), national wind at 100 m (m/s), radiation (W/m², aligned on the price hour, D8), temperature (°C) |
| `data/processed/join_report.json` | Row counts before and after the join, unmatched hours on each side |
| `data/processed/eda/*.csv` | EDA tables (hourly profile, wind bins, regression, monthly table, profile spread, windy vs calm months, indexed-contract risk, climatology) |

## Tableau

*(to fill in once the workbook is built)*

## Contributions

Each source was collected, parsed and quality-checked by one person, then
reviewed by the other before merging — both members can explain either
side of the pipeline.

| Branch / PR | Author | Scope |
|---|---|---|
| `feature/ree-collection` (PR #2) | Arnould | REE price collection, parsing, quality checks, CLI |
| `feature/open-meteo` (PR #1) | Paul | Open-Meteo weather collection, quality checks |
| `feature/open-meteo-provenance` (PR #3) | Paul | Open-Meteo provenance sheet, CLI wiring |
| `feature/national-weather` (PR #4) | Paul | National weather aggregation (simple average by role) |
| `feature/price-weather-join` | Arnould, reviewed and fixed by Paul | Price x weather join, consumption-profile KPIs, sensitivity and climatology comparisons. Review fixes by Paul: climatology compared month by month (the plain mean over-weighted spring and summer), sensitivity split at midnight Madrid time, quality note update |
| `feature/eda` | Paul | EDA module and tests, `build-analysis` pipeline command, `docs/eda.md`, final one-page quality note, README |

Review notes worth keeping visible: PR #2 was reviewed in detail before
merging and several fixes came out of it directly (`check_hourly_coverage`
blind to a truncated snapshot, a duplicate-price conflict silently
resolved by `drop_duplicates` before the quality check ran, a missing
`ruff format --check`) — see that PR's commit history for the before/after.
