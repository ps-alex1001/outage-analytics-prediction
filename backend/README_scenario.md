# Weather Scenario Simulator

`POST /api/scenario/simulate` uses the installed evaluated model and one validated
Open-Meteo snapshot for both branches. It never fits or modifies the model.

Example body (omitted adjustments default to zero):

```json
{"mode":"storm","adjustments":{"precipitation":10,"wind_speed":20,"wind_gusts":30}}
```

Modes are `constant` and `storm`. Adjustments are additive, per source location.
Pydantic `Adjustments` in `scenario.py` defines configurable request limits; the
matching slider configuration is in `frontend/src/lib/scenario.ts`. These are
application experiment limits, not measured training-support ranges. Wind deltas
are km/h at the API boundary and converted to m/s before feature engineering.
Humidity deltas are percentage points. Soil moisture is the 7–28 cm layer.

Storm intensity is `sin(pi*(hour+0.5)/24)^2`, normalized to peak at 1, with equal
peaks at positions 12 and 13. No random weather is generated. Adjustments apply
only to 24 source labels beginning at issue-time ceiling hour. Historical labels,
terrain, calendar and geographic mapping stay unchanged. The shared feature builder
then recomputes lagged values, rolling windows, rain streaks and interactions.

The first predicted interval is unchanged: its last weather label precedes the
adjustment boundary. The final modified source hour lies beyond the last scored
cutoff. `weather_hourly` aligns with the last input label for each target interval,
not the weather *inside* that interval. This preserves `(t,t+1h]` predictions using
only labels through `t-1h`.

Physical clipping is explicit and counted per source location. Humidity stays in
0–100, soil moisture in 0–1, temperature in −90–60°C, other variables nonnegative.
When wind adjustments are nonzero, finite modified gusts are raised to wind speed
where necessary. Existing provider inconsistencies are preserved for zero adjustments.
Missing inputs are never filled; incomplete features suppress the corresponding
score and paired difference. Stale snapshots are rejected with HTTP 503.

The response includes all 5,688 barangay-hour pairs, separate missing-feature lists
and statuses, exact string PSGCs and timestamps, lead hours, location links, model
and snapshot identifiers, hourly baseline/scenario input values, units, intensity
profile and clipping counts. Run initialization/publication timestamps remain null
because the existing weather product does not expose them. HTTP 422 rejects invalid
modes, unknown fields, nonfinite adjustments and out-of-range inputs.

The `/scenario` page retains the returned batch in local state. Playback advances
one shared frame index and never requests inference. Settings changes pause and mark
old results as outdated. Scores expire when the weather snapshot reaches one hour
of age. Baseline/scenario maps share a 0–100% relative color scale derived from their combined full-batch min/max plus a 5% range margin; raw scores stay unchanged. difference uses symmetric limits at the
largest absolute valid difference in the full 24-hour batch, centered at zero and
fixed throughout playback. Actual numeric limits and batch difference statistics
are displayed. Zero-only batches stay neutral without inventing a nonzero range.
Weather charts show means over available unique source locations (CAPE uses its own
cells), with counts and accessible tables. These means are not barangay-weighted.

All results are experimental synthetic-outage onset scores. Differences describe
model sensitivity to hypothetical inputs, not causal effects. Forecast-weather
source shift remains unvalidated; there is no live active-outage feed.

Targeted verification:

```powershell
.venv/Scripts/python.exe -m unittest backend.tests.test_scenario -v
```

Tests cover zero-adjustment identity using the installed native model across all
237 barangays, unchanged history, unit conversion, geographic offsets, cutoff
alignment, rolling-feature missingness, physical constraints, deterministic storm
shape, stale suppression and the POST/validation contract. No weather network calls
or retraining occur in these tests.

