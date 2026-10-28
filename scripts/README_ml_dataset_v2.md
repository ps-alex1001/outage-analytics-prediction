# Synthetic V2 research dataset

This workstream creates a one-hour-ahead **retrospective research panel**. It does
not train a model, generate outages, rebuild topology, or modify the application.
Read the executed `notebooks/01_eda_feature_engineering_v2.ipynb` for the teaching
walkthrough and `data/ml/v2/ml_data_quality_report.md` for the audit.

## Reproduce

Use the existing uv-managed environment from the repository root. Do not use pip.

```powershell
uv --cache-dir .venv/uv-cache run --offline python scripts/build_ml_dataset_v2.py --output data/ml/v2_reproduced
uv --cache-dir .venv/uv-cache run --offline python scripts/validate_ml_dataset_v2.py --output data/ml/v2_reproduced
uv --cache-dir .venv/uv-cache run --offline python -m unittest discover -s scripts/tests -p test_ml_v2.py -v
uv --cache-dir .venv/uv-cache run --offline python notebooks/support/execute_eda_feature_engineering_v2.py
```

The builder refuses an existing output directory. `--help` exposes configurable
source, legacy-panel, output and split paths. The default V2 event path is selected
only when exactly one V2 event CSV exists, then checked against its run manifest.
The legacy sources default to a sibling `Outage Prediction` project; pass
`--legacy-panel` and `--legacy-builder` when they live elsewhere. No legacy target
or infrastructure feature is copied into the new panel. The same installed uv
interpreter can also be invoked as `.venv/Scripts/python.exe` when the uv launcher
is locked; this does not install or change dependencies.

The notebook reads completed audits from `data/ml/v2`. It never silently launches
the full materialization. Its shared computation functions come from the builder.
`notebooks/support/build_eda_feature_engineering_v2.py` recreates the notebook
source; execution saves its results and an HTML review copy in `data/ml/v2/audit`.
To review a separately reproduced output, point the notebook's `OUT` parameter at
that directory before execution.

## Safe loading contract

1. Read `ml_predictor_columns.json`; only these columns are model inputs.
2. Require `is_eligible == true`, and select the requested chronological `split`.
3. Keep `outage_next_hour` separate from predictors. Excluded rows have null targets.
4. Preserve leading-zero PSGC strings. They are join/grouping metadata.
5. Warm-up feature nulls remain null. Fit any required imputer on eligible training
   data only; do not silently remove eligible negatives or convert missing rain to zero.

Partition folders are `year=YYYY/month=MM/part-000.parquet`. Read individual files
or an Arrow dataset with Hive partition discovery; folder year/month values are
partition metadata, not extra members of the predictor whitelist. The compact CSV
preview deliberately contains positives and negatives and is not a class-balance estimate.

## Timing and limitations

Target interval: `(t, t+1h]`, using the synthetic **origin** barangay. Active intervals
are `[start,end)` and are masked. The last incomplete horizon and the cutoff just
before each split boundary are masked. All other eligible negative hours remain.
Every weather window uses complete observation labels ending at `t-1h`.

Observation ordering is causal, but the inputs are retrospective reanalysis.
Actual publication-time availability is unverified; ERA5T arrives days later.
Annual NDVI includes future imagery and has no verified publication dates, so NDVI
and the wind–NDVI interaction are deferred. Source wind/gust inconsistencies and
shared coordinates are quantified in the report. Preserve those limitations in
any later model study: synthetic performance cannot establish PENELCO forecast accuracy.

The independent validator scans every exported key, target, mask and retained
feature value using separate NumPy windows and sorted timestamp searches. Its
receipt is `audit/validation.json`. Source/application preservation is verified
with SHA-256 hashes, not inferred from successful execution.
