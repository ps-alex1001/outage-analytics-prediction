"""Generate explicitly SYNTHETIC outage events for the Bataan GIS dashboard.

Real PENELCO outage records are unavailable. This generator supports dashboard
development, testing, and methodological demonstration only. Geography, weather,
CAPE, terrain, and annual vegetation composites are real source data; event
occurrence, cause, duration, and severity are simulated. All relationships and
weights are heuristic simulation assumptions, not fitted reliability estimates.
Outputs MUST NOT be described as actual PENELCO history or forecast probability.

2026 NDVI policy, explicitly approved by the user: use the supplied Jan-Sep
composite as retrospective context for Jan-Jun events. Annual NDVI is not an
event-time observation and does not establish proximity to electrical lines.
No infrastructure metrics are used. Source files are opened read-only.

Run with the existing environment:
    uv run python scripts/generate_synthetic_outages.py
    uv run python scripts/generate_synthetic_outages.py --self-test

Each invocation creates a NEW directory under data/synthetic and refuses to
reuse an existing directory. No existing datasets or extraction scripts change.
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

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr


# Configuration: every numerical effect below is an arbitrary simulation choice.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "synthetic"
INPUTS = {
    "weather": PROJECT_ROOT / "data/open_meteo/open_meteo_bataan_2019_01_to_2026_06.csv",
    "era5": PROJECT_ROOT / "data/era5/era5_bataan_2019_01_to_2026_06.nc",
    "geography": PROJECT_ROOT / "data/geo/bataan_barangays.geojson",
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
    geography: gpd.GeoDataFrame
    weather: dict[str, pd.DataFrame]
    vegetation: pd.DataFrame
    cape: dict[tuple[float, float], np.ndarray]
    times: pd.DatetimeIndex
    normalizers: dict[str, Normalizer]
    inspection: dict


def load_and_validate(start: str, end: str) -> IntegratedData:
    """Read actual source schemas, reject broken joins, and align in UTC."""
    report = {"sources": {}, "warnings": [], "normalization": {}}
    geo = gpd.read_file(INPUTS["geography"])
    require(geo.crs is not None, "Geography has no CRS")
    report["source_crs"] = str(geo.crs)
    require(not geo.geometry.isna().any() and not geo.geometry.is_empty.any()
            and geo.geometry.is_valid.all(), "Missing, empty, or invalid geometry")
    require(geo.geom_type.isin(["Polygon", "MultiPolygon"]).all(), "Expected polygon geometry")
    require(geo.province.eq("Bataan").all(), "Geography includes non-Bataan records")
    for col in ["barangay_psgc", "municipality_psgc"]:
        geo[col] = geo[col].astype(str)
    report["sources"]["geography"] = validate_table(geo.drop(columns="geometry"),
                                                        ["barangay_psgc"], "geography")
    check_psgc(geo, ["barangay_psgc", "municipality_psgc"], "geography")
    geo = geo.to_crs(4326).sort_values("barangay_psgc").reset_index(drop=True)
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

    with xr.open_dataset(INPUTS["era5"]) as ds:
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
        points = geo.to_crs(32651).representative_point().to_crs(4326)
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
        report["warnings"].append("User-approved 2026 NDVI includes imagery through 2026-09-23, later than Jan-Jun synthetic events; source window traced to extraction script")
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


def cause_indices(components: np.ndarray, weather_components: np.ndarray, uniforms: np.ndarray) -> np.ndarray:
    h, _, _, wv, _ = components.T
    r, w, c, _ = weather_components.T
    weights = np.column_stack([0.20 + 1.6 * h + 0.4 * r + 0.4 * w + 0.4 * c,
                               0.15 + 3.0 * wv, np.full(len(h), 0.70), np.full(len(h), 0.20)])
    cumulative = np.cumsum(weights / weights.sum(axis=1, keepdims=True), axis=1)
    return np.minimum((uniforms[:, None] > cumulative).sum(axis=1), len(CAUSES) - 1)


def generate_durations(causes: np.ndarray, components: np.ndarray, normal_draws: np.ndarray) -> np.ndarray:
    h, _, t, _, rt = components.T
    duration = CAUSE_MEDIANS[causes] * (1 + 0.8 * h) * (1 + 0.8 * t + 0.5 * rt) * np.exp(0.65 * normal_draws)
    return np.round(np.clip(duration, MIN_DURATION_MINUTES, MAX_DURATION_MINUTES), 2)


def remove_collisions(candidate_indices: np.ndarray, durations: np.ndarray, times: pd.DatetimeIndex) -> tuple[np.ndarray, int, int]:
    accepted = []
    active_until = times[0]
    collisions = boundaries = 0
    for index, minutes in zip(candidate_indices, durations):
        start = times[index]
        if start < active_until:
            collisions += 1
            continue
        end = start + pd.Timedelta(minutes=float(minutes))
        if end > times[-1]:
            boundaries += 1
            continue
        accepted.append(int(index))
        active_until = end
    return np.asarray(accepted, dtype=int), collisions, boundaries


def simulate(data: IntegratedData, seed: int, annual_rate: float, beta: float) -> tuple[dict, dict]:
    """q = scale * exp(beta*S), calibrated globally before collision rejection.

    q is a synthetic event-generation probability, never an empirical estimate.
    exp(0)=1 gives low-score hours a positive background chance. One global
    scale per scenario preserves spatial contrasts; no municipality-specific
    calibration or manual seasonal multiplier is applied. Common random numbers
    isolate weight sensitivity from unnecessary Monte Carlo variation.
    """
    hours = data.inspection["period"]["exposure_hours_per_barangay"]
    expected_attempts = annual_rate * len(data.geography) * hours / HOURS_PER_YEAR
    sums = {name: 0.0 for name in SCENARIOS}
    maxima = {name: 0.0 for name in SCENARIOS}
    sums_components = np.zeros(5)
    candidate_hist = {name: np.zeros(10, dtype=np.int64) for name in SCENARIOS}
    # The last timestamp is an inclusive end boundary, not a full eligible hour.
    eligible = slice(None, -1)
    for row in data.geography.itertuples(index=False):
        components, _, _, _ = component_arrays(data, row)
        sums_components += components[eligible].sum(axis=0)
        for name, weights in SCENARIOS.items():
            score = propensity(components, weights)[eligible]
            strength = np.exp(beta * score)
            sums[name] += float(strength.sum())
            maxima[name] = max(maxima[name], float(strength.max()))
            candidate_hist[name] += np.histogram(score, bins=BIN_EDGES)[0]
    scales = {name: expected_attempts / total for name, total in sums.items()}
    require(all(scales[name] * maxima[name] < 1 for name in SCENARIOS),
            "Requested rate/response makes q >= 1; choose a smaller rate or response")
    frames = {name: [] for name in SCENARIOS}
    bookkeeping = {name: {"collision_rejections": 0, "boundary_rejections": 0,
                           "sampled_attempts": 0, "expected_attempts": expected_attempts,
                           "probability_scale": scales[name], "maximum_q": scales[name] * maxima[name],
                           "candidate_score_histogram": candidate_hist[name].tolist(),
                           "score_bin_edges": BIN_EDGES.tolist()} for name in SCENARIOS}
    local_times = data.times.tz_convert(LOCAL_TZ)
    for number, row in enumerate(data.geography.itertuples(index=False), 1):
        components, weather_components, weather, ndvi = component_arrays(data, row)
        # PSGC-stable streams originate solely from the configurable seed.
        rng = np.random.default_rng(np.random.SeedSequence([seed, int(row.barangay_psgc)]))
        occurrence_draws = rng.random(len(data.times))
        cause_draws = rng.random(len(data.times))
        duration_draws = rng.standard_normal(len(data.times))
        for name, weights in SCENARIOS.items():
            score = propensity(components, weights)
            q = scales[name] * np.exp(beta * score)
            q[-1] = 0  # no positive-duration event can start at the final boundary
            candidates = np.flatnonzero(occurrence_draws < q)
            cause = cause_indices(components[candidates], weather_components[candidates], cause_draws[candidates])
            durations = generate_durations(cause, components[candidates], duration_draws[candidates])
            accepted, collisions, boundaries = remove_collisions(candidates, durations, data.times)
            positions = np.searchsorted(candidates, accepted)
            cause, durations = cause[positions], durations[positions]
            stats = bookkeeping[name]
            stats["sampled_attempts"] += len(candidates)
            stats["collision_rejections"] += collisions
            stats["boundary_rejections"] += boundaries
            if not len(accepted):
                continue
            starts = local_times[accepted]
            event = pd.DataFrame({
                "outage_id": [f"SYN-{seed}-{row.barangay_psgc}-{x.strftime('%Y%m%dT%H%M')}" for x in starts],
                "is_synthetic": True, "generator_version": GENERATOR_VERSION, "random_seed": seed,
                "barangay_psgc": row.barangay_psgc, "barangay": row.barangay,
                "municipality_psgc": row.municipality_psgc, "municipality": row.municipality,
                "start_time": starts, "end_time": starts + pd.to_timedelta(durations, unit="m"),
                "duration_minutes": durations, "year": starts.year, "month": starts.month,
                "hour": starts.hour, "day_of_week": starts.day_name(),
                "cause_category": np.asarray(CAUSES)[cause],
                "severity_class": np.where(durations < 60, "Minor", np.where(durations < 240, "Moderate", "Major")),
                "synthetic_event_propensity": score[accepted],
                "synthetic_event_generation_probability": q[accepted],
                "cape_j_kg": data.cape[(row.era5_latitude, row.era5_longitude)][accepted],
                "ndvi_annual_mean": ndvi[accepted], "ndvi_year": starts.year,
                "ndvi_period_type": np.where(starts.year == 2026, "year_to_date", "full_year"),
                "ndvi_context": "retrospective annual composite; not event-time NDVI",
                "ndvi_source_start": [f"{year}-01-01" for year in starts.year],
                "ndvi_source_end_exclusive": [NDVI_2026_END_EXCLUSIVE if year == 2026 else f"{year + 1}-01-01" for year in starts.year],
                "weather_spatial_resolution": "municipality",
                "era5_latitude": row.era5_latitude, "era5_longitude": row.era5_longitude,
                "era5_grid_resolution_degrees": 0.25,
            })
            for idx, field in enumerate(SCORE_FIELDS):
                event[field] = components[accepted, idx]
            for idx, field in enumerate(["rainfall_score", "wind_score", "convective_score", "soil_wetness_score"]):
                event[field] = weather_components[accepted, idx]
            for field in WEATHER_FIELDS:
                event[field] = weather[field].to_numpy()[accepted]
            for field in TERRAIN_FIELDS:
                event[field] = getattr(row, field)
            frames[name].append(event)
        if number % 50 == 0 or number == len(data.geography):
            print(f"Simulated {number}/{len(data.geography)} barangays across four scenarios", flush=True)
    require(bool(frames["baseline"]), "No events generated; increase target or selected period")
    outputs = {}
    for name, parts in frames.items():
        outputs[name] = (pd.concat(parts, ignore_index=True).sort_values(["start_time", "barangay_psgc"])
                         .reset_index(drop=True)) if parts else outputs["baseline"].iloc[:0].copy()
    means = sums_components / (len(data.geography) * (len(data.times) - 1))
    bookkeeping["baseline"]["mean_weighted_component_contributions"] = dict(zip(SCORE_FIELDS, means * SCENARIOS["baseline"]))
    return outputs, bookkeeping


def final_validation(events: pd.DataFrame, data: IntegratedData) -> dict:
    require(not events.isna().any().any(), "Missing output values")
    require(events.outage_id.is_unique, "Duplicate outage IDs")
    require(not events.duplicated(["barangay_psgc", "start_time"]).any(), "Duplicate event starts")
    require(events.barangay_psgc.isin(data.geography.barangay_psgc).all(), "Invalid barangay in output")
    require(events.is_synthetic.eq(True).all(), "Missing synthetic labeling")
    require((events.end_time > events.start_time).all(), "Nonpositive event duration")
    require(events.duration_minutes.between(MIN_DURATION_MINUTES, MAX_DURATION_MINUTES).all(), "Duration bounds violated")
    require(np.allclose((events.end_time - events.start_time).dt.total_seconds() / 60,
                        events.duration_minutes), "Stored duration disagrees with timestamps")
    require((events.start_time >= data.times[0]).all() and (events.end_time <= data.times[-1]).all(),
            "Event outside selected analysis interval")
    ordered = events.sort_values(["barangay_psgc", "start_time"])
    previous_end = ordered.groupby("barangay_psgc").end_time.shift()
    overlap_count = int((ordered.start_time < previous_end).sum())
    require(overlap_count == 0, f"Overlapping events: {overlap_count}")
    for field in [*SCORE_FIELDS, "synthetic_event_propensity", "synthetic_event_generation_probability",
                  "rainfall_score", "wind_score", "convective_score", "soil_wetness_score"]:
        require(events[field].between(0, 1).all(), f"Output {field} outside [0,1]")
    return {"overlapping_events": overlap_count, "duplicate_outage_ids": 0,
            "missing_required_values": 0, "duplicate_events": 0,
            "valid_barangays": True, "positive_consistent_durations": True,
            "timestamps_inside_period": True}


def describe(series: pd.Series) -> dict:
    return series.describe(percentiles=[0.05, 0.5, 0.95, 0.99]).to_dict()


def summarize(events: pd.DataFrame, data: IntegratedData, sampling: dict) -> dict:
    hours = data.inspection["period"]["exposure_hours_per_barangay"]
    summary = {**sampling, "total_events": len(events),
               "events_per_barangay_year": len(events) / (len(data.geography) * hours / HOURS_PER_YEAR),
               "events_by_year": events.year.value_counts().sort_index().to_dict(),
               "events_by_municipality": events.municipality.value_counts().sort_index().to_dict(),
               "events_by_barangay": events.barangay_psgc.value_counts().reindex(data.geography.barangay_psgc, fill_value=0).to_dict(),
               "events_by_month": events.month.value_counts().reindex(range(1, 13), fill_value=0).to_dict(),
               "events_by_year_month": events.start_time.dt.strftime("%Y-%m").value_counts().sort_index().to_dict(),
               "events_by_cause": events.cause_category.value_counts().reindex(CAUSES, fill_value=0).to_dict()}
    if events.empty:
        summary["warnings"] = ["No events in this sensitivity scenario"]
        return summary
    summary.update({"duration_minutes": describe(events.duration_minutes),
                    "propensity": describe(events.synthetic_event_propensity),
                    "minimum_start": str(events.start_time.min()), "maximum_end": str(events.end_time.max())})
    n = len(events)
    counts = np.array(list(summary["events_by_barangay"].values()))
    summary["top_10_percent_barangays_event_share"] = float(np.sort(counts)[-math.ceil(len(counts) / 10):].sum() / n)
    summary["spatial_hhi"] = float(np.sum((counts / n) ** 2))
    # Exposure-adjusted month rates account for only Jan-Jun being included in 2026.
    exposure_month = pd.Series(data.times[:-1].tz_convert(LOCAL_TZ).month).value_counts().reindex(range(1, 13), fill_value=0)
    summary["month_events_per_100000_barangay_hours"] = {
        month: (summary["events_by_month"][month] / (int(exposure_month[month]) * len(data.geography)) * 100000
                if exposure_month[month] else None) for month in range(1, 13)}
    event_hist = np.histogram(events.synthetic_event_propensity, BIN_EDGES)[0]
    exposure_hist = np.array(sampling["candidate_score_histogram"])
    summary["event_score_histogram"] = event_hist.tolist()
    rates = np.divide(event_hist * 100000, exposure_hist, out=np.zeros(10), where=exposure_hist > 0)
    summary["events_per_100000_hours_by_score_bin"] = [float(rates[i]) if exposure_hist[i] else None for i in range(10)]
    warnings = []
    for label, mapping in [("municipality", summary["events_by_municipality"]),
                           ("cause", summary["events_by_cause"]), ("month", summary["events_by_month"])]:
        if max(mapping.values(), default=0) / n > 0.80:
            warnings.append(f"More than 80% of events belong to one {label}; review concentration")
    if summary["top_10_percent_barangays_event_share"] > 0.50:
        warnings.append("Top 10% of barangays account for more than half of events")
    if events.weather_hazard_score.ge(0.8).mean() > 0.95:
        warnings.append("Almost all events require extreme weather hazard")
    populated = np.flatnonzero(exposure_hist >= 10000)
    if len(populated) >= 2 and rates[populated[-1]] <= rates[populated[0]]:
        warnings.append("Observed score-bin rates do not increase from lowest to highest well-populated bin")
    if "mean_weighted_component_contributions" in sampling:
        contribution = sampling["mean_weighted_component_contributions"]
        for field in ["vegetation_exposure_score", "terrain_exposure_score"]:
            if contribution[field] > contribution["weather_hazard_score"]:
                warnings.append(f"Mean direct {field} contribution exceeds direct weather contribution; evaluate static exposure influence")
    summary["warnings"] = warnings
    return summary


def total_variation(left: dict, right: dict) -> float:
    keys = sorted(set(left) | set(right))
    a, b = np.array([left.get(k, 0) for k in keys], float), np.array([right.get(k, 0) for k in keys], float)
    return float(np.abs(a / a.sum() - b / b.sum()).sum() / 2) if a.sum() and b.sum() else 1.0


def rank_correlation(left: dict, right: dict) -> float | None:
    """Spearman = Pearson of average ranks, with no optional SciPy dependency."""
    ranks = pd.DataFrame({"left": pd.Series(left), "right": pd.Series(right)}).rank(method="average")
    require(not ranks.isna().any().any(), "Rank comparison has unmatched keys")
    if ranks.left.nunique() < 2 or ranks.right.nunique() < 2:
        return None
    return float(np.corrcoef(ranks.left.to_numpy(), ranks.right.to_numpy())[0, 1])


def sensitivity_report(summaries: dict) -> dict:
    base = summaries["baseline"]
    comparisons = {}
    for name, summary in summaries.items():
        if name == "baseline":
            continue
        count_change = summary["total_events"] / base["total_events"] - 1
        spatial = total_variation(base["events_by_barangay"], summary["events_by_barangay"])
        seasonal = total_variation(base["events_by_month"], summary["events_by_month"])
        cause = total_variation(base["events_by_cause"], summary["events_by_cause"])
        rho = rank_correlation(base["events_by_barangay"], summary["events_by_barangay"])
        comparisons[name] = {"relative_event_count_change": count_change,
                             "spatial_total_variation": spatial, "seasonal_total_variation": seasonal,
                             "cause_total_variation": cause, "barangay_count_spearman": rho,
                             "substantial_change_flag": bool(abs(count_change) > 0.1 or max(spatial, seasonal, cause) > 0.1
                                                             or rho is None or rho < 0.8)}
    return {"scenario_weights_H_V_T_WV_RT": SCENARIOS,
            "interpretation": "Common random numbers, same target attempts, global scale recalibrated for each scenario; comparisons concern shape and collision-adjusted counts, not empirical validity",
            "terrain_reduced_scope": "Remove both direct T and RT from occurrence propensity; duration model remains fixed to isolate propensity-weight changes",
            "flag_thresholds": "Arbitrary diagnostics: >10% count change, >0.10 total variation, or Spearman <0.8; not significance tests",
            "comparisons": comparisons, "summaries": summaries}


def methodology_text(data: IntegratedData, args, summary: dict) -> str:
    return f"""# SYNTHETIC Bataan outage events — methodology

Real PENELCO outage records are unavailable. These events exist solely for GIS
dashboard development, testing, and demonstration. They are not actual PENELCO
outage history, empirical reliability estimates, or an outage forecast.

## Provenance and integration

Real inputs: supplied geography, municipality hourly Open-Meteo weather, ERA5
CAPE, barangay terrain, and annual NDVI composites. Synthetic outputs: event
occurrence, cause, duration, severity, and all scores/probabilities derived by
the simulation. See input_inspection.json and run_manifest.json for schemas,
coverage, normalization limits, joins, input hashes, configuration and versions.

Period: {data.inspection['period']['start_local']} through
{data.inspection['period']['end_local']} (inclusive event boundaries).
The first eight requested local hours lack ERA5 coverage and are excluded when
the requested start is January 1, 2019. No dynamic records are fabricated.
Exposure denominators use elapsed hours, excluding the final boundary instant.
Open-Meteo Asia/Singapore (+08:00) is localized using its metadata and converted
to UTC. ERA5 CF-1.7 epoch time is UTC. All joins use UTC; event timestamps carry
Asia/Manila +08:00 offsets. CF reference:
https://cfconventions.org/Data/cf-conventions/cf-conventions-1.7/build/ch04s04.html

The supplied GeoJSON is the spatial backbone. PSGC strings retain leading zeros.
Terrain and NDVI join by barangay PSGC. Weather joins through the explicit
Balanga -> City of Balanga alias and municipality identity. Municipality weather
is inherited by its barangays; this does not increase its spatial resolution.
ERA5 uses nearest coordinate grid cells to interior representative points
computed in UTM 51N, with its 0.25-degree grid recorded on every event.

NDVI is a retrospective annual composite, not NDVI observed at the event hour.
For 2026, the supplied Jan 1–Sep 23 composite is used for Jan–Jun synthetic
events, explicitly approved by the user. Exact composite windows are traced
to scripts/bataan_vegetation_2019_2026.js rather than encoded in the CSV itself.
Full-year composites also include observations later than early-year events.
NDVI is held constant within a year, without invented interpolation. It does
not establish vegetation proximity to power lines. Reported 100% valid composite
pixel coverage is not continuous cloud-free temporal coverage.

## Heuristic scores

Each component uses clipped empirical 1st–99th percentile scaling to [0,1].
Fits are pooled across locations in the selected period: municipality-hours
for weather, unique assigned grid-cell-hours for CAPE, barangay-years for NDVI,
and barangays for terrain. This retains spatial contrasts and limits outliers.
Constant or invalid components cause an error, not silent imputation.

R = precipitation_mm; W = wind_gusts_10m_m_s; C = cape (J/kg);
M = soil_moisture_7_to_28cm_m3_m3; V = annual mean_ndvi;
T = mean_slope_deg. Highly correlated slope and relief measures are not added
repeatedly. T is a single-indicator terrain score. Other terrain variables,
temperature, humidity and wind speed remain context. No electrical infrastructure
counts or density are used. ERA5 rain/wind are excluded to avoid double counting.
CIN and cloud-base height have missing values and are not used.

H = 0.30 R + 0.35 W + 0.20 C + 0.15 M.
WV = W*V; RT = R*T.
S = 0.50 H + 0.20 V + 0.10 T + 0.12 WV + 0.08 RT.

S is SYNTHETIC EVENT PROPENSITY. Weights are simulation assumptions, not learned
coefficients. Weather dominates the specified weights; gusts receive the largest
weather weight; CAPE adds convective context; deeper soil moisture represents
wetness. Terrain provides modest context, not evidence of causation. Interaction
terms describe assumed compound scenarios. No calendar-season multiplier is used.

## Discrete events and restoration

q = k * exp({args.response} * S) is SYNTHETIC EVENT-GENERATION PROBABILITY.
For each scenario k is calibrated globally so sum(q) equals the target attempts
of {args.events_per_barangay_year} per barangay-year (365.2425 days). This default
is arbitrary and configurable. exp(0)=1 supplies a nonzero background chance.
There is no calibration against real outage records. Configurations making q>=1
are rejected. Global calibration preserves geographic differences. Independent
Bernoulli draws generate candidate starts; collisions/boundary rejections mean
the realized rate can differ from the target. No overlapping incidents start
within the same barangay. An event exactly at the previous end is allowed.

Cause weights are stochastic: weather = 0.20+1.6H+0.4R+0.4W+0.4C;
vegetation = 0.15+3WV; equipment = 0.70; unknown = 0.20. Normalize these weights
to sum to one before sampling. They do not estimate PENELCO cause proportions.

Duration is generated separately: median-by-cause [120,100,80,45] minutes for
weather, vegetation, equipment, unknown, multiplied by (1+0.8H),
(1+0.8T+0.5RT), and exp(0.65Z), where Z is standard normal. Clip to 15–2880
minutes and round to 0.01 minute. These bounds and effects are demonstration
assumptions. Reject events ending outside the selected period. Severity is
Minor <60 minutes, Moderate 60–<240, Major >=240; these are not official utility
classifications. No detailed equipment failure mechanisms are invented.

One configurable seed ({args.seed}) controls NumPy streams keyed by barangay
PSGC. Sorted inputs and fixed draw arrays make runs reproducible and provide
common random numbers across sensitivity scenarios. The manifest records
software versions; reproducibility is checked in this environment. Outage IDs
use the SYN prefix, seed, PSGC and start hour; each row also has is_synthetic.

## Diagnostics, sensitivity and limitations

This run produced {summary['total_events']:,} baseline events, a realized rate
of {summary['events_per_barangay_year']:.4f} per barangay-year. See diagnostics.json
for counts, durations, score distributions, exposure-adjusted month rates,
collision/boundary rejections and sanity warnings. Annual/monthly comparisons
must account for partial 2019 boundary and the six-month 2026 exposure.

Sensitivity changes H/V/T/WV/RT to (0.60,0.15,0.05,0.12,0.08),
(0.40,0.30,0.10,0.12,0.08), and (0.60,0.24,0,0.16,0). The last removes
both direct terrain and its occurrence interaction, while holding the duration
model fixed. Only the baseline event CSV is saved. Scenario summaries compare
counts, geographic/seasonal/cause shares, concentration and barangay rankings.
Substantial-change flags use disclosed arbitrary screening thresholds rather
than statistical significance or empirical validation.

Only one barangay-hour block is materialized at a time; no large candidate CSV
is written. Weather sharing produces common environmental timing, but the
generator does not model feeder topology, customer exposure, network cascades,
or coordinated multi-barangay incidents. Hotspots reflect simulation assumptions
and supplied environmental structure; they cannot establish real reliability
differences. Neither sensitivity stability nor plausible-looking patterns
validate the dataset against actual utility behavior.
"""


def self_test() -> None:
    """Small analytical tests for sampling, boundaries, and reproducibility."""
    norm = Normalizer(10, 20)
    np.testing.assert_array_equal(norm.transform([0, 10, 15, 20, 30]), [0, 0, .5, 1, 1])
    try:
        Normalizer.fit([1, 1, 1], "constant")
    except ValueError:
        pass
    else:
        raise AssertionError("Constant normalization must fail")
    np.testing.assert_allclose(propensity(np.ones((2, 5)), SCENARIOS["baseline"]), [1, 1])
    times = pd.date_range("2020-01-01", periods=6, freq="h", tz="UTC")
    accepted, collisions, boundaries = remove_collisions(np.array([0, 1, 2, 4, 5]),
                                                        np.array([120, 30, 60, 120, 15]), times)
    np.testing.assert_array_equal(accepted, [0, 2])
    assert (collisions, boundaries) == (1, 2)
    assert local_timestamp("2019-01-01 08:00").tz_convert("UTC") == pd.Timestamp("2019-01-01", tz="UTC")
    components = np.tile([0.5, 0.5, 0.5, 0.25, 0.25], (1000, 1))
    weather = np.full((1000, 4), 0.5)
    rng1, rng2 = np.random.default_rng(42), np.random.default_rng(42)
    np.testing.assert_array_equal(rng1.random(1000), rng2.random(1000))
    causes = cause_indices(components, weather, np.linspace(0, 0.999, 1000))
    assert set(causes) == {0, 1, 2, 3}
    durations = generate_durations(causes, components, np.linspace(-20, 20, 1000))
    assert durations.min() == MIN_DURATION_MINUTES and durations.max() == MAX_DURATION_MINUTES
    assert total_variation({"a": 1, "b": 0}, {"a": 0, "b": 1}) == 1.0
    assert np.isclose(rank_correlation({"a": 1, "b": 2, "c": 2}, {"c": 4, "a": 1, "b": 4}), 1)
    assert np.isclose(rank_correlation({"a": 1, "b": 2}, {"a": 2, "b": 1}), -1)
    assert rank_correlation({"a": 1, "b": 1}, {"a": 2, "b": 1}) is None
    mixed_times = pd.to_datetime(["2020-01-01 00:00:00+08:00", "2020-01-01 00:00:00.600+08:00"],
                                format="ISO8601", utc=True)
    assert (mixed_times[1] - mixed_times[0]).total_seconds() == 0.6
    for weights in SCENARIOS.values():
        assert np.isclose(weights.sum(), 1)
    print("Self-tests passed: normalization, formulas, timezones, collisions, boundaries, causes, duration bounds, RNG")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    parser.add_argument("--events-per-barangay-year", type=float, default=TARGET_SYNTHETIC_EVENTS_PER_BARANGAY_YEAR)
    parser.add_argument("--response", type=float, default=PROPENSITY_RESPONSE)
    parser.add_argument("--start", default=START, help="Manila local time; hourly")
    parser.add_argument("--end", default=END, help="Inclusive last allowed event timestamp in Manila time")
    parser.add_argument("--output-dir", type=Path, help="NEW directory inside data/synthetic; existing directories are refused")
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    require(args.seed >= 0, "Seed must be nonnegative")
    require(np.isfinite(args.events_per_barangay_year) and args.events_per_barangay_year > 0,
            "Target event rate must be finite and positive")
    require(np.isfinite(args.response) and 0 < args.response <= 20, "Response must be in (0,20]")
    out = (args.output_dir or OUTPUT_ROOT / ("run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"))).resolve()
    require(out.is_relative_to(OUTPUT_ROOT.resolve()) and out != OUTPUT_ROOT.resolve(),
            "Output directory must be a new child of data/synthetic")
    require(not out.exists(), f"Refusing to overwrite existing output directory: {out}")
    before = fingerprints()
    print("Inspecting real inputs; outputs are explicitly SYNTHETIC.", flush=True)
    data = load_and_validate(args.start, args.end)
    print(json.dumps({"period": data.inspection["period"], "joins": data.inspection["joins"],
                      "normalization": data.inspection["normalization"], "warnings": data.inspection["warnings"]}, indent=2, default=json_default), flush=True)
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "input_inspection.json", data.inspection)
    if args.inspect_only:
        require(before == fingerprints(), "Source inputs changed during inspection")
        print(f"Inspection saved: {out}")
        return
    events, sampling = simulate(data, args.seed, args.events_per_barangay_year, args.response)
    summaries = {}
    for name in SCENARIOS:
        checks = final_validation(events[name], data)
        summaries[name] = summarize(events[name], data, sampling[name])
        summaries[name]["validation"] = checks
    sensitivity = sensitivity_report(summaries)
    # Validate JSON serialization too, before publishing the event CSV.
    json.dumps(sensitivity, default=json_default, allow_nan=False)
    require(before == fingerprints(), "Source inputs changed during generation; refusing to publish events")
    primary = out / "synthetic_outage_events.csv"
    csv_text = events["baseline"].to_csv(index=False, float_format="%.10g", lineterminator="\n")
    # Validate serialized content before publishing a CSV. ISO8601 handles both
    # integral and fractional seconds without dropping the explicit UTC offset.
    saved = pd.read_csv(io.StringIO(csv_text), dtype={"barangay_psgc": str, "municipality_psgc": str})
    for column in ["start_time", "end_time"]:
        saved[column] = pd.to_datetime(saved[column], format="ISO8601", utc=True).dt.tz_convert(LOCAL_TZ)
    final_validation(saved, data)
    require(saved.barangay_psgc.equals(events["baseline"].barangay_psgc), "PSGC round-trip mismatch")
    with primary.open("x", encoding="utf-8", newline="") as handle:
        handle.write(csv_text)
    write_json(out / "diagnostics.json", summaries["baseline"])
    write_json(out / "sensitivity.json", sensitivity)
    manifest = {"generator_version": GENERATOR_VERSION, "is_synthetic": True,
                "seed": args.seed, "target_events_per_barangay_year": args.events_per_barangay_year,
                "propensity_response": args.response, "weather_weights_R_W_C_M": WEATHER_WEIGHTS,
                "scenario_weights_H_V_T_WV_RT": SCENARIOS, "inputs": before,
                "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "event_csv_sha256": hashlib.sha256(primary.read_bytes()).hexdigest(),
                "source_inputs_unchanged": True, "csv_round_trip_validation": "passed before publishing",
                "software": {"python": platform.python_version(), "numpy": np.__version__,
                             "pandas": pd.__version__, "geopandas": gpd.__version__, "xarray": xr.__version__},
                "ndvi_policy": "User-approved retrospective annual context; 2026 includes imagery through Sep 23",
                "period": data.inspection["period"]}
    write_json(out / "run_manifest.json", manifest)
    with (out / "METHODOLOGY.md").open("x", encoding="utf-8") as handle:
        handle.write(methodology_text(data, args, summaries["baseline"]))
    print(json.dumps(summaries["baseline"], indent=2, default=json_default), flush=True)
    print("Sensitivity:", json.dumps(sensitivity["comparisons"], indent=2), flush=True)
    print(f"SYNTHETIC event CSV and diagnostics saved under: {out}", flush=True)


if __name__ == "__main__":
    main()
