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
- **Analytical rows after parsing**: the naive expectation is 1,004 days x
  24 hours = 24,096, but the achieved, verified count is **24,095**: the
  series runs from 2022-12-31T23:00Z to 2025-09-30T21:00Z, because Madrid
  local midnight on the first day is UTC+1 (23:00 the day before) while
  23:00 local on the last day is UTC+2 (21:00 the same day) — a net 1-hour
  boundary effect from the timezone conversion, not a gap. Verified by
  `check_hourly_coverage` against this exact fixed period (not derived
  from the data's own min/max, which would hide a truncated snapshot):
  0 missing hours, 0 unexpected hours, 0 duplicate rows, 0 null prices.
- **Grain**: one row = one hour of national (peninsular) day-ahead spot
  price, in EUR/MWh
- **Negative and zero prices are real, not filtered**: 782 hours at a
  negative price (minimum -15.00 EUR/MWh) and 871 hours at exactly 0,
  consistent with renewable oversupply. Kept as-is.
- **28 April 2025 (the Iberian Peninsula blackout) shows no anomaly in
  this series**, and that is expected, not a data quality problem:
  day-ahead prices are fixed the previous afternoon, before the blackout
  happened, so they cannot reflect it. Flagged here so it isn't mistaken
  for a collection error later.
- **Duplicate/conflict handling**: `parse_hourly_series` checks for two
  different prices on the same hour *before* deduplicating, and raises
  rather than silently keeping one and discarding the other. No such
  conflict exists in the current 33-month snapshot (checked directly).

## Installed capacity by region — attempted, not available

For the simple-average-vs-weighted-average comparison of the Open-Meteo
wind/solar points (see the weather collection's own provenance), the
weighting would use REE's installed wind/solar capacity (MW) by autonomous
community. This was investigated and is **not currently available**:

- **Widget identified**: `generacion/potencia-instalada` (confirmed by
  name against REE's own API documentation; it is the only widget in the
  `generacion` category that matches "installed capacity" — the similarly
  named `estructura-generacion` returns annual generation *output* in MWh,
  a different quantity, not a substitute)
- **Tested**: `https://apidatos.ree.es/en/datos/generacion/potencia-instalada`
  with `geo_limit=ccaa&geo_ids=7` (Castilla-La Mancha), with
  `geo_limit=peninsular`, and with no geo filter at all (national level) —
  **all three return `500 Internal Server Error`** (2026-10-30). Getting
  the same failure with zero geographic parameters rules out a parameter
  mistake on our side; the widget itself is currently broken on REE's end.
- **Decision**: default to the **simple average** across the weather
  points (no capacity weighting), as agreed with the Open-Meteo side. This
  is recorded as a project limitation rather than silently dropped — if
  REE's endpoint becomes available again, the weighted comparison can be
  added without changing anything upstream of it.

## Weather-to-generation link — not verified, deferred

The project's causal chain is weather -> renewable generation -> price,
but only the two ends (weather, price) are collected; the middle link is
assumed, not checked. REE's `generacion/estructura-generacion` (confirmed
working, unlike `potencia-instalada` above) could provide actual hourly
wind/solar generation to validate that the averaged wind speed/radiation
across the weather points tracks real generation.

- **Current limitation, to state explicitly in the Quality note's "What
  cannot be trusted" section**: *the weather-to-generation link is not
  verified* — the analysis assumes average wind speed/radiation across
  the chosen points is a reasonable proxy for renewable generation, but
  this has not been checked against REE's actual generation data.
- **Decision**: deferred until after the join and EDA are done (the join
  is the actual blocker for the rest of the project). Revisit by
  **2026-11-02 evening**: if the core pipeline (join + EDA) is finished
  by then, add the hourly wind/solar generation and the validating
  chart; otherwise this limitation stands as written above for the final
  submission.
