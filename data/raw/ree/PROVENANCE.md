# REE provenance

## Source

- **Owner**: Red Eléctrica de España (REE), Spain's grid operator
- **Documentation**: https://www.ree.es/en/apidatos
- **Endpoint called**: `https://apidatos.ree.es/en/datos/mercados/precios-mercados-tiempo-real`
- **Access method**: public REST API, HTTP GET, no authentication/API key
  required. Collected automatically by
  `src/spain_electricity_weather/collect/ree.py` (`collect_raw`), one
  request per calendar day, with retry-with-backoff on transient errors
  and a 0.4s delay between requests.
- **Reuse conditions**: public institutional data published without an
  explicit license statement on the API documentation page. Used here for
  academic, non-commercial purposes. Not independently verified beyond
  what the documentation page states.

## Extraction

- **Date(s) collected**: 2026-10-29 to 2026-10-30 (collection was
  interrupted once by a machine shutdown and resumed; `collect_raw` skips
  months already saved, so no data was lost or duplicated — see commit
  history for the two collection runs)
- **Filters used**: `time_trunc=hour`; only the series with `id="600"`
  ("Precio mercado spot" / "Spot market price") is kept — the payload also
  contains an unrelated `PVPC` series (`id="1001"`) which is discarded.
  `extract_spot_values` additionally checks the series name matches, not
  just its id, to catch any REE-side change silently (see `ree.py`).

## Coverage

- **Requested period**: 2023-01-01 to 2025-09-30 (hourly), stopping there
  because the market moved to 15-minute pricing on 1 October 2025 — see
  `data/raw/ree/_verification/` for the frozen evidence of that switch.
- **Storage**: one raw JSON file per month (`data/raw/ree/YYYY-MM.json`),
  each a list of `{"date": ..., "payload": ...}` entries holding REE's
  response exactly as returned, unmodified
- **Raw row count is not uniform, and that is expected**: for 2023 and
  2024, the API honours `time_trunc=hour` and returns exactly 24 values
  per day (23/25 on the two 2023/2024 DST transition days). From January
  2025 onward, the API returns quarter-hour-spaced entries regardless of
  `time_trunc=hour` (~93/day) — checked directly: the four values inside
  each hour are identical (e.g. `134.49` repeated at `:00/:15/:30/:45` on
  2025-01-01), i.e. the same hourly price reshaped, not real quarter-hour
  pricing (that only starts 2025-10-01, see `_verification/`). Total raw
  entries across all 33 months: 42,929.
- **Expected analytical rows after parsing**: 1,004 days x 24 hours =
  **24,096** hourly price points — the parsing step must filter to
  `minute == 0` to collapse both the plain-hourly (2023-2024) and the
  reshaped quarter-hour (2025) months to one row per hour consistently.
- **Grain**: one row = one hour of national (peninsular) day-ahead spot
  price, in EUR/MWh
