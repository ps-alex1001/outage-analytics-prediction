"""Turn the bounded read-only inspection into a concise dataset catalog."""
from pathlib import Path
import csv
import json

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "notebooks/assessment"
evidence = json.loads((OUT / "source_assessment.json").read_text(encoding="utf-8"))
rows = []
def add(path, role, records, fields, coverage, concerns, usage):
    path = Path(path)
    rows.append({"file_path": str(path), "role": role, "size_mib": round(path.stat().st_size / 2**20, 3),
        "records_or_shape": records, "important_columns_or_variables": fields,
        "date_geographic_coverage": coverage, "quality_concerns": concerns, "necessary_for": usage})

data = ROOT / "data"
legacy = Path(evidence["legacy_root"])
add(data / "outages/synthetic_outage_events.csv", "Canonical synthetic labels / event analytics", "10,608 rows; 51 fields",
    "outage_id; text barangay_psgc, municipality_psgc; start_time, end_time (+08:00); duration_minutes; cause_category; severity_class; weather/terrain/annual NDVI; generator scores",
    "237 barangays / 12 municipalities; starts 2019-01-01 08:00 to 2026-06-30 21:00 Manila; declared exposure ends 23:00",
    "No nulls/duplicate IDs; mixed fractional timestamp representations require format='mixed'. Synthetic heuristics shape labels. Event-only rows cannot estimate occurrence without non-event exposure. Scores/probabilities/outcomes and retrospective NDVI excluded from predictors.",
    "EDA: required; modeling: target source; dashboard: event counts, causes, durations")
add(data / "outages/run_manifest.json", "Run provenance / coverage authority", "1 run", "is_synthetic; seed; version; hashes; period; weights; NDVI policy",
    "2019-01-01 08:00 to 2026-06-30 23:00 Manila; 65,703 elapsed hours; 65,704 candidate starts",
    "Old absolute paths; historical text hashes can differ from current bytes. Seed 42. Run/CSV identity verified; identity does not prove scientific validity.", "EDA/modeling/dashboard: required for coverage and disclosure")
add(data / "boundaries/bataan_barangays.geojson", "Administrative geographic universe", "237 features", "barangay_psgc, barangay, municipality_psgc, municipality; Polygon/MultiPolygon geometry",
    "Bataan / 12 municipalities; CRS84 longitude/latitude; static snapshot",
    "Event key/name matching checked in notebook. Official provenance is documented by old catalog, not independently revalidated. Polygon topology/historical changes deferred.", "EDA: key integrity; modeling: keys/areas; dashboard: GIS boundaries")
add(data / "weather/open_meteo_bataan_2019_01_to_2026_06.csv", "Historical municipal weather", f"{evidence['weather']['rows']:,} rows; 12 × 65,712 hours",
    "municipality, time, latitude, longitude, timezone, utc_offset_seconds; temperature_2m_c; relative_humidity_2m_pct; precipitation_mm; surface_pressure_hpa; wind speed/gust m/s; soil moisture m3/m3",
    "2019-01-01 00:00 to 2026-06-30 23:00 local; 12 municipal points",
    "Chunked scan: no nulls or duplicate municipality/time keys; each municipality has 65,712 rows. Naive clocks, Asia/Singapore with +28,800 s offset. Balanga vs City of Balanga name alias. Municipality-level weather shared by barangays. Historical reanalysis, not archived forecast; issue/release times and accumulation bounds absent. Weather files omitted from copied catalog although present here.",
    "EDA: required later; modeling: historical environment after timing audit; dashboard: optional weather context")
add(data / "weather/era5_bataan_2019_01_to_2026_06.nc", "Gridded historical reanalysis", "65,712 times × 4 latitudes × 3 longitudes; 10 variables",
    "valid_time (UTC); cape/cin J kg-1; tcwv kg m-2; totalx K; cbh m; t2m K; u10/v10 m s-1; cp/tp m",
    "2019-01-01 00:00 to 2026-06-30 23:00 UTC (Manila ends July 1 07:00); lat 15.00–14.25, lon 120.25–120.75; 0.25-degree grid",
    "Metadata verified, no full value scan here. Prior report documents CIN/cloud-base missingness; do not infer completeness from events. Unit conversion, nearest-grid matching and precipitation accumulation bounds need audit. Not a live forecast source.",
    "EDA: weather comparison; modeling: conditional on deployable feature compatibility; dashboard: optional")
add(data / "terrain/bataan_barangay_terrain.csv", "Static terrain summaries", "237 rows; 16 fields", "; ".join(evidence["terrain"]["columns"]),
    "All 237 barangays; static Copernicus GLO-30 DSM context; mean elevation 0.4–572.9 m; mean slope 0.44–19.82 degrees",
    "No nulls/duplicate keys. DSM is surface elevation, not necessarily bare-earth terrain. Raster acquisition/availability dates unverified; slope static, distinct from annual NDVI.", "EDA/modeling: selected terrain fields; dashboard: spatial context")
add(data / "vegetation/bataan_barangay_ndvi_yearly.csv", "Annual retrospective vegetation context", "1,896 rows = 237 × 8", "; ".join(evidence["ndvi"]["columns"]),
    "2019–2025 full-year; 2026 YTD through September 23 per provenance; all 237 barangays",
    "No nulls/duplicate barangay-year keys; valid coverage 100% as reported, not independently recomputed. NDVI means -0.1321–0.8512. Same-year future imagery leaks; prior-year publication times absent. Negative NDVI may reflect water/built surfaces; it is not automatically invalid.",
    "EDA/dashboard: vegetation patterns; modeling: defer pending availability policy")
for raster in evidence["rasters"]:
    is_dem = "dem" in Path(raster["path"]).name
    add(raster["path"], "Elevation DEM raster" if is_dem else "Annual NDVI raster",
        f"{raster['width']:,} × {raster['height']:,} pixels; {raster['bands']} band",
        "Elevation, meters per documented processing (band units not stored)" if is_dem else "NDVI, dimensionless; band description NDVI",
        f"Bataan; {raster['crs']}; pixel size {raster['resolution'][0]} m; " + ("static DSM" if is_dem else Path(raster["path"]).stem.replace("bataan_ndvi_", "")),
        "Metadata only; pixels/nodata masks not audited. " + ("Single band is elevation; no separate slope raster found here. Nodata -9999." if is_dem else "Retrospective composite; nodata metadata absent; do not assume all pixels valid. 2026 imagery extends beyond event coverage."),
        "EDA: optional pixel/GIS audit; modeling: prefer small existing summaries; dashboard: optional raster overlays")
for parquet in evidence["parquet"]:
    path = Path(parquet["path"])
    if path == legacy / "data/processed/ml/ml_static_barangay_context.parquet":
        continue  # identical role is already represented by the local copy
    hourly = "hour_" in path.name
    analytical = "analytical" in path.name
    static = "static" in path.name
    concerns = ("Footer/schema and compact first-row-group key/target sample inspected; full data/positive counts not independently rescanned. Same-hour onset target with t-1h historical features; not 24–72-hour target. Includes infrastructure fields outside simplified scope. "
                + ("Analytical file also contains retrospective NDVI, same-hour context and post-event outcomes: not a predictor table." if analytical else "Lagged historical weather does not prove forecast-time availability; event count is a target diagnostic, never a feature.")) if hourly else (
                "Contains observed and synthetic infrastructure; exclude those fields under current scope. Prefer terrain CSV for simpler audit; not a training panel." if static else "Enriched event-only outcomes and synthetic infrastructure assignments; excluded from simplified occurrence scope.")
    add(path, "Hourly analytical panel" if analytical else "Hourly occurrence panel" if hourly else "Prepared static barangay context" if static else "Enriched event relationship table",
        f"{parquet['rows']:,} rows; {len(parquet['columns'])} fields; {parquet['row_groups']} row groups",
        "PSGC, timezone-aware timestamp, outage_occurred, outage_event_count, calendar, terrain, infrastructure, weather_*_lag1h, era5_*_lag1h" + (", context_* and outcome_*" if analytical else "") if hourly else "; ".join(parquet["columns"][:16]) + "; infrastructure/outcome fields",
        "237 barangays / 12 municipalities; 2019-01-01 08:00 to 2026-06-30 23:00 Manila (prior report + schema)" if hourly else "237 barangays; static snapshot" if static else "10,608 canonical events, 2019–June 2026",
        concerns, "EDA/modeling: inspect in place later, select safe columns; dashboard: use small aggregates" if hourly else "Optional EDA/dashboard context; infrastructure excluded")
add(data / "data_manifest.csv", "Historical copy catalog / lineage", "34 listed source files", "file_path; sha256; size_bytes; source_path; provenance; usage flags; historical hashes",
    "Older curated dashboard copy scope",
    "Paths still include data/curated; local layout is data/. Weather and hourly panels not listed here. READMEs claim absent weather although files now exist. Usage flags reflect older application, not current approval.", "EDA/modeling: provenance; dashboard: source traceability")
for name in ["ml_dataset_report.md", "ml_dataset_summary.json", "ml_field_roles.csv", "ml_missingness.csv", "ml_join_quality.csv", "ml_source_inventory.csv", "ml_independent_validation.json"]:
    path = legacy / "data/processed/ml" / name
    add(path, "Existing ML contract / prior QA", "Metadata / documentation", "field roles; targets; timing; join checks; missingness; provenance; prior validation",
        "Existing hourly panel generation run",
        "Prior QA reports are evidence of past checks, not fresh full-panel validation. Reported onset prevalence 0.06812294%; validate before modeling. Original report allows infrastructure now excluded.", "Modeling: essential later; EDA: timing/provenance; dashboard: documentation only")

with (OUT / "dataset_inventory.csv").open("w", newline="", encoding="utf-8") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

def link(path):
    return f"[{Path(path).name}](<{Path(path).as_posix()}>)"
lines = ["# Verified dataset inventory", "", "Assessment: 12 October 2026. All sources were opened read-only. No generators, migrations, copies of large panels or application edits were run.", "",
         "The current workspace is an early rebuild: backend and notebooks were empty, frontend contained an empty app folder. The existing application and full processed ML sources remain at `D:/PyCharmProjects/Outage Prediction` and were inspected without changes. The source event file matches the selected run's SHA-256.", "",
         "The table below covers core inputs and existing prepared panels. Exact paths, sizes, schemas, caveats, all eight NDVI raster entries and the prior QA contracts are in [dataset_inventory.csv](dataset_inventory.csv). Inspection evidence is in [source_assessment.json](source_assessment.json). MiB means bytes / 1,048,576.", "",
         "| File path | Role / size and records | Important fields | Date / geographic coverage | Quality / usage |", "|---|---|---|---|---|"]
for row in rows:
    if Path(row["file_path"]).suffix == ".tif" or row["role"] == "Existing ML contract / prior QA":
        continue
    important_fields = row["important_columns_or_variables"].split(";")[:5]
    compact_fields = "; ".join(important_fields)
    if len(compact_fields) > 190:
        compact_fields = compact_fields[:187] + "… (full fields in CSV)"
    compact_concerns = {
        "Canonical synthetic labels / event analytics": "No nulls/duplicate IDs; timestamps and durations validated. Synthetic labels; generator scores/outcomes and retrospective NDVI leak if used as predictors.",
        "Run provenance / coverage authority": "Historical paths and some text hashes differ from the current checkout. Event hash matches the selected run.",
        "Administrative geographic universe": "Event keys/names match. Polygon validity and official historical boundary accuracy remain unverified.",
        "Historical municipal weather": "No nulls/duplicate municipality-time keys; naive clocks in UTC+8. Shared municipal observations; Balanga name alias; forecast issue/release times absent.",
        "Gridded historical reanalysis": "Metadata inspected; prior report flags CIN/cloud-base missingness. Units/accumulation bounds need audit; forecast availability unproven.",
        "Static terrain summaries": "No nulls/duplicate keys. Static surface-elevation/slope context; availability dates unverified.",
        "Annual retrospective vegetation context": "No nulls/duplicate keys. Same-year composites use future imagery; prior-year release dates also unverified.",
        "Hourly analytical panel": "Inspected in place via footer/schema/sample. Same-hour onset target; includes retrospective context, outcomes and out-of-scope infrastructure.",
        "Hourly occurrence panel": "Same-hour onset target, not 24–72 hours. Lagged historical weather; infrastructure requires exclusion. Full-panel QA deferred.",
        "Prepared static barangay context": "Not a training panel; includes excluded observed/synthetic infrastructure fields. Prefer terrain CSV for this scope.",
        "Enriched event relationship table": "Event-only post-event outcomes and simulated infrastructure assignments; excluded from simplified modeling.",
        "Historical copy catalog / lineage": "Old data/curated paths and stale weather READMEs; usage flags reflect the older app. Weather/panels not cataloged here.",
    }.get(row["role"], row["quality_concerns"])
    lines.append(f"| {link(row['file_path'])} | {row['role']}; {row['size_mib']:,.3f} MiB; {row['records_or_shape']} | {compact_fields} | {row['date_geographic_coverage']} | {compact_concerns} **Use:** {row['necessary_for']} |")
lines.extend(["", "## Raster sources", "", "| Exact path | Size / shape | Coverage and concern |", "|---|---|---|"])
for row in rows:
    if Path(row["file_path"]).suffix == ".tif":
        lines.append(f"| {link(row['file_path'])} | {row['size_mib']:.2f} MiB; {row['records_or_shape']} | {row['date_geographic_coverage']}; {row['quality_concerns']} |")
lines.extend(["", "## Excluded sources", "", "All files under current `data/infrastructure/`, `data/topology/` and `data/impacts/` remain outside this simplified scope. They contain modeled assets, connectivity/service envelopes or post-event impacts; joining impact rows could multiply the original event count and leak outcomes. Their existence/sizes were checked, but content quality was not audited. They are unnecessary for this first EDA and simplified environmental occurrence model. No excluded file was altered.", "",
    "## Decisions and open questions", "",
    "- Use canonical original-barangay events for counts; downstream affected barangays are not extra occurrence labels.",
    "- Use declared coverage for exposure. Partial 2026 counts are not directly comparable to full years.",
    "- Static slope/elevation and annual NDVI have different timing semantics. Full-year NDVI is retrospective; 2026 includes imagery after labels end. Publication dates remain unverified even for prior-year composites.",
    "- Inspect existing Parquet selectively in place; do not duplicate 15.57M-row panels. Both panels are same-hour onset products and contain infrastructure fields requiring explicit exclusion later.",
    "- A future 24–72-hour task requires issue-time semantics, horizon targets, chronological splits and gaps preventing overlapping outcome windows from crossing splits. No features/labels were redesigned here.",
    "- Historical weather is available. No archived Open-Meteo forecast issue/lead-time dataset was found in the inspected current data tree. Release timing, units, accumulation intervals and deployable variable compatibility remain unverified.",
    "- Labels, causes, duration and severity are synthetic. Strong future test results would demonstrate behavior within this simulator, not operational PENELCO skill.",
    "- This assessment scanned event/weather/terrain/NDVI tables, read raster/ERA5 metadata, inspected generator/panel documentation and Parquet metadata plus bounded key/target samples. It did not repeat full-panel QA or raster-pixel statistics.", ""])
(OUT / "dataset_inventory.md").write_text("\n".join(lines), encoding="utf-8")
print(f"Wrote {len(rows)} inventory records and concise assessment.")
