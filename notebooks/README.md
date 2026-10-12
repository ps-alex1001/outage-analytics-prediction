# Guided notebooks for the Bataan synthetic outage demonstration

The first deliverable is [01_synthetic_outage_eda.ipynb](01_synthetic_outage_eda.ipynb). Its saved tables and plots allow review before rerunning. The [source inventory](assessment/dataset_inventory.md) records current and legacy source paths, schemas, sizes, provenance, concerns and intended uses. No dataset or application was modified.

## Proposed sequence — only lesson 01 exists

| Phase | Proposed notebook | What you learn / decision it supports |
|---|---|---|
| A | **01_synthetic_outage_eda.ipynb** | Event schema, synthetic provenance, missingness/duplicates, local timestamps, durations, geographic keys, exposure-adjusted temporal and geographic comparisons. |
| A | 02_weather_coverage_and_event_context.ipynb | Municipal vs gridded weather, UTC/Manila alignment, coverage/missingness/units, accumulation intervals, bounded event-weather windows; distinguish historical context from forecast availability. |
| A | 03_vegetation_terrain_and_simulated_associations.ipynb | Static elevation/slope vs annual NDVI, raster/summary compatibility, NDVI observation/publication windows, non-event comparison exposure; descriptive relationships with method, sample size, dependence, confounding and simulator caveats. |
| B | 04_existing_panel_and_feature_availability_audit.ipynb | Selective Parquet/SQL inspection of existing panels; grain, zero vs unknown, class balance, target/feature roles, geography joins; remove out-of-scope infrastructure, generator scores, outcomes and unavailable annual NDVI. |
| B | 05_forecast_horizons_and_temporal_validation_design.ipynb | Explicit forecast issue time and 24/48/72-hour targets; hourly vs daily choice, non-overlapping or purged windows, chronological/geographic evaluation, lag/rolling feature availability, historical/forecast input compatibility. Design small examples before any approved large materialization. |
| C | 06_naive_and_frequency_baselines.ipynb | Training-period-only prevalence, seasonal/local frequency baselines, imbalance, precision/recall and PR-AUC; why accuracy is misleading; compare against simple defaults. |
| C | 07_logistic_and_tree_experiments.ipynb | Empirical model comparison, train-only preprocessing, imbalance choices, validation-selected thresholds, feature contributions and geographic limitations; preserve untouched final test. |
| C | 08_calibration_and_forecast_compatibility.ipynb | Reliability plots, Brier score, held-out probability calibration, final test evaluation once choices are frozen; verify actual Open-Meteo forecast schema, units, issue/lead times and missingness before integration. |
| D | 09_model_artifact_and_api_contract.ipynb | Only after evaluation gates: reproducible model/preprocessing artifact, precise fields/units/horizon/probability meaning, example FastAPI request/response contract and small dashboard summaries. No FastAPI or Next.js implementation. |

Each future lesson will have objectives, source introduction, concept explanation, small code cells, interpretation, checks, exercises and a summary. Notebook 02 begins only after feedback on 01. Each phase transition requires your approval. This is a proposed sequence, not authorization to start modeling.

## Run in the uv-managed PyCharm environment

From the repository root:

```powershell
uv sync
```

In PyCharm, select `.venv/Scripts/python.exe` as the notebook interpreter, set the working directory to the repository root or `notebooks`, restart the kernel, then run all cells in order. No separate global kernel registration is required.

The recorded project dependencies support the notebook and bounded inventory inspection. No web application dependencies were added. `uv.lock` pins the installed versions. There are no installation or network cells in the notebook.

To repeat execution and produce the HTML preview using the exact project interpreter:

```powershell
uv run python notebooks/support/execute_first_notebook.py
```

This writes only the executed lesson and small review artifacts under `notebooks/assessment`. The notebook itself only reads sources and creates in-memory summaries. The support scripts are maintenance helpers; the lesson has no dependency on them or the legacy panels.

## Review and practice

Read the synthetic caveat, checksum and coverage explanation first. Run the quality guards and trace each chart's denominator. Practice the municipality filter, matched January–June comparisons, percentiles, and bottom-10 view. Explain why complete synthetic data and familiar patterns do not prove real forecasting accuracy.

The schema checks are intended to stop on unexpected changes rather than silently cleaning data. If one fails, inspect and report the source discrepancy before proceeding.
