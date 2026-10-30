# Quarter-hour pricing verification (frozen evidence)

## What this proves

The project period stops at 30 September 2025 because the Spanish day-ahead
market moved from hourly to 15-minute pricing on 1 October 2025. The two
files below are unmodified REE API responses that demonstrate this directly:

- `2025-09-15_response.json`: the "Spot market price" series (id `600`) has
  four identical values per hour (e.g. `97.03 / 97.03 / 97.03 / 97.03`) —
  hourly data reshaped into a 15-minute array.
- `2025-10-01_response.json`: the same series has four genuinely different
  values per hour (e.g. `105.10 / 104.24 / 102.28 / 102.00`) — real
  quarter-hour pricing.

## Exact request that produced them

```
GET https://apidatos.ree.es/en/datos/mercados/precios-mercados-tiempo-real
    ?start_date=2025-09-15T00:00&end_date=2025-09-15T23:59&time_trunc=day
GET https://apidatos.ree.es/en/datos/mercados/precios-mercados-tiempo-real
    ?start_date=2025-10-01T00:00&end_date=2025-10-01T23:59&time_trunc=day
```

Captured: 2026-10-29.

## Why this is frozen, not a live script

`time_trunc=day` on this endpoint is currently returning a 400 error for
every date (confirmed 2026-10-30, including for dates that worked before).
Only `time_trunc=hour` works right now, which does not return the
quarter-hour breakdown needed for this specific check. The main collection
pipeline (`collect.ree.collect_raw`) uses `time_trunc=hour` and is
unaffected. This evidence is preserved as-is so the comparison does not
depend on REE's `time_trunc=day` parameter working again.
