# Exploratory analysis: wind, sun and the Spanish day-ahead price

All figures below come from `uv run spain-electricity-weather build-analysis`,
which rebuilds them offline from the frozen raw snapshots and writes them to
`data/processed/eda/` (one CSV per table, named in brackets). The code is in
`src/spain_electricity_weather/analysis/eda.py`, tested in `tests/test_eda.py`.

**Scope:** 24,094 hours, January 2023 – September 2025, Spain mainland
day-ahead price (energy component, EUR/MWh) joined with national wind at
100 m, solar radiation and temperature. All calendar grouping uses Madrid
local time.

## Finding 1 — The sun creates a midday price dip that keeps getting deeper

- In every season the cheapest hours are 14:00-15:00 and the dearest are
  20:00-21:00 (`hourly_profile.csv`). In spring, the 15:00 hour averages
  **17.1 EUR/MWh** and is at zero or below **46% of the time**, against
  75-83 EUR/MWh at 20:00-21:00. Winter shows the same shape, flatter: 67-68 EUR/MWh at 14:00-15:00
  vs 114-125 EUR/MWh at 20:00-21:00, with about 3% of zero-or-negative hours.
- Holding hour, month and year fixed, **+100 W/m² of radiation goes with
  -9.2 EUR/MWh** (`weather_regression.csv`).
- Consequence for the SME: a weekday-daytime profile buys mostly in the dip.
  Over the same January-September months, its break-even price is below the
  24/7 one by **3.3 EUR/MWh in 2023, 7.6 in 2024 and 15.1 in 2025**
  (`profile_spread_by_year.csv`). The advantage doubled each year.
  October-December is excluded from this comparison because 2025 stops in
  September.

## Finding 2 — Wind lowers the price at every hour, beyond a threshold

- Across wind deciles (`price_by_wind_bin.csv`), the price is flat at about
  85-89 EUR/MWh below ~4.7 m/s, then falls steadily: **36.6 EUR/MWh on
  average (median 20.2) in the windiest tenth of hours** (above 8.8 m/s).
  The threshold matches how turbines work: little output at low wind, then
  output rising steeply with speed.
- At night, with no sun at all, the effect is just as clear: **105 EUR/MWh in
  the calmest fifth of night hours vs 51 EUR/MWh in the windiest fifth**
  (`price_by_wind_bin_night.csv`). Wind is not a proxy for the solar dip.
- Holding hour, month and year fixed, **+1 m/s of wind goes with
  -8.7 EUR/MWh**. Adding the three weather variables raises the share of
  hourly price variance explained from **37% (calendar only) to 57%**
  (`weather_regression.csv`).

## Finding 3 — Weather moves the monthly bill, but does not make it predictable

- The indexed price paid each month swings widely (`indexed_risk.csv`,
  `monthly_table.csv`): for the weekday-daytime profile, from **2.8 EUR/MWh
  (May 2025) to 138.7 EUR/MWh (February 2023)**, standard deviation
  33.4 EUR/MWh across 33 months; for 24/7, from 13.7 to 133.5, standard
  deviation 28.7. One month in ten is above about 101-104 EUR/MWh.
- Within a year, the windier half of the months was cheaper in 2023
  (-21 EUR/MWh) and 2024 (-35 EUR/MWh), but **dearer in 2025 (+12 EUR/MWh)**
  (`windy_vs_calm_months.csv`). At monthly scale, wind is mixed with season:
  the windiest months are winter months, when gas plants set the price more
  often, and the calmest are sunny spring months. The hourly regression,
  which holds the month fixed, is the cleaner estimate of the wind effect;
  the monthly view shows what the SME actually experiences.
- The sample is not atypical for wind or sun: compared month by month with
  the 1991-2020 normal, wind is within -3.5% to +1.4% and radiation within
  -0.2% to +0.9% at every point; it was warmer by +0.7 to +1.1 °C
  (`climatology.csv`).

## Bounded answer

For the energy component only, over January 2023 – September 2025:

- **Weekday-daytime SME:** the indexed contract cost **65.2 EUR/MWh** on
  average (53.8 EUR/MWh on 2024-2025 alone, after the gas cap ended). An
  indexed contract beat any fixed offer above that level, and the advantage
  of daytime consumption is growing with solar output (Finding 1).
- **24/7 SME:** **71.9 EUR/MWh** (63.2 on 2024-2025). It benefits from wind
  but not much from the solar dip.
- **The price of that saving is monthly volatility:** a month can cost twice
  the average. Indexed suits a daytime SME that can absorb monthly swings of
  about ±30 EUR/MWh; a 24/7 SME with tight cash flow is better off fixed if
  the offer is close to its break-even.

The data cannot say what the next year's price level will be: weather
explains part of the hourly variation, but the level shifts from year to
year with factors outside this dataset (gas price, 2023 gas cap).

## Limitations specific to the EDA

- **Associations, not causal effects.** The regression is descriptive; gas
  price, hydro output and demand are not in the model. Hourly residuals are
  autocorrelated, so no standard errors or p-values are reported.
- **Weather, not generation**: wind speed and radiation stand for wind
  and solar output; the threshold in Finding 2 is consistent with turbine
  behaviour but not checked against REE generation.
- **33 months** for the monthly risk measures: enough for a range and a
  standard deviation, not for a reliable tail estimate.
- **Holidays** counted as working days in the daytime profile.

## Appendix — hypotheses for further work (not tested)

1. The 2025 reversal of the windy/calm comparison comes from winter gas
   prices; adding the TTF/MIBGAS gas price would test it.
2. The growing midday dip follows installed solar capacity; REE's
   installed-capacity series (once available) would show it.
3. The very low spring 2024 prices are partly hydro (wet spring); REE's
   generation by technology would separate hydro from wind and sun.
4. Summer evening prices respond to temperature through air-conditioning
   demand; adding REE hourly demand would test it.
5. Battery storage, if it grows, should narrow the gap between the 15:00
   dip and the 20:00 peak — and so reduce the daytime profile's advantage.
