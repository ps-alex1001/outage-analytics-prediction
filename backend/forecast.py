"""Experimental forecast scores. No live outage probabilities or operational alerts."""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from threading import Lock

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from backend.datasets import DATA_ROOT
from backend.forecast_features import FEATURES, TERRAIN, TZ, build_rows
from backend.forecast_model import ForecastModel, ModelUnavailable
from backend.weather_forecast import OpenMeteoClient, WeatherUnavailable, utc_now

router = APIRouter(prefix="/api/forecast", tags=["experimental forecasting"])
NOTICE = ("Experimental synthetic-model score, not a calibrated real outage probability. "
          "Forecast weather differs from evaluated historical inputs; no operational alerts are issued.")


@lru_cache(maxsize=1)
def geography():
    try:
        audit = DATA_ROOT / "ml/v2/audit"
        geo = pd.read_csv(audit / "geographic_crosswalk.csv", dtype=str)
        terrain = pd.read_csv(DATA_ROOT / "terrain/bataan_barangay_terrain.csv", dtype={"barangay_psgc": str})
        coordinates = pd.read_csv(audit / "weather_coordinate_groups.csv")
        if not geo.barangay_psgc.is_unique or not terrain.barangay_psgc.is_unique or not coordinates.municipality.is_unique:
            raise ValueError("Geographic keys are duplicated")
        geo = geo.merge(terrain[["barangay_psgc", *TERRAIN]], on="barangay_psgc", validate="one_to_one", how="left")
        geo = geo.merge(coordinates, on="municipality", validate="many_to_one", how="left")
        if len(geo) != 237 or geo.municipality_psgc.nunique() != 12 or geo.isna().any().any():
            raise ValueError("Incomplete V2 geographic mapping")
        for name in [*TERRAIN, "latitude", "longitude", "era5_latitude", "era5_longitude"]:
            geo[name] = pd.to_numeric(geo[name], errors="raise")
            if not np.isfinite(geo[name]).all():
                raise ValueError("Nonfinite geographic fields")
        places, by_coordinate = [], {}
        for row in geo.to_dict("records"):
            for role, lat, lon in [("weather", row["latitude"], row["longitude"]),
                                   ("cape", row["era5_latitude"], row["era5_longitude"])]:
                identity = f"{role}:{lat:.6f},{lon:.6f}"
                row[role + "_location_id"] = identity
                point = by_coordinate.setdefault((lat, lon), {"latitude": lat, "longitude": lon, "ids": []})
                if identity not in point["ids"]:
                    point["ids"].append(identity)
            places.append(row)
        return places, list(by_coordinate.values())
    except (OSError, ValueError, KeyError) as exc:
        raise ModelUnavailable("V2 geography, terrain or weather mapping is unavailable") from exc


class ForecastService:
    def __init__(self, model_dir=None, weather=None, clock=utc_now, catalog=geography):
        self.model_dir = Path(model_dir or os.getenv("FORECAST_MODEL_DIR", str(DATA_ROOT / "models/outage_v2_xgboost")))
        self.weather = weather or OpenMeteoClient(
            os.getenv("FORECAST_CACHE_DIR", str(DATA_ROOT / "forecasts/weather")),
            model=os.getenv("FORECAST_WEATHER_MODEL", "ecmwf_ifs025"),
        )
        self.clock, self.catalog = clock, catalog
        self._model = None
        self._model_lock = Lock()
        self._score_lock = Lock()
        self._cached = None

    def model(self):
        with self._model_lock:
            if self._model is None:
                self._model = ForecastModel(self.model_dir)
            return self._model

    def metadata(self):
        status, error, info = "ready", None, None
        try:
            model = self.model()
            places, locations = self.catalog()
            info = {"model_id": model.manifest["model_id"], "training_rows": model.manifest["training_rows"],
                    "parity_rows_verified": model.parity_rows, "held_out_metrics": model.manifest.get("held_out_metrics")}
        except ModelUnavailable as exc:
            status, error, places, locations = "unavailable", str(exc), [], []
        municipalities = {p["municipality_psgc"]: p["municipality"] for p in places}
        return {"status": status, "error": error, "scope": "experimental_synthetic", "notice": NOTICE,
                "model": info, "features": FEATURES, "timezone": TZ, "target_interval": "(t,t+1h]",
                "supported_hours": [1, 48], "forecast_weather_validated": False,
                "weather_model": self.weather.model, "unique_weather_coordinates": len(locations),
                "barangay_count": len(places), "municipalities": [
                    {"municipality_psgc": key, "municipality": name} for key, name in sorted(municipalities.items())],
                "current_outage_status": "unknown_no_live_feed", "automatic_alerts": False}

    def latest(self, hours=24, municipality_psgc=None, barangay_psgc=None):
        if not 1 <= hours <= 48:
            raise ValueError("Forecast hours must be between 1 and 48")
        model = self.model()
        places, locations = self.catalog()
        if municipality_psgc and municipality_psgc not in {p["municipality_psgc"] for p in places}:
            raise HTTPException(404, "Unknown municipality PSGC")
        selected = [p for p in places if (not municipality_psgc or p["municipality_psgc"] == municipality_psgc)
                    and (not barangay_psgc or p["barangay_psgc"] == barangay_psgc)]
        if not selected:
            raise HTTPException(404, "No matching barangay in this municipality")
        snapshot = self.weather.get(locations)
        issued = pd.Timestamp(self.clock()).tz_convert(TZ)
        age = (issued.to_pydatetime() - snapshot.retrieved_at).total_seconds()
        if age < 0:
            raise WeatherUnavailable("Weather snapshot retrieval time is in the future")
        first_cutoff = issued.ceil("h")
        # Cache a complete province/48-hour batch, then filter without new API calls or fits.
        key = (snapshot.snapshot_id, first_cutoff.isoformat(), model.manifest["model_id"], snapshot.stale)
        with self._score_lock:
            if self._cached is None or self._cached[0] != key:
                cutoffs = pd.date_range(first_cutoff, periods=48, freq="h")
                matrix = build_rows(places, snapshot.frames, cutoffs)
                values = matrix[FEATURES].to_numpy("float32")
                complete = np.isfinite(values).all(axis=1)
                scores = np.full(len(values), np.nan)
                if not snapshot.stale and complete.any():
                    scores[complete] = model.predict(values[complete])
                matrix["score"] = scores
                matrix["complete"] = complete
                self._cached = key, matrix
            matrix = self._cached[1]
        selected_ids = {p["barangay_psgc"] for p in selected}
        rows = matrix.loc[matrix.barangay_psgc.isin(selected_ids)
                          & (matrix.prediction_time < first_cutoff + pd.Timedelta(hours=hours))]
        lookup = {p["barangay_psgc"]: p for p in selected}
        points = []
        for row in rows.to_dict("records"):
            place = lookup[row["barangay_psgc"]]
            missing = [f for f in FEATURES if not np.isfinite(row[f])]
            points.append({
                "barangay_psgc": place["barangay_psgc"], "barangay": place["barangay"],
                "municipality_psgc": place["municipality_psgc"], "municipality": place["municipality"],
                "prediction_cutoff": row["prediction_time"].isoformat(),
                "target_start": row["prediction_time"].isoformat(),
                "target_end": (row["prediction_time"] + pd.Timedelta(hours=1)).isoformat(),
                "last_weather_label": (row["prediction_time"] - pd.Timedelta(hours=1)).isoformat(),
                "lead_hours": (row["prediction_time"] - issued).total_seconds() / 3600,
                "score": float(row["score"]) if np.isfinite(row["score"]) else None,
                "status": "stale_weather" if snapshot.stale else ("experimental" if row["complete"] else "insufficient_weather"),
                "missing_features": missing, "feature_coverage": (24 - len(missing)) / 24,
                "current_outage_status": "unknown_no_live_feed", "assumption": "no_active_outage_at_target_cutoff",
                "weather_location_id": place["weather_location_id"], "cape_location_id": place["cape_location_id"],
                "weather_summary": {f: float(row[f]) if np.isfinite(row[f]) else None for f in
                    ["rainfall_24h_sum", "wind_gust_3h_max", "soil_moisture_24h_mean", "cape_3h_max"]},
            })
        return {"status": "stale_weather" if snapshot.stale else "experimental", "notice": NOTICE,
                "issued_at": issued.isoformat(), "model_id": model.manifest["model_id"],
                "hours": hours, "barangay_count": len(selected), "target_interval": "(t,t+1h]",
                "forecast_weather_validated": False, "automatic_alerts": False,
                "weather": {"provider": "Open-Meteo", "model": self.weather.model,
                    "snapshot_id": snapshot.snapshot_id, "retrieved_at": snapshot.retrieved_at.isoformat(),
                    "age_seconds": age, "stale": snapshot.stale, "refresh_failed": snapshot.refresh_failed,
                    "product": "latest forecast series including past model estimates; not station observations",
                    "run_initialized_at": None, "published_at": None,
                    "run_availability_status": "retrieval_verified_exact_run_not_exposed",
                    "source_shift": "ECMWF forecasts replace historical municipal weather and ERA5 CAPE",
                    "attribution": "Weather data by Open-Meteo and ECMWF", "locations": snapshot.locations},
                "points": points}


@lru_cache(maxsize=1)
def service():
    return ForecastService()


@router.get("/metadata")
def forecast_metadata():
    return service().metadata()


@router.get("/latest")
def forecast_latest(hours: int = Query(24, ge=1, le=48),
                    municipality_psgc: str | None = Query(None, pattern=r"^\d{10}$"),
                    barangay_psgc: str | None = Query(None, pattern=r"^\d{10}$")):
    try:
        return service().latest(hours, municipality_psgc, barangay_psgc)
    except (ModelUnavailable, WeatherUnavailable) as exc:
        raise HTTPException(503, str(exc)) from exc
