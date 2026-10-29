# Synthetic outage generator V3

Run from the repository root:

```powershell
uv run python scripts/generate_synthetic_outages_v3.py
uv run python -m unittest discover -s scripts/tests -p test_outage_v3.py -v
uv run python scripts/verify_outage_v3.py data/synthetic/v3_<run>
```

For a restricted default uv cache, the installed environment also supports
`uv --cache-dir .venv/uv-cache run --offline python ...`.

Inputs are discovered and inspected existing project files: Open-Meteo hourly
weather, ERA5 CAPE, Bataan's 237 polygons, terrain and annual NDVI. Environmental
loading/normalization and the exact H/S coefficients reuse the V1 port in
`outage_environment.py`; the original script remains under `legacy/`.
Per the user's clarification, V2 is used **only for duration**: its existing
cause-specific fit table, log-scale environmental effect and bounded lognormal
sampling functions. No V2 geographic allocation or cause proportions are used.

V3 uses Bernoulli logistic sampling, three separate regularized municipal
offsets, conditional six-cause sampling, observed-temperature heat preferences,
approximately 6,000 events, Weather ~40% and Animal ~3%. These are simulation
targets. The other four cause shares are not constrained. No exact event quota,
municipality quota, highest-score selection or count-forcing seed search is used.

Every invocation creates a new `data/synthetic/v3_<UTC timestamp>/` directory.
Use `--output` for a specific **new** directory, `--seed`, `--target-total-events`,
`--start` or `--end` as needed. `--skip-sensitivity` is a debug option and marks
the diagnostic report incomplete. Baseline output is always
`synthetic_outage_events_v3.csv`. Preserve the entire run folder; `data/` is
ignored by Git in this repository.

Each run includes source inspection, formula mapping, source/code checksums,
calibration parameters, duration parameters, municipality/barangay/time/cause
tables, integrity checks, eight sensitivity alternatives, a validation report
and a downstream migration plan. Geographic and environmental contributions
are reported separately. Collision losses are included through a low-rate
expected dead-time approximation and actual suppressed-candidate counts.

ERA5 removes the first eight local hours of January 1, 2019 from the true
intersection. NDVI is retrospective annual context: the 2026 composite includes
imagery through September 23, after the June event horizon. Do not use it as an
available-at-onset forecasting measurement. No environmental values are filled
or invented. Weather is municipality resolution, CAPE is ERA5 grid resolution.

All outage occurrence, cause, duration, severity and calibration effects are
synthetic, not actual PENELCO history or empirically validated failure risk.
Infrastructure does not enter generation. Application paths, impact records,
labels and trained models remain on their existing versions until deliberately
rebuilt and activated together; see each run's `downstream_migration.md`.
