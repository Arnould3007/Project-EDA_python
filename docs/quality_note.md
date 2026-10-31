# Quality note

**Status: draft.** The price-weather join exists and runs but is not yet
merged — two decisions (radiation alignment, boundary rows) are pending
confirmation on the Open-Meteo side (decision journal D8, D23). Numbers
below are from that draft.

## Coverage and integration checks

- **REE prices**: 24,095 hourly rows, 2023-01-01 to 2025-09-30, verified
  against the project's fixed period (not the data's own min/max, which
  would hide a truncated snapshot) — 0 missing hours, 0 duplicates, 0 null
  prices.
- **Open-Meteo weather**: 7 points, hourly and daily, each with 0 gaps,
  0 duplicates, 0 nulls, 0 physically impossible values (checked against
  the full requested period for every point).
- **Join** (price x national weather, inner join on UTC hour): 24,094 of
  24,095 price-hours matched. 3 unmatched, all at the period's edges, none
  in the middle: 1 price-only hour (2022-12-31 23:00 UTC) and 2
  weather-only hours (2025-09-30 22:00-23:00 UTC) — a boundary artefact of
  REE being requested in Madrid local time vs. Open-Meteo in UTC directly,
  not a data loss. Reported explicitly, not dropped silently.

## Key decisions

- **Join key**: UTC hour, 1-1 relationship, enforced (`validate="one_to_one"`
  — a duplicated key fails loudly instead of silently multiplying rows).
- **Radiation alignment** (D8, pending confirmation): Open-Meteo's solar
  radiation at hour T is the average of [T-1, T), while REE's price at T
  covers [T, T+1). Shifted radiation by one hour so both describe the same
  interval.
- **National weather**: simple average per role (wind points for wind,
  solar points for solar); a capacity-weighted alternative was planned but
  REE's installed-capacity endpoint (`generacion/potencia-instalada`)
  returns `500` regardless of parameters — confirmed broken server-side,
  not a request error (see `data/raw/ree/PROVENANCE.md`).
- **Negative and zero prices** (782 and 871 hours respectively, min -15
  EUR/MWh): kept, not filtered — real market outcomes from renewable
  oversupply, not errors.
- **28 April 2025 (Iberian blackout)**: shows no anomaly in the price
  series, and that's expected: day-ahead prices are fixed the previous
  afternoon, before the blackout happened.

## What cannot be trusted

- **Wholesale price only.** Excludes supplier margin and network tolls:
  this compares the energy component of a contract, not the full bill.
- **Spain's 2023 gas price cap mechanism.** Gas plants often set the
  marginal price; the cap applied through 2023 only, making that year not
  directly comparable to 2024-2025 (quantified: excluding 2023 lowers the
  break-even price by roughly 15-17%, see the sensitivity comparison).
- **Spatial weather aggregation.** 7 points approximate national
  conditions; point choice and equal weighting are debatable, and a
  capacity-weighted alternative could not be built (see above).
- **Market granularity change, October 2025.** The study stops
  2025-09-30; results do not cover the 15-minute market that followed.
- **Climatology sample.** Checked directly: 2023-2025 was close to the
  1991-2020 normal for wind (within ~1-2.5%), but warmer (+7 to +12%) and
  slightly sunnier (+4-5%) on every point — the indexed-contract risk from
  wind specifically is not obviously under- or over-stated by the sample,
  but the sample was not climate-neutral either.
- **Wind speed averaged, not generation.** Wind power scales roughly with
  the cube of speed between cut-in and rated speed; averaging speeds
  smooths out peaks. Not checked against REE's actual wind generation
  (`generacion/estructura-generacion`, confirmed available) — deferred
  past the join/EDA, revisited 2026-11-02 evening if time allows.
- **Capacity weighting uses 6 regions only**, each represented by a single
  point; other producing regions are absent from that (unused) weighting.
