"""Environmental loader and formula ported from the preserved V1 generator.

Only paths, GIS adapter and explicit engine are changed. No infrastructure inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import transform as transform_geometry
from rasterio.warp import transform as transform_coordinates
import numpy as np
import pandas as pd
import xarray as xr


# Configuration: every numerical effect below is an arbitrary simulation choice.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "synthetic"
INPUTS = {
    "weather": PROJECT_ROOT / "data/weather/open_meteo_bataan_2019_01_to_2026_06.csv",
    "era5": PROJECT_ROOT / "data/weather/era5_bataan_2019_01_to_2026_06.nc",
    "geography": PROJECT_ROOT / "data/boundaries/bataan_barangays.geojson",
    "terrain": PROJECT_ROOT / "data/terrain/bataan_barangay_terrain.csv",
    "vegetation": PROJECT_ROOT / "data/vegetation/bataan_barangay_ndvi_yearly.csv",
}
GENERATOR_VERSION = "1.0.0"
RANDOM_SEED = 42
TARGET_SYNTHETIC_EVENTS_PER_BARANGAY_YEAR = 6.0
PROPENSITY_RESPONSE = 4.0
HOURS_PER_YEAR = 365.2425 * 24
LOCAL_TZ = "Asia/Manila"
START = "2019-01-01 00:00"
END = "2026-06-30 23:00"
NDVI_2026_END_EXCLUSIVE = "2026-09-24"  # Source extraction script; not hourly NDVI.
QUANTILES = (0.01, 0.99)
MIN_DURATION_MINUTES = 15.0
MAX_DURATION_MINUTES = 48 * 60.0
CAUSES = ("Weather / Storm", "Vegetation / Tree Contact",
          "Equipment / Technical", "Other / Unknown")
CAUSE_MEDIANS = np.array([120.0, 100.0, 80.0, 45.0])
WEATHER_WEIGHTS = np.array([0.30, 0.35, 0.20, 0.15])
# Columns are H, V, T, WV, RT; each scenario sums to one.
SCENARIOS = {
    "baseline": np.array([0.50, 0.20, 0.10, 0.12, 0.08]),
    "weather_heavier": np.array([0.60, 0.15, 0.05, 0.12, 0.08]),
    "vegetation_heavier": np.array([0.40, 0.30, 0.10, 0.12, 0.08]),
    "terrain_reduced": np.array([0.60, 0.24, 0.00, 0.16, 0.00]),
}
MUNICIPALITY_ALIASES = {"Balanga": "City of Balanga"}
WEATHER_FIELDS = [
    "precipitation_mm", "wind_speed_10m_m_s", "wind_gusts_10m_m_s",
    "soil_moisture_0_to_7cm_m3_m3", "soil_moisture_7_to_28cm_m3_m3",
    "temperature_2m_c", "relative_humidity_2m_pct", "surface_pressure_hpa",
]
TERRAIN_FIELDS = ["mean_elevation_m", "mean_slope_deg", "p90_slope_deg",
                  "elevation_relief_p95_p05_m"]
TERRAIN_CANDIDATES = ["mean_slope_deg", "median_slope_deg", "p90_slope_deg",
                      "pct_area_slope_gt_15_deg", "elevation_relief_p95_p05_m"]
SCORE_FIELDS = ["weather_hazard_score", "vegetation_exposure_score",
                "terrain_exposure_score", "wind_vegetation_interaction",
                "rain_terrain_interaction"]
BIN_EDGES = np.linspace(0, 1, 11)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def json_default(value):
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, (Path, pd.Timestamp)):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Cannot encode {type(value)}")


def write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, default=json_default, allow_nan=False)
        handle.write("\n")


def fingerprints() -> dict:
    result = {}
    for name, path in INPUTS.items():
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        result[name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": digest}
    return result


def validate_table(frame: pd.DataFrame, keys: list[str], name: str) -> dict:
    require(not frame.empty, f"{name}: empty table")
    require(set(keys).issubset(frame), f"{name}: missing keys {keys}")
    missing = frame.isna().sum()
    require(not missing.any(), f"{name}: missing values: {missing[missing.gt(0)].to_dict()}")
    for column in frame.select_dtypes(include=["str", "object", "string"]):
        require(frame[column].astype(str).str.strip().ne("").all(),
                f"{name}: blank values in {column}")
    duplicated = frame.duplicated(keys, keep=False)
    require(not duplicated.any(),
            f"{name}: duplicate keys: {frame.loc[duplicated, keys].to_dict('records')[:30]}")
    numeric = frame.select_dtypes(include="number")
    require(np.isfinite(numeric.to_numpy()).all(), f"{name}: non-finite numeric values")
    return {"rows": len(frame), "schema": frame.dtypes.astype(str).to_dict(),
            "missing_values": missing.to_dict(), "duplicate_keys": int(duplicated.sum())}


def check_psgc(frame: pd.DataFrame, columns: list[str], name: str) -> None:
    for column in columns:
        require(frame[column].str.fullmatch(r"\d{10}").all(),
                f"{name}: {column} must contain 10-digit PSGC strings")


def validate_hourly(index: pd.DatetimeIndex, name: str) -> None:
    require(len(index) > 0 and not index.hasnans and index.is_unique,
            f"{name}: empty, missing, or duplicated timestamps")
    require(index.equals(pd.date_range(index.min(), index.max(), freq="h")),
            f"{name}: timestamps must be sorted, contiguous, and hourly")
    require((index == index.floor("h")).all(), f"{name}: timestamps are not on the hour")


def local_timestamp(value: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(LOCAL_TZ) if stamp.tzinfo is None else stamp.tz_convert(LOCAL_TZ)


@dataclass(frozen=True)
class Normalizer:
    lower: float
    upper: float

    @classmethod
    def fit(cls, values, name: str) -> "Normalizer":
        values = np.asarray(values, dtype=float)
        require(np.isfinite(values).all(), f"{name}: invalid normalization inputs")
        low, high = np.quantile(values, QUANTILES)
        require(high > low, f"{name}: 1st/99th percentiles coincide; cannot safely normalize")
        return cls(float(low), float(high))

    def transform(self, values) -> np.ndarray:
        return np.clip((np.asarray(values, dtype=float) - self.lower) /
                       (self.upper - self.lower), 0, 1)


@dataclass
class IntegratedData:
    geography: pd.DataFrame
    weather: dict[str, pd.DataFrame]
    vegetation: pd.DataFrame
    cape: dict[tuple[float, float], np.ndarray]
    times: pd.DatetimeIndex
    normalizers: dict[str, Normalizer]
    inspection: dict


def load_and_validate(start: str, end: str) -> IntegratedData:
    """Read actual source schemas, reject broken joins, and align in UTC."""
    report = {"sources": {}, "warnings": [], "normalization": {}}
    document = json.loads(INPUTS["geography"].read_text(encoding="utf-8"))
    features = document["features"]
    geometry = [shape(f["geometry"]) for f in features]
    require(all(g.is_valid and not g.is_empty and g.geom_type in {"Polygon", "MultiPolygon"}
                for g in geometry), "Invalid, empty or nonpolygon barangay geometry")
    require(len({g.wkb for g in geometry}) == len(geometry), "Duplicate barangay geometries")
    geo = pd.DataFrame([f["properties"] for f in features])
    require(geo.province.eq("Bataan").all(), "Non-Bataan geography")
    report["sources"]["geography"] = validate_table(geo, ["barangay_psgc"], "geography")
    check_psgc(geo, ["barangay_psgc", "municipality_psgc"], "geography")
    def project(src, dst):
        def coordinates(x, y, z=None):
            return transform_coordinates(src, dst, x, y)
        return coordinates
    projected = [transform_geometry(project("EPSG:4326", "EPSG:32651"), g) for g in geometry]
    points = [transform_geometry(project("EPSG:32651", "EPSG:4326"), g.representative_point())
              for g in projected]
    geo["point_latitude"] = [p.y for p in points]
    geo["point_longitude"] = [p.x for p in points]
    geo = geo.sort_values("barangay_psgc").reset_index(drop=True)
    report["source_crs"] = "EPSG:4326; representative points computed in EPSG:32651"
    report["duplicate_geometries"] = 0
    municipalities = geo[["municipality_psgc", "municipality"]].drop_duplicates()
    require(municipalities.municipality.is_unique and municipalities.municipality_psgc.is_unique,
            "Municipality names and PSGC codes are not one-to-one")
    geo_keys = set(geo.barangay_psgc)

    terrain = pd.read_csv(INPUTS["terrain"], dtype={"barangay_psgc": str, "municipality_psgc": str})
    vegetation = pd.read_csv(INPUTS["vegetation"], dtype={"barangay_psgc": str})
    for name, table, keys in [("terrain", terrain, ["barangay_psgc"]),
                               ("vegetation", vegetation, ["barangay_psgc", "year"])]:
        report["sources"][name] = validate_table(table, keys, name)
        check_psgc(table, ["barangay_psgc"], name)
        unmatched, extra = sorted(geo_keys - set(table.barangay_psgc)), sorted(set(table.barangay_psgc) - geo_keys)
        require(not unmatched and not extra, f"{name}: unmatched barangays={unmatched}; extra={extra}")
        labels = ["barangay", "municipality"] + (["municipality_psgc"] if name == "terrain" else [])
        joined = table.merge(geo[["barangay_psgc", *labels]], on="barangay_psgc",
                             suffixes=("_source", "_geo"), validate="many_to_one")
        mismatches = {col: joined.loc[joined[col + "_source"].ne(joined[col + "_geo"]),
                                     "barangay_psgc"].tolist() for col in labels}
        require(not any(mismatches.values()), f"{name}: label/PSGC mismatches {mismatches}")
        report["sources"][name]["name_mismatches"] = mismatches
    require(vegetation.mean_ndvi.between(-1, 1).all(), "NDVI outside [-1,1]")
    require(vegetation.valid_coverage_pct.between(0, 100).all()
            and vegetation.valid_pixel_count.gt(0).all(), "NDVI has invalid pixel coverage")
    require((vegetation.valid_pixel_count <= vegetation.total_pixel_count).all(), "NDVI invalid pixel counts")
    if vegetation.valid_coverage_pct.lt(90).any():
        raise ValueError("NDVI coverage below 90%; review the affected records before simulation")
    require((vegetation.year % 1 == 0).all(), "NDVI year must be an integer")
    require(vegetation.period_type.eq(np.where(vegetation.year.eq(2026), "year_to_date", "full_year")).all(),
            "Unexpected NDVI period_type; review temporal metadata")
    require(terrain.mean_slope_deg.between(0, 90).all(), "Slope outside [0,90]")
    require(terrain.elevation_relief_p95_p05_m.ge(0).all(), "Negative terrain relief")
    corr = terrain[TERRAIN_CANDIDATES].corr(method="spearman")
    report["terrain_spearman_correlations"] = corr.to_dict()
    report["terrain_choice"] = "T = normalized mean_slope_deg only; redundant slope/relief measures remain context"
    geo = geo.merge(terrain[["barangay_psgc", *TERRAIN_FIELDS]], on="barangay_psgc", validate="one_to_one")

    weather = pd.read_csv(INPUTS["weather"])
    report["sources"]["weather"] = validate_table(weather, ["municipality", "time"], "weather")
    require(set(WEATHER_FIELDS).issubset(weather), "Missing required weather fields")
    require(weather.timezone.nunique() == 1 and weather.utc_offset_seconds.nunique() == 1,
            "Mixed Open-Meteo timezones require explicit review")
    source_zone = weather.timezone.iloc[0]
    require(source_zone == "Asia/Singapore" and weather.utc_offset_seconds.eq(28800).all(),
            "Open-Meteo timezone metadata changed; review conversion")
    parsed = pd.DatetimeIndex(pd.to_datetime(weather.time, errors="raise"))
    require(parsed.tz is None, "Expected naive Open-Meteo local timestamps")
    weather["time"] = parsed.tz_localize(source_zone).tz_convert("UTC")
    weather["municipality"] = weather.municipality.replace(MUNICIPALITY_ALIASES)
    require(set(weather.municipality) == set(geo.municipality),
            f"Weather municipality mismatch: {set(weather.municipality) ^ set(geo.municipality)}")
    weather_by_muni = {}
    for name, frame in weather.groupby("municipality", sort=True):
        frame = frame.set_index("time").sort_index()
        validate_hourly(frame.index, f"Open-Meteo {name}")
        weather_by_muni[name] = frame
    for col in ["precipitation_mm", "wind_gusts_10m_m_s", "wind_speed_10m_m_s"]:
        require(weather[col].ge(0).all(), f"Negative {col}")
    for col in ["soil_moisture_0_to_7cm_m3_m3", "soil_moisture_7_to_28cm_m3_m3"]:
        require(weather[col].between(0, 1).all(), f"{col} outside [0,1]")
    require(weather.relative_humidity_2m_pct.between(0, 100).all(), "Invalid humidity")
    requested_start, requested_end = local_timestamp(start).tz_convert("UTC"), local_timestamp(end).tz_convert("UTC")
    require(requested_end > requested_start, "Analysis end must follow start")
    require(requested_start == requested_start.floor("h") and requested_end == requested_end.floor("h"),
            "Start and end must specify whole hours")
    require(requested_start >= local_timestamp(START).tz_convert("UTC")
            and requested_end <= local_timestamp(END).tz_convert("UTC"),
            "Requested period exceeds inspected source period; review inputs first")

    with xr.open_dataset(INPUTS["era5"], engine="h5netcdf") as ds:
        require(set(ds.cape.dims) == {"valid_time", "latitude", "longitude"}, "Unexpected CAPE dimensions")
        require(ds.cape.attrs.get("units") == "J kg**-1", "Unexpected CAPE units")
        require(ds.attrs.get("Conventions") == "CF-1.7"
                and ds.valid_time.encoding.get("units") == "seconds since 1970-01-01",
                "ERA5 time encoding changed; review UTC interpretation")
        era_times = pd.DatetimeIndex(ds.valid_time.values).tz_localize("UTC")
        validate_hourly(era_times, "ERA5")
        era_report = {"dimensions": dict(ds.sizes), "variables": {},
                      "start_utc": str(era_times.min()), "end_utc": str(era_times.max()),
                      "time_units": ds.valid_time.encoding["units"], "timezone": "UTC (CF-1.7)",
                      "latitude": ds.latitude.values.tolist(), "longitude": ds.longitude.values.tolist()}
        for name, variable in ds.data_vars.items():
            era_report["variables"][name] = {
                "dims": list(variable.dims), "dtype": str(variable.dtype),
                "units": variable.attrs.get("units"), "missing": int(variable.isnull().sum()),
                "size": variable.size,
            }
        report["sources"]["era5"] = era_report
        common_start = max(requested_start, era_times.min(), *(f.index.min() for f in weather_by_muni.values()))
        common_end = min(requested_end, era_times.max(), *(f.index.max() for f in weather_by_muni.values()))
        require(common_end > common_start, "No usable common hourly period")
        times = pd.date_range(common_start, common_end, freq="h")
        if common_start != requested_start or common_end != requested_end:
            report["warnings"].append(f"Requested period adjusted to real intersection: {common_start.tz_convert(LOCAL_TZ)} through {common_end.tz_convert(LOCAL_TZ)}; no filling or extrapolation")
        points = geo.rename(columns={"point_latitude": "y", "point_longitude": "x"})
        latitudes, longitudes = ds.latitude.values, ds.longitude.values
        require(len(latitudes) > 1 and len(longitudes) > 1, "ERA5 spatial coordinates are insufficient")
        require(np.isfinite(latitudes).all() and np.isfinite(longitudes).all(), "Invalid ERA5 coordinates")
        require(points.y.between(min(latitudes), max(latitudes)).all()
                and points.x.between(min(longitudes), max(longitudes)).all(), "Barangay representative point outside ERA5 grid")
        geo["era5_latitude"] = [latitudes[np.argmin(np.abs(latitudes - y))] for y in points.y]
        geo["era5_longitude"] = [longitudes[np.argmin(np.abs(longitudes - x))] for x in points.x]
        cape = {}
        indices = era_times.get_indexer(times)
        require((indices >= 0).all(), "Missing ERA5 times in intersection")
        for lat, lon in sorted(set(zip(geo.era5_latitude, geo.era5_longitude))):
            values = ds.cape.sel(latitude=lat, longitude=lon).isel(valid_time=indices).values.astype(float)
            require(np.isfinite(values).all() and (values >= 0).all(), f"Unusable CAPE at {(lat, lon)}")
            cape[(lat, lon)] = values

    years = sorted(set(times.tz_convert(LOCAL_TZ).year))
    for year in years:
        unmatched = sorted(geo_keys - set(vegetation.loc[vegetation.year.eq(year), "barangay_psgc"]))
        require(not unmatched, f"Missing NDVI for year {year}, barangays={unmatched}; no extrapolation allowed")
    vegetation = vegetation.loc[vegetation.year.isin(years)].copy()
    weather_by_muni = {name: frame.loc[times, WEATHER_FIELDS].copy() for name, frame in weather_by_muni.items()}
    report["warnings"].append("NDVI is retrospective annual composite context, not event-hour vegetation or measured power-line proximity")
    if 2026 in years:
        report["warnings"].append("Inherited V1 retrospective 2026 NDVI includes imagery through 2026-09-23, later than Jan-Jun synthetic events; source window traced to extraction script")
    report["period"] = {"requested_start": start, "requested_end": end,
                        "start_local": str(times[0].tz_convert(LOCAL_TZ)),
                        "end_local": str(times[-1].tz_convert(LOCAL_TZ)), "candidate_hours": len(times),
                        "exposure_hours_per_barangay": (times[-1] - times[0]).total_seconds() / 3600,
                        "boundary_rule": "Both start and end must be within the inclusive interval; reject events extending past end"}
    report["joins"] = {"geo_barangays": len(geo), "terrain_matched": len(geo),
                       "vegetation_matched_each_year": len(geo), "weather_matched": len(geo),
                       "era5_matched": len(geo), "unmatched_barangays": [],
                       "municipality_aliases": MUNICIPALITY_ALIASES,
                       "era5_method": "Nearest coordinate grid cell to UTM51N interior representative point",
                       "era5_cells_used": len(cape)}
    report["timezone_conversion"] = "Open-Meteo Asia/Singapore (+08:00) -> UTC; ERA5 CF decoded time -> UTC; joins UTC; final events Asia/Manila (+08:00)"
    normalizers = {}
    for symbol, column in [("R", "precipitation_mm"), ("W", "wind_gusts_10m_m_s"),
                           ("M", "soil_moisture_7_to_28cm_m3_m3")]:
        normalizers[symbol] = Normalizer.fit(np.concatenate([f[column].to_numpy() for f in weather_by_muni.values()]), symbol)
    normalizers["C"] = Normalizer.fit(np.concatenate(list(cape.values())), "C")
    normalizers["V"] = Normalizer.fit(vegetation.mean_ndvi, "V")
    normalizers["T"] = Normalizer.fit(geo.mean_slope_deg, "T")
    report["normalization"] = {key: {"p01": value.lower, "p99": value.upper}
                               for key, value in normalizers.items()}
    report["normalization_population"] = "Weather: municipality-hours; CAPE: unique assigned grid-cell-hours; NDVI: barangay-years; terrain: barangays. Selected period only, pooled locations, clip p01/p99."
    return IntegratedData(geo, weather_by_muni, vegetation, cape, times, normalizers, report)


def component_arrays(data: IntegratedData, row) -> tuple[np.ndarray, np.ndarray, pd.DataFrame, np.ndarray]:
    """Only one barangay's hourly arrays are materialized at a time."""
    weather = data.weather[row.municipality]
    norm = data.normalizers
    rain = norm["R"].transform(weather.precipitation_mm)
    wind = norm["W"].transform(weather.wind_gusts_10m_m_s)
    cape = data.cape[(row.era5_latitude, row.era5_longitude)]
    convection = norm["C"].transform(cape)
    moisture = norm["M"].transform(weather.soil_moisture_7_to_28cm_m3_m3)
    annual = data.vegetation.loc[data.vegetation.barangay_psgc.eq(row.barangay_psgc)].set_index("year")
    ndvi = annual.mean_ndvi.reindex(data.times.tz_convert(LOCAL_TZ).year).to_numpy()
    vegetation = norm["V"].transform(ndvi)
    terrain = np.full(len(data.times), norm["T"].transform(row.mean_slope_deg))
    raw_components = np.column_stack([rain, wind, convection, moisture])
    hazard = raw_components @ WEATHER_WEIGHTS
    # User-specified interactions and score weights are retained without simplification.
    components = np.column_stack([hazard, vegetation, terrain, wind * vegetation, rain * terrain])
    return components, raw_components, weather, ndvi


def propensity(components: np.ndarray, weights: np.ndarray) -> np.ndarray:
    require(np.isclose(weights.sum(), 1) and (weights >= 0).all(), "Invalid propensity weights")
    score = components @ weights
    require(np.isfinite(score).all() and (score >= 0).all() and (score <= 1 + 1e-12).all(),
            "Propensity outside [0,1]")
    return np.clip(score, 0, 1)

