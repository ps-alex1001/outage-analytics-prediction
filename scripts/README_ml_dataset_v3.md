# V3 ML preparation and Colab experiment

V3 event source: `data/synthetic/v3_20261029T115229_718511Z/synthetic_outage_events_v3.csv` (6,017 synthetic events). V1/V2 data, builders, notebooks and original native model are preserved.

## Accepted dataset

`data/ml/v3` contains 90 original monthly Parquet partitions, the explicit predictor list, split manifest, feature manifest, quality report and independent validation receipt. The full scan checked 15,571,848 keys/labels and 373,724,352 numerical feature values. There are 15,556,234 eligible rows, 6,015 positive onset windows and 15,614 excluded rows. Two source events occur in excluded windows and are reconciled explicitly.

| Cutoff period | Eligible rows | Positive onset windows |
|---|---:|---:|
| 2019–2023 training | 10,373,524 | 4,177 |
| 2024 validation | 2,079,746 | 763 |
| 2025–June 2026 test | 3,102,964 | 1,075 |

The original 24 numerical predictors, one-hour weather lag, rolling windows, terrain joins, eligibility rules, split boundary purge and `(t,t+1h]` target are unchanged. Municipality PSGC remains a ten-digit string in Parquet and becomes twelve one-hot predictors in training/serving (36 model inputs). These are origin-barangay onset labels; downstream impacts are visualization consequences and never become predictors. Required weather missingness remains null; training preprocessing is fitted on training rows only. Serving still suppresses scores when required weather is missing or stale.

Same-year NDVI and its gust interaction remain excluded because publication-time availability is not established. This omits information present in the synthetic generator. V3 onset draws remain stochastic: its recorded maximum candidate occurrence probability is about 1.09%, so highly confident individual-event predictions are not guaranteed by reusing the environmental formulas.

## Topology

`scripts/map_outage_topology_impacts_v3.py` uses an explicit six-cause adapter for source selection and preserves exported V3 cause names. It follows existing active downstream connectivity without upstream enlargement. `data/impacts/v3_topology_1_0_0` contains 5,998 mapped events, 19 unresolved disconnected local origins, and 4,491 independently traversed impact sets. Branches without downstream transformers remain flagged; no affected customer counts or whole-barangay failures are invented.

## Reproducible Colab protocol

Duplicate notebook: `notebooks/PENELCO_ML_V3_Colab_Reviewed.ipynb`.

Reviewed workflow: `scripts/train_ml_dataset_v3_colab.py`. Its actual source-file SHA256 is `dc63c11e57fa6a46151ab9b7c29b8040281723fccfc432f4a7161ce6c1bd2d8a`.

Verified input bundle: `data/ml/v3/penelco_ml_v3_inputs.zip` (59,550,185 bytes, SHA256 `ef115b8810aebf8bf3764468db740236cf2e3005ea1658b3d9dbed8e2dcb10e8`). Upload to `MyDrive/PENELCO_ML_V3/`, mount Drive, then run the notebook in order. The dataset metadata ID is `a016e84213b7be61e6f5cb697921600965636d2b5491d5863f69e504ec2152a7`.

1. All 4,177 training positives plus 400,000 negatives, proportionally stratified by year/month/municipality, form one shared sample for all seven original algorithms. Fixed seeds, sample keys and quotas are saved.
2. Average precision on the entire unsampled 2024 population ranks Logistic Regression, Decision Tree, Random Forest, Histogram Gradient Boosting, XGBoost, Extra Trees and AdaBoost.
3. The strongest two repeat on one larger shared sample with 1,200,000 negatives and every training positive. The confirmation winner is selected without substituting a cheaper algorithm.
4. The actual confirmed winner and its preprocessing are fitted on every eligible training row. Benchmark sample scores are not deployment scores.
5. A validation threshold allowing at most one alert per thousand intervals, including whole tied score groups, is frozen before test evaluation. Final test predictions/metrics are saved and reused on reruns.
6. The original frozen V2 model is also scored against the same V3 targets where its original Drive export is available. It does not select or tune the V3 model.

Results and checkpoints: `MyDrive/PENELCO_ML_V3/training_outputs/reviewed_v3/`. The cloud notebook runs the checksum-pinned saved workflow in one cell and displays its saved stages below; the local duplicate separates the same workflow into ordered cells. Both use the identical reviewed training source/protocol. Benchmark `fit_seconds` includes validation prediction time; the full-training duration measures the final fit.

## Serving and activation

`v3_serving_export.zip` contains the model, manifest and 153 independent prediction parity cases spanning all twelve municipalities and missing-input imputation cases. XGBoost uses native UBJ; the other six algorithms use non-executable numerical JSON. The service never unpickles a model. All six numerical formats were compared to their fitted scikit-learn estimators on a generated software fixture before the real experiment.

Install and activate only the evaluated export:

```powershell
.venv/Scripts/python.exe scripts/activate_v3_model.py --archive PATH_TO_V3_SERVING_EXPORT.zip
```

Activation verifies dataset/event checksums, independently validated topology, all training rows, full chronological evaluation, confirmed first place and local prediction parity. It atomically writes `data/active_bundle.json`, coupling events, topology impacts, ML geography and model. Restart the backend after activation or rollback so imported analytics tables resolve that bundle together. An explicit `FORECAST_MODEL_DIR` overrides bundle model selection and should be cleared for the ordinary bundle deployment.

Preserved V2 rollback:

```powershell
.venv/Scripts/python.exe scripts/activate_v3_model.py --rollback-v2
```

Deployment receipts are saved under `data/models/deployments/`. No activation occurs merely by uploading the notebook or finishing a sample benchmark.

## Validation and limits

The builder records before/after source and application hashes at export time. Independent revalidation checks immutable archived data, all direct ML inputs, the exact builder, every feature and every target; later application integration edits do not invalidate an unchanged ML matrix. The original interrupted export is retained under `data/ml/v3_incomplete_20261029` and is not used for training.

These are retrospective synthetic-onset experiments, not real PENELCO outage history, calibrated operational outage probabilities, duration forecasts or customer-impact predictions. Archived forecast vintages and issue-time availability have not been validated. Historical Open-Meteo exposure is shared between municipalities at seven coordinate pairs; ERA5 CAPE has its original coarse spatial assignment. The first eight hours of 2019 are unavailable. No predictions are increased for map appearance, and map scales are unchanged by this pipeline.


## Completed run — 29 October 2026

AdaBoost won confirmation, completed all 10,373,524 eligible training rows, and is active as `models/outage_v3_adaboost_13f641fde2e3`. All 153 local parity cases passed. Live forecast, historical replay and synthetic storm each returned 5,688 available scores from this model. The matching V3 bundle is recorded in `data/active_bundle.json`.

The full deployment report and unchanged test evidence are in `data/models/v3_experiment_reviewed_20261029/deployment_report.md`. Test average precision improved from 0.000657071 for the frozen V2 control to 0.000752458 on the same V3 targets. Calibration is much worse: mean raw score 0.169117 versus observed onset frequency 0.000346443. Both detect 9 test onsets at their frozen thresholds; V3 has more false alerts. Treat the deployed output strictly as an experimental classifier score, never a percent chance. No scores were inflated.
