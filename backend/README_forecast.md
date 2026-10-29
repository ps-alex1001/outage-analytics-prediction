# Experimental forecast backend

The evaluated V2 XGBoost model is served without retraining. Forecast-driven scores
are **experimental synthetic-model outputs**, not calibrated probabilities of real
PENELCO outages. The historical held-out result (8 detected positives, 2,213 false
positives, 1,922 missed positives) does not validate performance on forecast inputs.
There are no operational alert decisions, traffic-light risk classes, duration,
cause, customer-impact or equipment-failure predictions.

## Start and verify

Use the existing uv project environment:

```powershell
uv --cache-dir .venv/uv-cache sync
uv --cache-dir .venv/uv-cache run python scripts/check_forecast_backend.py
uv --cache-dir .venv/uv-cache run python scripts/check_forecast_backend.py --live
uv --cache-dir .venv/uv-cache run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

- `GET /api/forecast/metadata` checks installed model/feature/geography readiness,
  including native prediction parity. It performs no weather request.
- `GET /api/forecast/latest?hours=24` returns 237 × 24 hourly research scores.
- Optional `municipality_psgc` and `barangay_psgc` filters take 10-digit strings.
  Invalid query formats return 422; unknown or inconsistent locations return 404.
- `hours` is bounded to 1–48. Model/data failures or unavailable weather return 503.
  Upstream weather messages do not expose internal API requests.

The existing GET-only CORS policy and other backend routes are preserved. Weather
is refreshed on demand, with a 30-minute cache, a process-local single-flight lock
and a 60-second failure cooldown. No scheduler or browser-triggered training is used.
Each request scores at most 11,376 rows, with the complete 48-hour batch cached in
memory. Scoring is CPU-only and does not load the 15-million-row panel.

## Model installation

The preserved original installation is `data/models/outage_v2_xgboost/`, containing
`manifest.json`, `model.ubj` and `parity.json`. The export was produced from the
evaluated frozen model in Colab, including the original fitted median imputer,
exact feature order, source bundle checksum and 153 reference predictions (including
missing-input cases). Native XGBoost predictions must match those references before
the backend reports ready. Model and parity file checksums are verified on load.
Only native model files are loaded; the backend never unpickles a joblib file.

All `data/` assets are locally ignored by Git. On another machine, transfer the
portable export from Drive and install it with:

```powershell
uv --cache-dir .venv/uv-cache run python scripts/install_forecast_model.py D:/Downloads/penelco_xgboost_serving_v1.zip --sha256 69fb73094e5a8fb55422437d555124047f764565b30efd9f8cee6c7ed0b6bf6d
```

The installer rejects extra archive paths, corrupt files and parity mismatches.
An identical installation is idempotent. A different model must be installed into
a new directory. The optional `data/models/active_forecast.json` selects a verified
versioned installation by directory name. The service notices pointer changes;
model IDs invalidate forecast and historical replay caches. `FORECAST_MODEL_DIR`
remains an explicit override and takes precedence over the pointer.

The municipality-aware artifact version 2 retains all 24 original weather,
terrain and calendar predictors and adds 12 one-hot municipality columns. Their
order and exact string PSGCs are stored in the model manifest. All inference paths
use the same encoder. Version 1 artifacts remain supported for rollback. Read
`scripts/README_municipality_model.md` for the training and promotion protocol.
The evaluated native artifact uses XGBoost 3.4.1, pinned in `pyproject.toml`/`uv.lock`.

## Weather and feature contract

Open-Meteo `/v1/forecast` is requested with `models=ecmwf_ifs025`, UTC timestamps,
m/s wind, mm precipitation, Celsius temperature, percent humidity, m³/m³ soil water
at **7–28 cm**, and J/kg CAPE. The request includes 74 preceding hourly labels and
49 forecast hours. Missing variables, incompatible units, duplicates, invalid
ranges or non-hourly timestamps are rejected. Null values and gaps remain missing;
rain is never silently zero-filled. Gusts below wind speed are counted in provenance
and retained, consistent with the audited training source.

The live source check found a negative CAPE sample (-20 J/kg). Negative CAPE is
preserved in the raw archive and mapped to missing in the feature input, with
`invalid_value_counts` reported per location. It is never clipped to zero. Affected
three-hour CAPE windows return no score; unrelated hours remain usable.

Seven original municipal weather coordinates supply the municipal predictors;
four original ERA5-cell coordinates supply forecast CAPE. Coordinates are reused
across barangays using `data/ml/v2/audit/geographic_crosswalk.csv` and
`weather_coordinate_groups.csv`. Static terrain comes from
`data/terrain/bataan_barangay_terrain.csv`. These joins are validated at startup
on first access. API-returned grid coordinates are also recorded because forecast
grid cells are not the same measurement support as the historical sources.

The standalone feature engine reproduces the V2 builder's exact formulas and
float32 feature order. At target cutoff `t`, complete trailing windows end at
source label `t-1h`; the target is `(t,t+1h]`. The rainy streak uses `>0.1 mm`, caps
at 72 hours, and remains unknown at a wet left boundary until a dry reset or cap.
Wind-gust change is `gust(t-1h)-gust(t-4h)`. Calendar values describe `t` in Manila,
not retrieval time. See `scripts/build_ml_dataset_v2.py` as the controlling training
specification; the older draft notebook weather contract is not the model signature.

At an off-hour issue (for example 20:15), the first cutoff is the next whole hour
(21:00), predicting `(21:00,22:00]`. This avoids presenting an interval that already
began as an entirely future prediction. Responses include the exact issue/cutoff,
target bounds, lead time and last weather label. Every future cutoff uses only the
same already-retrieved snapshot; subsequent observed weather and previous outage
predictions are not fed into later rows.

Complete feature rows receive scores. Incomplete rows return `score: null`,
`status: insufficient_weather`, feature coverage and the missing feature names.
The fitted imputer is retained for exact artifact parity but is not used to disguise
missing live weather. On a refresh failure, a previously validated snapshot up to
60 minutes old can be returned as `stale_weather`, with **all scores suppressed**.
Older snapshots return 503. No stale data is presented as fresh inference.

Raw responses and retrieval metadata are archived under `data/forecasts/weather/`.
`FORECAST_CACHE_DIR` changes that path. Apply a deployment-specific archive retention
policy outside the request path. `FORECAST_WEATHER_MODEL` can change the upstream
model only after verifying variable support and repeating the compatibility study.

The generic forecast product can stitch model runs. The backend records retrieval
time and snapshot identity; it does not invent initialization/publication timestamps
that are absent from the response. `run_initialized_at` and `published_at` are null.
Past model estimates returned by this endpoint are **not station observations**.
An archived-vintage, lead-specific evaluation is still needed for forecasting claims.

There is currently no live active-outage feed. Each score explicitly assumes no
active outage at that target cutoff and reports `current_outage_status:
unknown_no_live_feed`. Historical synthetic outages are not passed off as current
operational status. Connect an authoritative outage-status feed before using this
model to assess onset eligibility operationally.

## Tests

```powershell
uv --cache-dir .venv/uv-cache run python -m unittest backend.tests.test_forecast -v
uv --cache-dir .venv/uv-cache run python -m unittest discover -s backend/tests -v
```

Tests cover parity with the original V2 feature builder, missing hours and rain
streak boundaries, future-value causality, source unit/range validation, stale
cache handling, API validation, native Colab prediction parity and tampering.
The live check uses actual source data and writes `data/forecasts/backend_validation.json`;
it establishes integration, not forecast accuracy.

References: [Open-Meteo forecast semantics](https://open-meteo.com/en/docs),
[ECMWF variables](https://open-meteo.com/en/docs/ecmwf-api),
[archived single runs](https://open-meteo.com/en/docs/single-runs-api),
[XGBoost native model persistence](https://xgboost.readthedocs.io/en/stable/tutorials/saving_model.html).


## Versioned V3 deployment

The reviewed V3 workflow and dataset protocol are documented in `scripts/README_ml_dataset_v3.md`. `data/active_bundle.json`, when installed by `scripts/activate_v3_model.py`, resolves the event CSV, topology impacts, ML geographic audit and frozen model together. Restart the backend after changing this pointer. `FORECAST_MODEL_DIR` remains an explicit model override; ordinary bundle deployment should leave it unset. Without a bundle pointer the pre-existing dataset/model defaults apply.

The model loader accepts the original native XGBoost exports and checksum-verified numerical JSON exports for the other six benchmark algorithms. It never loads executable model pickles. Input feature order, one-hot municipality encoding, fitted imputer parameters and reference predictions are verified before readiness. Metadata also exposes the selected algorithm and dataset version. Forecast, Advanced Scenario and historical replay call the same loader and feature encoder; missing/stale weather behavior is unchanged.

A V3 candidate is activated only after full training, chronological evaluation, confirmed ranking and local serving parity. The preserved V2 bundle can be restored with `scripts/activate_v3_model.py --rollback-v2` and a backend restart. No model is retrained inside the API.
