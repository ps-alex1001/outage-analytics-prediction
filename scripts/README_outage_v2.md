# Synthetic outage V2 pipeline

The V2 generator extends the original environmental simulator. The original
generator is retained byte-for-byte at `scripts/legacy/generate_synthetic_outages_v1.py`.
The existing `data/outages/` V1 outputs, frontend, backend and infrastructure are
unchanged. `outage_environment.py` ports V1's environmental loading and scoring
to the current repository layout, using existing Shapely/rasterio dependencies
instead of adding GeoPandas. `prepare_outage_environment.py` records that port.

Run in the project's uv environment:

```powershell
uv run python scripts/generate_synthetic_outages.py
uv run python -m unittest discover -s scripts/tests -v
```

When the default uv cache is inaccessible, use
`uv --cache-dir .venv/uv-cache run --offline python ...` with the installed environment.

Default reference inputs (copies of the supplied originals):

- `data/reference/penelco/penelco_outages_analysis.csv`
- `data/reference/penelco/codes.xls`
- `data/reference/penelco/penelco_reported_listing.txt`

`xlrd` is the only new dependency, required to read the supplied legacy XLS
codebook. Every run extracts all four code lists and audits source code/label
pairs. It creates a new output directory under `data/synthetic/v2_<UTC timestamp>/`.
Existing output directories cannot be reused or overwritten.

The default count equals V1's realized event volume. Parameters include
`--event-count`, `--seed`, `--epsilon`, `--response`, `--noise-sigma`,
`--animal-share`, `--allocation`, `--geographic-mode`, `--start` and `--end`.
Use `--help` for all options. Default allocation is constrained multinomial.
`--allocation quota` enables a largest-remainder comparison mode.
`--skip-experiments` is for debugging and explicitly marks the multi-seed and
sensitivity validation incomplete; the completed deliverable uses all experiments.

The primary event CSV is accompanied by an input audit, station/circuit crosswalk,
mapping coverage, applied geography and cause targets, duration fits, comparisons,
randomness/sensitivity results, validation, methodology and SHA-256 run manifest.
Refer to each run's `README.md` and `validation_report.md` for results and caveats.

No actual record receives an inferred barangay. The conservative geography mode
distinguishes reference-supported municipality proxies from uncertain mass
allocated using V1's simulation shares. It must not be presented as verified
province-wide PENELCO geography. All event occurrence, cause, duration, severity
and barangay assignments are synthetic. No model or forecasting interface is built.

The repository already ignores `data/`. Retain the versioned output folder and
`data/reference/penelco/` separately when transferring results or source inputs.
