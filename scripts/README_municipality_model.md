# Municipality-feature model experiment

Run `scripts/train_municipality_model.py` with the existing project environment.
The script uses disk-backed XGBoost and two CPU threads. It preserves all eligible
2019–2023 training rows, evaluates full unsampled 2024 validation, and only evaluates
the already-used 2025–June 2026 test period after selection is frozen. That test is
an explicitly reused benchmark, not a new independent confirmation.

The 24 existing predictors are retained. Twelve one-hot columns encode exact
municipality PSGC strings. No ordinal PSGC numbers, generator propensity, annual
future-looking NDVI, event cause/duration or future weather enter the predictors.
The existing training-fitted medians are reused for the unchanged base features.
No class weighting, score rescaling or artificial score inflation is applied.

Three predeclared configurations use depths 3, 5 and 4, training up to 400 rounds
with early stopping on validation average precision. Selection requires improvement
in validation average precision and no recall regression under a budget of at most
one flagged barangay-hour per 1,000 validation rows. Thresholds respect whole tied
score groups. The highest eligible AP wins. The live model is not changed by the
training script. Artifacts, plan and reports are in the ignored directory
`data/models/municipality_experiment_v1/`.

Serving supports legacy artifact version 1 (24 features) and version 2 (24 base
features plus the exact 12 categories). Both validate native model and parity-file
hashes, dimensions, objective and reference predictions. Municipality encoding is
shared across Forecast, Scenario, Forecasted synthetic replay and historical replay.
Missing base features still suppress scores before inference.

After verification, `data/models/active_forecast.json` can point at the versioned
directory with `{"directory":"outage_v2_municipality_v1"}` after running
`scripts/activate_municipality_model.py <downloaded-export.zip>`. The original
`outage_v2_xgboost` directory stays intact; pointing back to it rolls back. The
explicit `FORECAST_MODEL_DIR` environment override takes precedence. ForecastService
detects pointer changes and loads the corresponding model; forecast score caches
and historical replay caches are keyed by model identity.

The synthetic generator samples rare events with random variation. Adding identity
may learn geographic allocation, but cannot reveal random draws or event-hour weather
not available to the prediction cutoff. A useful model needs better discrimination
and honest probabilities; numerically larger outputs alone are not an improvement.
