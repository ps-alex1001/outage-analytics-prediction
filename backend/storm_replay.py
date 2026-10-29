"""Local 2025–2026 storm-track selection and retrospective frozen-model replay."""
from __future__ import annotations

from functools import lru_cache
from threading import Lock

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException

from backend.datasets import DATA_ROOT
from backend.forecast import geography, service
from backend.forecast_features import FEATURES, HOURLY_VARIABLES, TZ, build_rows
from backend.forecast_model import ModelUnavailable, model_values
from backend.scenario import Adjustments, ScenarioRequest, simulate
from backend.weather_forecast import WeatherUnavailable

router = APIRouter(prefix="/api/storm-replays", tags=["storm replays"])
STORM_PATH = DATA_ROOT / "storm_replays/ibtracs_phl_hourly_processed_imputed.csv"
WEATHER_PATH = DATA_ROOT / "weather/open_meteo_bataan_2019_01_to_2026_06.csv"
ERA_PATH = DATA_ROOT / "weather/era5_bataan_2019_01_to_2026_06.nc"
ARCHIVE_START = pd.Timestamp("2019-01-01", tz=TZ)
# ERA5 extends eight hours later locally; use the common municipal weather boundary.
ARCHIVE_END = pd.Timestamp("2026-06-30T23:00:00", tz=TZ)
REFERENCE = (14.67, 120.47)
LOOKBACK = 74
RAW = {"precipitation_mm": "precipitation", "wind_speed_10m_m_s": "wind_speed_10m",
       "wind_gusts_10m_m_s": "wind_gusts_10m", "temperature_2m_c": "temperature_2m",
       "relative_humidity_2m_pct": "relative_humidity_2m",
       "soil_moisture_7_to_28cm_m3_m3": "soil_moisture_7_to_28cm"}
NOTICE = "Retrospective experimental model scores for synthetic outage onset; not validated historical forecasts or operational outage probabilities."
_lock = Lock()


def number(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def distance(lat, lon):
    y, x = np.radians(np.asarray(lat)), np.radians(np.asarray(lon))
    y0, x0 = np.radians(REFERENCE)
    a = np.sin((y-y0)/2)**2 + np.cos(y)*np.cos(y0)*np.sin((x-x0)/2)**2
    return 6371 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def read_tracks(path):
    f = pd.read_csv(path, dtype={"storm_id": str})
    required = {"storm_id", "timestamp_pht", "storm_lat", "storm_lon", "wmo_wind", "wmo_pres"}
    if not required.issubset(f):
        raise ValueError("Storm CSV does not match the inspected track contract")
    f["time"] = pd.to_datetime(f.timestamp_pht, errors="raise").dt.tz_localize(TZ)
    if f.duplicated(["storm_id", "time"]).any() or not f.time.equals(f.time.dt.floor("h")):
        raise ValueError("Storm-hour keys are duplicated or not hourly aligned")
    f = f.loc[(f.time >= pd.Timestamp("2025-01-01", tz=TZ)) & (f.time < pd.Timestamp("2027-01-01", tz=TZ))].copy()
    if not np.isfinite(f[["storm_lat", "storm_lon"]].to_numpy()).all():
        raise ValueError("Missing storm coordinates")
    f["reference_distance_km"] = distance(f.storm_lat, f.storm_lon)
    return f.sort_values(["storm_id", "time"])


def track_catalog(f):
    events = []
    for sid, g in f.groupby("storm_id"):
        start, end = g.time.min(), g.time.max()
        closest = g.loc[g.reference_distance_km.idxmin()]
        first = max(start, min(closest.time - pd.Timedelta(hours=12), end - pd.Timedelta(hours=23)))
        enough = end - start >= pd.Timedelta(hours=23)
        available = enough and first - pd.Timedelta(hours=LOOKBACK) >= ARCHIVE_START and first + pd.Timedelta(hours=22) <= ARCHIVE_END
        events.append({"id": sid, "name": None, "title": f"Storm {sid}", "kind": "historical",
            "start": start.isoformat(), "end": end.isoformat(), "duration_hours": (end-start).total_seconds()/3600,
            "track_hours": len(g), "closest_reference_km": float(closest.reference_distance_km),
            "window_start": first.isoformat(), "window_end": (first+pd.Timedelta(hours=24)).isoformat(),
            "available": bool(available), "unavailable_reason": None if available else "A 24-hour window and its lookback must lie inside the local weather archive, ending June 2026.",
            "weather_info": "Archived municipal rainfall, wind, gusts, temperature, humidity, soil moisture; ERA5 CAPE" if available else "Historical weather coverage unavailable for this window",
            "track_wind_present": bool(g.wmo_wind.notna().any()), "track_pressure_present": bool(g.wmo_pres.notna().any())})
    return sorted(events, key=lambda e: e["closest_reference_km"])


@lru_cache(maxsize=1)
def tracks():
    return read_tracks(STORM_PATH)


@lru_cache(maxsize=1)
def events():
    return track_catalog(tracks())


def weather_locations(places):
    groups = {}
    for p in places:
        identity = p.get("source_weather_location_id", p["weather_location_id"])
        g = groups.setdefault(identity, {"id": identity, "latitude": p["latitude"],
            "longitude": p["longitude"], "municipalities": []})
        if p["municipality"] not in g["municipalities"]:
            g["municipalities"].append(p["municipality"])
    return list(groups.values())


def historical_places(places):
    # The original V2 builder groups weather by municipality, not coordinate.
    # Same-coordinate series can differ (e.g. elevation-adjusted temperature).
    return [{**p, "source_weather_location_id": p["weather_location_id"],
             "weather_location_id": f'weather:municipality:{p["municipality_psgc"]}'} for p in places]


def load_history(places, first):
    """Read just the required 98 source hours; never fill missing weather values."""
    index = pd.date_range(first-pd.Timedelta(hours=LOOKBACK), first+pd.Timedelta(hours=23), freq="h")
    lower, upper = index[0].strftime("%Y-%m-%d %H:%M:%S"), index[-1].strftime("%Y-%m-%d %H:%M:%S")
    chunks = []
    for chunk in pd.read_csv(WEATHER_PATH, usecols=["municipality", "time", *RAW], chunksize=100000):
        part = chunk.loc[(chunk.time >= lower) & (chunk.time <= upper)]
        if not part.empty: chunks.append(part)
    weather = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame(columns=["municipality", "time", *RAW])
    # Same audited alias as the V2 training-dataset builder; otherwise the shared
    # Balanga/Pilar source point would silently lose all municipal weather inputs.
    weather["municipality"] = weather.municipality.replace({"Balanga": "City of Balanga"})
    if weather.duplicated(["municipality", "time"]).any():
        raise ValueError("Historical weather has duplicate municipality-hour keys")
    frames = {}
    for place in places:
        if place["weather_location_id"] in frames: continue
        municipal = place["municipality"]
        f = weather.loc[weather.municipality == municipal].copy()
        f.index = pd.DatetimeIndex(pd.to_datetime(f.time)).tz_localize(TZ)
        f = f[list(RAW)].rename(columns=RAW).reindex(index).astype(float)
        # Protect the physical contract without fabricating replacement values.
        for col in f:
            values = f[col]
            invalid = ~np.isfinite(values)
            if col != "temperature_2m": invalid |= values < 0
            if col == "relative_humidity_2m": invalid |= values > 100
            if col == "soil_moisture_7_to_28cm": invalid |= values > 1
            f.loc[invalid, col] = np.nan
        f["cape"] = np.nan  # CAPE is joined separately at the original ERA5 cell.
        frames[place["weather_location_id"]] = f
    import xarray as xr
    with xr.open_dataset(ERA_PATH, engine="h5netcdf") as ds:
        if ds.cape.attrs.get("units") != "J kg**-1" or ds.cape.attrs.get("GRIB_stepType") != "instant":
            raise ValueError("Historical CAPE units/interval do not match the model contract")
        times = pd.DatetimeIndex(ds.valid_time.values).tz_localize("UTC").tz_convert(TZ)
        use = np.flatnonzero((times >= index[0]) & (times <= index[-1]))
        for p in places:
            identity = p["cape_location_id"]
            if identity in frames: continue
            values = ds.cape.sel(latitude=p["era5_latitude"], longitude=p["era5_longitude"]).isel(valid_time=use).values.astype(float)
            values[(values < 0) | ~np.isfinite(values)] = np.nan
            f = pd.DataFrame(np.nan, columns=HOURLY_VARIABLES, index=index)
            f["cape"] = pd.Series(values, index=times[use]).reindex(index)
            frames[identity] = f
    return frames


def hourly_weather(places, frames, cutoffs):
    result = []
    for loc in weather_locations(places):
        keys = {p["weather_location_id"] for p in places
                if p.get("source_weather_location_id", p["weather_location_id"]) == loc["id"]}
        for cutoff in cutoffs:
            label = cutoff-pd.Timedelta(hours=1)
            rows = pd.DataFrame([frames[key].reindex([label]).iloc[0] for key in sorted(keys)])
            values = {"rainfall": rows.precipitation, "wind": rows.wind_speed_10m * 3.6, "gusts": rows.wind_gusts_10m * 3.6}
            result.append({"location_id": loc["id"], "prediction_cutoff": cutoff.isoformat(), "weather_label": label.isoformat(),
                **{key: number(series.mean()) for key, series in values.items()},
                "municipal_series_count": len(keys), "available_counts": {key: int(series.notna().sum()) for key, series in values.items()}})
    return result


def build_replay(event, track_frame, places, frames, model):
    first = pd.Timestamp(event["window_start"])
    cutoffs = pd.date_range(first, periods=24, freq="h")
    matrix = build_rows(places, frames, cutoffs)
    values = matrix[FEATURES].to_numpy("float32")
    complete = np.isfinite(values).all(axis=1)
    scores = np.full(len(matrix), np.nan)
    if complete.any(): scores[complete] = model.predict(model_values(model, matrix.loc[complete]))
    lookup = {p["barangay_psgc"]: p for p in places}
    points = []
    for i, row in enumerate(matrix.to_dict("records")):
        p, cutoff = lookup[row["barangay_psgc"]], row["prediction_time"]
        missing = [f for f in FEATURES if not np.isfinite(row[f])]
        points.append({**{key: p[key] for key in ("barangay_psgc", "barangay", "municipality_psgc", "municipality", "weather_location_id", "cape_location_id")},
            "prediction_cutoff": cutoff.isoformat(), "target_start": cutoff.isoformat(),
            "target_end": (cutoff+pd.Timedelta(hours=1)).isoformat(),
            "last_weather_label": (cutoff-pd.Timedelta(hours=1)).isoformat(),
            "score": number(scores[i]), "status": "experimental" if complete[i] else "insufficient_weather",
            "missing_features": missing, "assumption": "no_active_outage_at_target_cutoff", "current_outage_status": "unknown_no_live_feed"})
        points[-1]["source_weather_location_id"] = p.get("source_weather_location_id", p["weather_location_id"])
    selected = track_frame.loc[track_frame.time.isin(cutoffs)]
    track = [{"time": r.time.isoformat(), "latitude": number(r.storm_lat), "longitude": number(r.storm_lon),
              "source_wind": number(r.wmo_wind), "source_pressure": number(r.wmo_pres)} for r in selected.itertuples()]
    return {"kind": "historical", "event": event, "hours": 24, "barangay_count": len(places),
        "model_id": model.manifest["model_id"], "notice": NOTICE, "points": points,
        "weather_locations": weather_locations(places), "weather_hourly": hourly_weather(places, frames, cutoffs),
        "track": track, "provenance": {"storm_file": STORM_PATH.name,
            "track_processing": "Processed/imputed file; per-value imputation flags and storm names not supplied",
            "track_wind_pressure_units": "Not specified by the supplied file; never used as local weather inputs",
            "weather_file": WEATHER_PATH.name, "cape_file": ERA_PATH.name, "lookback_hours": LOOKBACK,
            "source_resolution": "12 municipal weather series at 7 shared source coordinates; CAPE at 4 assigned ERA5 cells",
            "display_aggregation": "Map/chart weather averages available municipal series sharing a coordinate, then charts average source points. Model inference retains all 12 series separately; identical coordinates do not imply identical measurements. Per-point contributing counts are retained.",
            "timing": "(t,t+1h], using source labels through t−1h. Charts/map weather show the last input hour.",
            "weather_kind": "Archived gridded estimates, not station observations or archived issued forecasts",
            "reference": {"latitude": REFERENCE[0], "longitude": REFERENCE[1]},
            "window_selection": "24 hours centered around closest track approach to the stated Bataan reference, constrained to supplied track coverage",
            "supplied_distance_column": "Not used; its municipality/reference is unspecified"}}


@lru_cache(maxsize=4)
def _historical_replay(storm_id, model_id):
    event = next((e for e in events() if e["id"] == storm_id), None)
    if event is None: raise HTTPException(404, "Unknown 2025–2026 storm ID")
    if not event["available"]: raise HTTPException(422, event["unavailable_reason"])
    places, _ = geography()
    places = historical_places(places)
    frames = load_history(places, pd.Timestamp(event["window_start"]))
    return build_replay(event, tracks().loc[tracks().storm_id == storm_id], places, frames, service().model())


def historical_replay(storm_id):
    if not any(e["id"] == storm_id for e in events()):
        raise HTTPException(404, "Unknown 2025–2026 storm ID")
    return _historical_replay(storm_id, service().model().manifest["model_id"])


def forecasted_replay():
    preset = Adjustments(precipitation=10, wind_speed=20, wind_gusts=30, temperature=-2, humidity=10, soil_moisture=.05, cape=500)
    result = simulate(ScenarioRequest(mode="storm", adjustments=preset))
    places, _ = geography()
    points = [{**p, "score": p["scenario_score"], "status": p["scenario_status"], "missing_features": p["scenario_missing_features"]} for p in result["points"]]
    hourly = [{"location_id": w["location_id"], "prediction_cutoff": w["prediction_cutoff"], "weather_label": w["weather_label"],
        "rainfall": w["scenario"]["precipitation"], "wind": w["scenario"]["wind_speed"], "gusts": w["scenario"]["wind_gusts"]}
        for w in result["weather_hourly"] if w["location_id"].startswith("weather:")]
    times = sorted({p["prediction_cutoff"] for p in points})
    return {"kind": "synthetic_forecast", "event": {"id": "forecasted", "title": "Forecasted", "kind": "synthetic_forecast",
            "window_start": times[0], "window_end": (pd.Timestamp(times[-1])+pd.Timedelta(hours=1)).isoformat()},
        "hours": 24, "barangay_count": len(places), "model_id": result["model_id"],
        "notice": "Synthetic storm demonstration on current forecast weather. Experimental synthetic-outage onset model scores, not a forecast of a real named storm or operational outage probabilities.",
        "points": points, "weather_locations": weather_locations(places), "weather_hourly": hourly, "track": [],
        "provenance": {"weather": result["weather"], "issued_at": result["issued_at"], "preset": preset.model_dump(),
            "intensity_profile": result["intensity_profile"], "clipping_counts": result["clipping_counts"], "timing": result["timing_note"],
            "source_resolution": "7 municipal source points; CAPE at 4 assigned cells", "synthetic": True}}


@router.get("")
def storm_catalog():
    try:
        return {"events": events(), "years": [2025, 2026], "synthetic": {"id": "forecasted", "title": "Forecasted", "kind": "synthetic_forecast"},
                "notice": "Names absent in supplied CSV; storm IDs are shown. Track values are processed/imputed and are not local weather measurements.",
                "archive_end": ARCHIVE_END.isoformat()}
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, "Local storm catalog unavailable or invalid") from exc


@router.get("/{storm_id}")
def storm_replay(storm_id: str):
    try:
        with _lock:
            return forecasted_replay() if storm_id == "forecasted" else historical_replay(storm_id)
    except (OSError, ValueError, KeyError, ModelUnavailable, WeatherUnavailable) as exc:
        raise HTTPException(503, f"Replay inputs unavailable: {exc}") from exc
