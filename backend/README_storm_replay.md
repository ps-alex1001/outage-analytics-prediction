# Storm Replay Simulator

`/scenario` now opens the replay experience. The original adjustment simulator and
its endpoint remain available under **Advanced · weather-adjustment experiments**.
The Forecast and Spatial Analysis pages and frozen model are unchanged.

## Supplied file and inspection

Original: `D:/Downloads/ibtracs_phl_hourly_processed_imputed.csv`.
Unmodified copy: `data/storm_replays/ibtracs_phl_hourly_processed_imputed.csv`.
SHA256: `96b201cca5813036b154a31178daf5dd794b694ec7b5ee124e91e1a5e509791d`.
The existing `data/` ignore rule keeps this file and local receipts out of Git.

The CSV has 44,540 rows, 228 storm IDs, hourly timestamps from 2019-01-01 through
2026-09-06 08:00 PHT, no duplicate storm-hour keys and no internal hourly gaps.
Columns: timestamp_pht, storm_id, storm_lat, storm_lon, wmo_wind, wmo_pres,
distance_to_municipality_km, year. Wind and pressure each have 2,882 missing values.
Track latitude ranges −33.6 to 62.5 and longitude 31.3 to 194.7, so the filename does
not imply every track position is within the Philippines. Distance calculations
handle longitude wrapping through trigonometric coordinates.

This is **processed/imputed storm-track and storm-intensity data**, not local hourly
weather observations. It lacks storm names, measurement unit definitions,
per-value imputation flags and a municipality/reference for the supplied distance.
No historical names or local weather are invented. The UI uses original storm IDs.
Only records in 2025–2026 appear in the catalog, as requested; the copy is unfiltered.

## Historical weather and inference

`GET /api/storm-replays` returns the restricted catalog plus one synthetic card.
`GET /api/storm-replays/{storm_id}` loads a fixed 24-hour window centered around
closest approach to an explicitly stated Bataan reference (14.67 N, 120.47 E),
constrained to the supplied track coverage. The unspecified distance column is not
used. Track durations describe the span in the supplied data, not independently
verified storm lifetimes. Windows without a full 24-hour track span or outside the
common weather archive are marked unavailable.

Local Open-Meteo historical weather contains 788,544 municipality-hour rows across
12 municipalities from January 2019 through June 2026, including all six required
local weather variables without nulls. ERA5 supplies instantaneous CAPE in J/kg on
the existing 0.25-degree grid. The original V2 geography assigns 12 municipal
weather series at seven shared coordinates and four ERA5 CAPE cells to all 237
barangays. The historical loader retains each municipal series separately:
co-located municipal temperatures can differ, so coordinates are not a safe
deduplication key for historical inference.

The loader reads 74 pre-cutoff source hours plus the replay window. It retains the
original UTC+08 municipal labels, converts ERA5 UTC labels to Asia/Manila, selects
the exact assigned ERA5 cells, and calls the existing `build_rows` and frozen native
XGBoost model. Missing/invalid values remain missing and incomplete feature rows
return null scores. No storm-level wind or pressure is substituted for local weather.
The V2 municipal name alias `Balanga` → `City of Balanga` is preserved for the
historical CSV join, including its shared weather coordinate with Pilar.
No model fitting or weather fabrication occurs. The last weather label at cutoff
`t` is `t−1h`; scores describe synthetic onset in `(t,t+1h]`. Weather charts/map
show that last **input** hour, not conditions within the target interval.

Historical responses contain 5,688 scores, exact string PSGCs, cutoff/start/end and
source timestamps, missing-feature lists, location links and provenance. A bounded
four-replay cache avoids repeated historical inference. No inference is requested
during playback. Restart the backend after replacing source files to clear caches.

These are **retrospective experimental model scores**, not validated historical
forecasts, real outage probabilities or reconstructions of actual outages. The
weather archive contains gridded estimates, not proof of information available at
each historical issue time; some replay dates were used in the original model's
evaluation. There is no active-outage feed.

## Forecasted card

`GET /api/storm-replays/forecasted` reuses the existing scenario backend on the latest
validated forecast snapshot, with a documented deterministic storm-evolution preset:
peak +10 mm/h rain, +20 km/h wind, +30 km/h gusts, −2°C temperature, +10 humidity
percentage points, +0.05 m³/m³ soil moisture and +500 J/kg CAPE.

The card is titled **Forecasted** with an explicit **synthetic storm scenario**
label. It is not a real named storm forecast. The existing backend applies physical
constraints, preserves past input values, suppresses incomplete scores, and returns
its actual scenario predictions. Preset, intensity profile, snapshot and clipping
are retained in provenance. Stale snapshots are rejected; an open synthetic replay
expires at one hour. Synthetic results have no fabricated historical storm track.

## Map, charts and playback

The existing MapLibre component is reused. Weather Intensity displays circles at
the seven actual source-point coordinates, using rainfall (mm/h) or wind (km/h).
Historical display values average available co-located municipal series, retaining
contributing counts for partial coverage. This aggregation is for map/charts only,
not model features. It does not paint fabricated barangay-level weather or imply
continuous coverage between points. Underlying Open-Meteo grid resolution is not
supplied by the CSV.
Weather bounds use the complete replay batch and remain fixed for playback. Missing
points are separate. Model Score colors use full-batch min/max plus a 5% range margin, labeled Relative model score (0–100%, not outage probability). Raw 0–1 scores remain in tooltips/details. Bounds are fixed for all 24 hours and shared by barangays and municipality means. Equal-score batches stay neutral; missing values stay unavailable.
The replay opens in Model Score / Municipality view, with a municipality/barangay
boundary toggle. Municipality fill is the arithmetic mean of available barangay
scores, with coverage counts for the selected municipality. It is a display summary,
not a probability of any municipal outage. Missing scores are excluded, all-missing
municipalities stay unavailable, and valid zero scores remain zero. Both boundary
sources are cached and initialized on one MapLibre instance. Boundary changes and
playback reuse the returned scores without new inference.

Play/Pause, previous/next, timeline and 0.5×/1×/2× speeds share one local frame index
with weather charts, source-time labels, storm center and barangay score details.
At 1×, a frame lasts one second. Playback stops at hour 24; Replay restarts at hour 1.
Weather means are across unique available source points, with source counts and
accessible tables. Colorblind, light/dark and responsive layouts are preserved.

## Targeted checks

```powershell
.venv/Scripts/python.exe -m unittest backend.tests.test_storm_replay.ReplayTests -v
cd frontend
node --test tests/storm-replay.test.mjs
```

Tests cover the restricted catalog, unknown/unavailable windows, missing weather,
unit conversion and cutoff alignment, explicit synthetic provenance, a real full
province historical replay with the installed native model, cache reuse and playback
without requests. Local inspection and replay receipts are in `data/storm_replays/`.

Validated initial replay: storm ID `2025289N13140`, 18 October 2025 23:00 through
19 October 2025 23:00 PHT, with 5,688 available scores and no missing scores.
All 136,512 feature values (5,688 rows × 24 features) match the original V2 October
2025 Parquet partition exactly. The catalog contains 57 storm IDs with 2025–2026
records; 43 have eligible windows in the current weather archive and 14 do not.
`feature_parity.json`, `inspection.json` and `replay_validation.json` retain these
local checks. Other catalog storms have not each been exhaustively validated.

