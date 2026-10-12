"""Read-only source assessment. Writes only small evidence into notebooks/assessment/."""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import rasterio
import xarray as xr

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
LEGACY = Path(r"D:\PyCharmProjects\Outage Prediction")
OUT = ROOT / "notebooks/assessment"
OUT.mkdir(exist_ok=True)

def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

evidence = {"assessment_date": "2026-10-12", "current_root": str(ROOT), "legacy_root": str(LEGACY)}
evidence["protected_local_files"] = {
    str(p.relative_to(ROOT)): {"bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
    for folder in [DATA, ROOT / "frontend", ROOT / "backend"]
    for p in folder.rglob("*") if p.is_file()
}
events = pd.read_csv(DATA / "outages/synthetic_outage_events.csv", dtype={"barangay_psgc": "string", "municipality_psgc": "string"})
start = pd.to_datetime(events.start_time, format="mixed", utc=True, errors="coerce").dt.tz_convert("Asia/Manila")
end = pd.to_datetime(events.end_time, format="mixed", utc=True, errors="coerce").dt.tz_convert("Asia/Manila")
with (DATA / "boundaries/bataan_barangays.geojson").open(encoding="utf-8") as stream:
    geojson = json.load(stream)
geography = pd.DataFrame([feature["properties"] for feature in geojson["features"]])
evidence["boundaries"] = {"features": len(geography), "columns": geography.columns.tolist(), "crs": geojson.get("crs"), "sample": geography.head(2).to_dict("records")}
evidence["outages"] = {
    "rows": len(events), "columns": events.columns.tolist(), "dtypes": events.dtypes.astype(str).to_dict(),
    "missing": events.isna().sum().to_dict(), "duplicate_rows": int(events.duplicated().sum()),
    "duplicate_ids": int(events.outage_id.duplicated().sum()), "synthetic_values": events.is_synthetic.value_counts().to_dict(),
    "first_start": str(start.min()), "last_start": str(start.max()), "last_end": str(end.max()),
    "invalid_timestamps": int((start.isna() | end.isna()).sum()),
    "nonpositive_duration": int((end <= start).sum()),
    "duration_max_difference_minutes": float(((end - start).dt.total_seconds()/60-events.duration_minutes).abs().max()),
    "duration_summary": events.duration_minutes.describe(percentiles=[.5,.9,.95,.99]).to_dict(),
    "municipalities": int(events.municipality_psgc.nunique()), "barangays": int(events.barangay_psgc.nunique()),
    "year_counts": start.dt.year.value_counts().sort_index().to_dict(),
    "causes": events.cause_category.value_counts().to_dict(), "severity": events.severity_class.value_counts().to_dict(),
}
manifest = pd.read_csv(DATA / "data_manifest.csv")
evidence["manifest_columns"] = manifest.columns.tolist()
evidence["notebook_source_hashes"] = {str(p.relative_to(ROOT)): sha256(p) for p in [DATA / "outages/synthetic_outage_events.csv", DATA / "outages/run_manifest.json", DATA / "boundaries/bataan_barangays.geojson", DATA / "data_manifest.csv"]}

weather_path = DATA / "weather/open_meteo_bataan_2019_01_to_2026_06.csv"
weather_columns = pd.read_csv(weather_path, nrows=0).columns.tolist()
weather_missing = pd.Series(0, index=weather_columns, dtype="int64")
weather_keys = []
for chunk in pd.read_csv(weather_path, chunksize=100_000):
    weather_missing += chunk.isna().sum()
    weather_keys.append(chunk[["municipality", "time", "timezone", "utc_offset_seconds", "latitude", "longitude"]])
weather = pd.concat(weather_keys, ignore_index=True)
evidence["weather"] = {"rows": len(weather), "columns": weather_columns, "missing": weather_missing.to_dict(),
    "coverage": weather.groupby("municipality").time.agg(["min", "max", "count"]).reset_index().to_dict("records"),
    "duplicate_municipality_time": int(weather.duplicated(["municipality", "time"]).sum()),
    "timezone_values": weather.timezone.unique().tolist(), "utc_offsets": weather.utc_offset_seconds.unique().tolist(),
    "sha256": sha256(weather_path)}
for name, relative in [("terrain", "terrain/bataan_barangay_terrain.csv"), ("ndvi", "vegetation/bataan_barangay_ndvi_yearly.csv")]:
    frame = pd.read_csv(DATA / relative, dtype={"barangay_psgc": "string", "municipality_psgc": "string"})
    entry = {"rows": len(frame), "columns": frame.columns.tolist(), "missing": frame.isna().sum().to_dict(), "barangays": frame.barangay_psgc.nunique(), "sha256": sha256(DATA / relative)}
    if name == "ndvi":
        entry.update(years=sorted(frame.year.unique().tolist()), period_types=frame.groupby("year").period_type.unique().apply(list).to_dict(),
                     duplicate_keys=int(frame.duplicated(["barangay_psgc", "year"]).sum()),
                     ranges=frame[["mean_ndvi", "valid_coverage_pct"]].agg(["min", "max"]).to_dict())
    else:
        entry.update(duplicate_keys=int(frame.barangay_psgc.duplicated().sum()), ranges=frame[["mean_elevation_m", "mean_slope_deg", "p90_slope_deg"]].agg(["min", "max"]).to_dict())
    evidence[name] = entry

evidence["rasters"] = []
for path in [*sorted((DATA / "vegetation").glob("*.tif")), DATA / "terrain/bataan_dem_utm51n.tif"]:
    with rasterio.open(path) as raster:
        evidence["rasters"].append({"path": str(path), "bytes": path.stat().st_size, "width": raster.width, "height": raster.height,
          "bands": raster.count, "crs": str(raster.crs), "resolution": raster.res, "nodata": raster.nodata,
          "descriptions": raster.descriptions, "units": raster.units, "tags": raster.tags()})
with xr.open_dataset(DATA / "weather/era5_bataan_2019_01_to_2026_06.nc", engine="h5netcdf") as era5:
    evidence["era5"] = {"dimensions": dict(era5.sizes), "variables": {name: {"dimensions": list(variable.dims), "attrs": variable.attrs} for name, variable in era5.data_vars.items()},
                        "coordinates": {name: {"first": str(coordinate.values.flat[0]), "last": str(coordinate.values.flat[-1])} for name, coordinate in era5.coords.items()}}

evidence["parquet"] = []
for path in [DATA / "ml/ml_static_barangay_context.parquet", *sorted((LEGACY / "data/processed/ml").glob("*.parquet"))]:
    parquet = pq.ParquetFile(path)
    entry = {"path": str(path), "bytes": path.stat().st_size, "rows": parquet.metadata.num_rows,
        "row_groups": parquet.num_row_groups, "schema": str(parquet.schema_arrow), "columns": parquet.schema_arrow.names,
        "metadata": {key.decode(): value.decode(errors="replace") for key, value in (parquet.schema_arrow.metadata or {}).items() if key != b"pandas"}}
    if "hour_" in path.name:
        # Metadata plus one compact key/target slice, never the whole 15.57M-row panel.
        columns = [c for c in ["barangay_psgc", "timestamp", "outage_occurred", "outage_event_count"] if c in parquet.schema_arrow.names]
        entry["sample"] = parquet.read_row_group(0, columns=columns).slice(0,3).to_pandas().astype(str).to_dict("records")
    evidence["parquet"].append(entry)
summary_path = LEGACY / "data/processed/ml/ml_dataset_summary.json"
summary = json.loads(summary_path.read_text(encoding="utf-8"))
evidence["legacy_ml_reported_summary"] = {key: summary[key] for key in ["start", "end", "rows", "positive", "negative", "positive_rate", "hours", "barangays", "municipalities", "outputs"]}
evidence["scope_note"] = "Panel row counts verified from Parquet footers; positive counts and full-panel QA are prior report claims, not independently rescanned in this assessment."
OUT.joinpath("source_assessment.json").write_text(json.dumps(evidence, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"events": len(events), "invalid_timestamps": evidence["outages"]["invalid_timestamps"],
                  "weather_rows": len(weather), "weather_nulls": int(weather_missing.sum()),
                  "parquet_rows": {Path(p["path"]).name: p["rows"] for p in evidence["parquet"]}}, indent=2))
print("Read-only inventory evidence saved to", OUT)
