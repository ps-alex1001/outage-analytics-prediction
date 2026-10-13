# Guided notebooks for the Bataan synthetic outage demonstration

The first deliverable is [01_synthetic_outage_eda.ipynb](01_synthetic_outage_eda.ipynb). Its saved tables and plots allow review before rerunning. The [source inventory](assessment/dataset_inventory.md) records current and legacy source paths, schemas, sizes, provenance, concerns and intended uses. No dataset or application was modified.

## Notebook sequence — lessons 01 and 02 completed

| Phase | Proposed notebook | What you learn / decision it supports |
|---|---|---|
| A | **01_synthetic_outage_eda.ipynb** | Event schema, synthetic provenance, missingness/duplicates, local timestamps, durations, geographic keys, exposure-adjusted temporal and geographic comparisons. |
| A | **[02_weather_coverage_and_event_context.ipynb](02_weather_coverage_and_event_context.ipynb)** | One actual synthetic event; hourly rain/wind checks, temperature/humidity/pressure/soil-moisture timelines, ERA5 atmospheric context, before/during/after summaries and shared-weather overlap checks. Retrospective/prediction-time limits remain explicit; broader 72h/antecedent investigations remain later work. |
| A | 03_vegetation_terrain_and_simulated_associations.ipynb | Static elevation/slope vs annual NDVI, raster/summary compatibility, NDVI observation/publication windows, non-event comparison exposure; descriptive relationships with method, sample size, dependence, confounding and simulator caveats. |
| B | 04_existing_panel_and_feature_availability_audit.ipynb | Selective Parquet/SQL inspection of existing panels; grain, zero vs unknown, class balance, target/feature roles, geography joins; audit candidate weather windows and availability; remove out-of-scope infrastructure, generator scores, outcomes and unavailable annual NDVI. |
| B | 05_forecast_horizons_and_temporal_validation_design.ipynb | Explicit forecast issue time and 24/48/72-hour targets; hourly vs daily choice, non-overlapping or purged windows, chronological/geographic evaluation, lag/rolling feature availability, historical/forecast input compatibility. Design small examples before any approved large materialization. |
| C | 06_naive_and_frequency_baselines.ipynb | Training-period-only prevalence, seasonal/local frequency baselines, imbalance, precision/recall and PR-AUC; why accuracy is misleading; compare against simple defaults. |
| C | 07_logistic_and_tree_experiments.ipynb | Empirical model comparison; compare raw lagged weather with rain/wind/antecedent window families, ablate candidates and retain useful features only. Train-only preprocessing, imbalance choices, validation-selected thresholds and geographic limitations; preserve untouched final test. |
| C | 08_calibration_and_forecast_compatibility.ipynb | Reliability plots, Brier score, held-out probability calibration, final test evaluation once choices are frozen; verify actual Open-Meteo forecast schema, units, issue/lead times and missingness before integration. |
| D | 09_model_artifact_and_api_contract.ipynb | Only after evaluation gates: reproducible model/preprocessing artifact, evaluated feature whitelist, precise fields/units/windows/availability/horizon/probability meaning, example FastAPI request/response contract and small dashboard/timeline/forecast summaries. No FastAPI or Next.js implementation. |

Each future lesson will have objectives, source introduction, concept explanation, small code cells, interpretation, checks, exercises and a summary. Work stops after Notebook 02 for your review. Each phase transition requires your approval. The remaining sequence is proposed, not authorization to start modeling.

## Weather-window requirements

The [weather-feature specification](assessment/weather_feature_specification.md) records formulas, units, exact window boundaries, location grouping, incomplete-window behavior, leakage risks and the four uses: descriptive analytics, event timelines, ML and forecast visualization. Its [draft JSON computation contract](assessment/weather_feature_contract.json) can guide later FastAPI implementation without implementing the application.

Rainfall history, wind means, gust maxima and antecedent-wetness indicators are candidates for investigation. No weather feature has yet been selected for ML. Selection requires chronological baseline comparisons and ablation, coverage/calibration checks and demonstrated issue-time availability. Retrospective weather and issued forecast vintages remain distinct. The existing first notebook and source files are preserved.

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
