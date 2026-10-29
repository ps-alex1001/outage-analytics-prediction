"""Deterministic weather sensitivity experiments using the frozen forecast model."""
from __future__ import annotations

from typing import Literal
import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend.forecast import service
from backend.forecast_features import FEATURES, TZ, build_rows
from backend.forecast_model import ModelUnavailable, model_values
from backend.weather_forecast import WeatherUnavailable

router = APIRouter(prefix="/api/scenario", tags=["experimental weather scenarios"])


class Adjustments(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    precipitation: float = Field(0, ge=-50, le=50, description="Additive mm/h")
    wind_speed: float = Field(0, ge=-100, le=100, description="Additive km/h; converted to m/s")
    wind_gusts: float = Field(0, ge=-150, le=150, description="Additive km/h; converted to m/s")
    temperature: float = Field(0, ge=-15, le=15, description="Additive degrees Celsius")
    humidity: float = Field(0, ge=-50, le=50, description="Additive percentage points")
    soil_moisture: float = Field(0, ge=-0.4, le=0.4, description="Additive m³/m³, 7–28 cm")
    cape: float = Field(0, ge=-3000, le=3000, description="Additive J/kg")


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["constant", "storm"] = "constant"
    adjustments: Adjustments = Field(default_factory=Adjustments)


# Application experiment limits, not assertions about the model's training domain.
# Humidity, soil moisture and nonnegative variables follow the weather contract.
FIELDS = {
    "precipitation": ("precipitation", 1, 0, None, "mm/h"),
    "wind_speed": ("wind_speed_10m", 1 / 3.6, 0, None, "km/h"),
    "wind_gusts": ("wind_gusts_10m", 1 / 3.6, 0, None, "km/h"),
    "temperature": ("temperature_2m", 1, -90, 60, "°C"),
    "humidity": ("relative_humidity_2m", 1, 0, 100, "%"),
    "soil_moisture": ("soil_moisture_7_to_28cm", 1, 0, 1, "m³/m³"),
    "cape": ("cape", 1, 0, None, "J/kg"),
}


def intensity(mode):
    # Half-hour centers produce a smooth symmetric peak at hours 12 and 13.
    values = np.sin(np.pi * (np.arange(24) + .5) / 24) ** 2
    return np.ones(24) if mode == "constant" else values / values.max()


def adjust_frames(frames, first, request):
    profile = pd.Series(intensity(request.mode), index=pd.date_range(first, periods=24, freq="h"))
    changed, counts = {}, {}
    deltas = request.adjustments.model_dump()
    for identity, original in frames.items():
        f = original.copy(deep=True)
        weights = profile.reindex(f.index).fillna(0)
        eligible = weights > 0
        clipped = {}
        for key, (column, factor, lower, upper, _) in FIELDS.items():
            if deltas[key] == 0:
                continue
            proposed = f.loc[eligible, column] + deltas[key] * factor * weights.loc[eligible]
            bounded = proposed.clip(lower=lower, upper=upper)
            clipped[key] = int((proposed.notna() & (proposed != bounded)).sum())
            f.loc[eligible, column] = bounded
        # Only reconcile hypothetical wind changes; zero adjustments preserve even
        # pre-existing provider inconsistencies exactly. NaNs are never filled.
        if deltas["wind_speed"] or deltas["wind_gusts"]:
            reconcile = eligible & f.wind_speed_10m.notna() & f.wind_gusts_10m.notna()
            mask = reconcile & (f.wind_gusts_10m < f.wind_speed_10m)
            clipped["gust_raised_to_wind"] = int(mask.sum())
            f.loc[mask, "wind_gusts_10m"] = f.loc[mask, "wind_speed_10m"]
        changed[identity], counts[identity] = f, clipped
    return changed, counts


def simulate(request: ScenarioRequest, engine=None):
    engine = engine or service()
    model = engine.model()
    places, locations = engine.catalog()
    snapshot = engine.weather.get(locations)  # Exactly one shared snapshot.
    issued = pd.Timestamp(engine.clock()).tz_convert(TZ)
    age = (issued.to_pydatetime() - snapshot.retrieved_at).total_seconds()
    if snapshot.stale or age < 0 or age > 3600:
        raise WeatherUnavailable("A fresh validated weather snapshot is required for simulation")
    first = issued.ceil("h")
    cutoffs = pd.date_range(first, periods=24, freq="h")
    changed, clipping = adjust_frames(snapshot.frames, first, request)
    baseline = build_rows(places, snapshot.frames, cutoffs)
    scenario = build_rows(places, changed, cutoffs)
    scores, missing = [], []
    for matrix in (baseline, scenario):
        values = matrix[FEATURES].to_numpy("float32")
        complete = np.isfinite(values).all(axis=1)
        output = np.full(len(values), np.nan)
        if complete.any():
            output[complete] = model.predict(model_values(model, matrix.loc[complete]))
        scores.append(output)
        missing.append([[name for name in FEATURES if not np.isfinite(row[name])]
                        for row in matrix.to_dict("records")])

    def number(value):
        return float(value) if np.isfinite(value) else None

    lookup = {p["barangay_psgc"]: p for p in places}
    points = []
    for i, row in enumerate(baseline.to_dict("records")):
        place, cutoff = lookup[row["barangay_psgc"]], row["prediction_time"]
        b, s = scores[0][i], scores[1][i]
        points.append({**{k: place[k] for k in ("barangay_psgc", "barangay", "municipality_psgc", "municipality",
                                               "weather_location_id", "cape_location_id")},
            "prediction_cutoff": cutoff.isoformat(), "target_start": cutoff.isoformat(),
            "target_end": (cutoff + pd.Timedelta(hours=1)).isoformat(),
            "last_weather_label": (cutoff - pd.Timedelta(hours=1)).isoformat(),
            "lead_hours": (cutoff - issued).total_seconds() / 3600,
            "baseline_score": number(b), "scenario_score": number(s), "difference": number(s - b),
            "baseline_status": "insufficient_weather" if missing[0][i] else "experimental",
            "scenario_status": "insufficient_weather" if missing[1][i] else "experimental",
            "baseline_missing_features": missing[0][i], "scenario_missing_features": missing[1][i],
            "assumption": "no_active_outage_at_target_cutoff", "current_outage_status": "unknown_no_live_feed"})
    weather = []
    for identity, original in snapshot.frames.items():
        # Aligned with the last weather label used at each displayed cutoff.
        for cutoff in cutoffs:
            label = cutoff - pd.Timedelta(hours=1)
            b = original.reindex([label]).iloc[0]
            s = changed[identity].reindex([label]).iloc[0]
            weather.append({"location_id": identity, "prediction_cutoff": cutoff.isoformat(),
                "weather_label": label.isoformat(),
                "baseline": {key: number(b[col] / factor) for key, (col, factor, *_) in FIELDS.items()},
                "scenario": {key: number(s[col] / factor) for key, (col, factor, *_) in FIELDS.items()}})
    return {"status": "experimental", "issued_at": issued.isoformat(), "model_id": model.manifest["model_id"],
        "hours": 24, "barangay_count": len(places), "timezone": TZ, "target_interval": "(t,t+1h]",
        "mode": request.mode, "adjustments": request.adjustments.model_dump(), "points": points,
        "weather_hourly": weather, "weather_units": {key: spec[-1] for key, spec in FIELDS.items()},
        "intensity_profile": intensity(request.mode).tolist(), "adjustment_start": first.isoformat(),
        "clipping_counts": clipping, "weather": {"snapshot_id": snapshot.snapshot_id,
            "retrieved_at": snapshot.retrieved_at.isoformat(), "age_seconds": age, "stale": False,
            "provider": "Open-Meteo", "model": engine.weather.model, "locations": snapshot.locations,
            "run_initialized_at": None, "published_at": None,
            "attribution": "Weather data by Open-Meteo and ECMWF"},
        "notice": "Experimental synthetic-outage onset model scores. Differences show model sensitivity, not causal effects or validated operational outage probabilities.",
        "timing_note": "Only source labels at or after adjustment_start are modified. The first interval is unchanged because its inputs precede that boundary. Weather charts show the last input hour, not weather inside the target interval.",
        "forecast_weather_validated": False, "automatic_alerts": False}


@router.post("/simulate")
def simulate_scenario(request: ScenarioRequest):
    try:
        return simulate(request)
    except (ModelUnavailable, WeatherUnavailable) as exc:
        raise HTTPException(503, str(exc)) from exc
