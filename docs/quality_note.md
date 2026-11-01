# Quality note

Sources: `data/raw/ree/PROVENANCE.md`, `docs/provenance_open_meteo.md`.

## Coverage and integration checks

- **REE prices** — one row per hour (UTC): 24,095 rows, 2023-01-01 to
  2025-09-30, checked against the fixed project period, not the data's own
  min/max: 0 missing hours, 0 duplicates, 0 nulls.
- **Open-Meteo weather** — one row per point and hour (UTC), 7 points:
  0 gaps, 0 duplicates, 0 nulls, 0 physically impossible values per point.
  Aggregated to one national row per hour (24,096).
- **Join** — inner join on the UTC hour, 1-to-1 enforced
  (`validate="one_to_one"`: a duplicated key fails instead of multiplying
  rows). 24,094 of 24,095 price hours matched (99.996%). 3 unmatched hours,
  all at the edges (1 price-only, 2 weather-only) because REE is requested in
  Madrid time; listed in `data/processed/join_report.json`.

## Key decisions

- **Radiation shifted by one hour**: Open-Meteo radiation at T covers
  [T-1, T), the REE price at T covers [T, T+1).
- **National weather = simple mean by role** (wind points for wind, solar
  points for radiation, all 7 for temperature). The capacity-weighted mean
  was dropped: REE's installed-capacity endpoint returns HTTP 500.
- **Zero and negative prices kept** (871 and 782 hours, min -15 EUR/MWh):
  real market outcomes of renewable oversupply, not errors.
- **28 April 2025 blackout kept**: day-ahead prices were set the day
  before, so the series shows no anomaly.
- **Like-for-like seasons**: climatology compared month by month with the
  1991-2020 normal, and profile spreads compared on January-September only,
  since the sample holds three Jan-Sep seasons but two Oct-Dec ones (a plain
  mean made it look 4-5% sunnier than normal). Calendar logic in Madrid time.

## What cannot be trusted

- **Wholesale energy price only**: no supplier margin, network tolls or
  taxes; it compares the energy component of a contract, not the bill.
- **Gas is missing.** Gas plants often set the price, and the Iberian gas
  cap ran until end-2023: excluding 2023 lowers the break-even price by 12%
  (24/7) to 17.5% (weekday-daytime). Weather effects are associations, not
  causal effects; no standard errors (autocorrelated hourly data).
- **Weather stands in for generation**: 7 points, equal weights, wind speed
  rather than wind output (output rises roughly with the cube of speed).
- **33 months** give a range, not a reliable tail risk; the sample was
  normal for wind and sun but 0.7-1.1 °C warmer. Public holidays count as
  working days in the daytime profile.
