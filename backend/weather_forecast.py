"""Pinned Open-Meteo snapshots with bounded HTTP reads and an on-disk archive."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from backend.forecast_features import HOURLY_VARIABLES

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
EXPECTED_UNITS = {
    "temperature_2m": "°C", "relative_humidity_2m": "%", "precipitation": "mm",
    "wind_speed_10m": "m/s", "wind_gusts_10m": "m/s",
    "soil_moisture_7_to_28cm": "m³/m³", "cape": "J/kg",
}


class WeatherUnavailable(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class WeatherSnapshot:
    retrieved_at: datetime
    snapshot_id: str
    frames: dict
    locations: list
    stale: bool = False
    refresh_failed: bool = False


def decode_snapshot(record, locations):
    payload = record["response"]
    if not isinstance(payload, list):
        payload = [payload]
    if len(payload) != len(locations):
        raise ValueError("Weather response has the wrong location count")
    retrieved = datetime.fromisoformat(record["retrieved_at"])
    if retrieved.tzinfo is None:
        raise ValueError("Weather retrieval time has no timezone")
    frames, provenance = {}, []
    for location, response in zip(locations, payload):
        if response.get("error") or response.get("utc_offset_seconds") != 0:
            raise ValueError("Weather API error or unexpected timezone")
        hourly, units = response["hourly"], response["hourly_units"]
        if units.get("time") != "iso8601":
            raise ValueError("Unexpected weather timestamp format")
        index = pd.DatetimeIndex(pd.to_datetime(hourly["time"], utc=True))
        if index.empty or index.hasnans or not index.is_unique or not index.is_monotonic_increasing:
            raise ValueError("Weather times are empty, invalid, duplicated or unordered")
        if not index.equals(index.floor("h")):
            raise ValueError("Weather times are not hourly aligned")
        values, invalid_counts = {}, {}
        for name in HOURLY_VARIABLES:
            if units.get(name) != EXPECTED_UNITS[name] or len(hourly[name]) != len(index):
                raise ValueError(f"Missing/incompatible weather field: {name}")
            array = np.asarray(hourly[name], dtype="float64")
            if np.isinf(array).any():
                raise ValueError(f"Infinite weather values: {name}")
            if name == "cape":
                # Some source hours contain negative CAPE. Preserve them in the
                # raw archive, mark them missing for features, and never clip to zero.
                invalid = np.isfinite(array) & (array < 0)
                invalid_counts[name] = int(invalid.sum())
                array[invalid] = np.nan
            finite = array[np.isfinite(array)]
            if name != "temperature_2m" and (finite < 0).any():
                raise ValueError(f"Negative weather values: {name}")
            upper = {"relative_humidity_2m": 100, "soil_moisture_7_to_28cm": 1}.get(name)
            if upper is not None and (finite > upper).any():
                raise ValueError(f"Invalid weather range: {name}")
            values[name] = array
        frame = pd.DataFrame(values, index=index)
        for identity in location["ids"]:
            frames[identity] = frame
        provenance.append({
            "location_ids": location["ids"],
            "requested_latitude": location["latitude"], "requested_longitude": location["longitude"],
            "returned_latitude": response.get("latitude"), "returned_longitude": response.get("longitude"),
            "gust_below_wind_hours": int((frame.wind_gusts_10m < frame.wind_speed_10m).sum()),
            "invalid_value_counts": invalid_counts,
        })
    return WeatherSnapshot(retrieved, record["snapshot_id"], frames, provenance)


class OpenMeteoClient:
    def __init__(self, cache_dir, model="ecmwf_ifs025", ttl_seconds=1800,
                 max_age_seconds=3600, opener=urlopen, clock=utc_now):
        self.cache_dir = Path(cache_dir)
        self.model = model
        self.ttl = ttl_seconds
        self.max_age = max_age_seconds
        self.opener, self.clock = opener, clock
        self._lock = Lock()
        self._memory = {}
        self._last_failure = {}

    def get(self, locations):
        parameters = {
            "latitude": ",".join(str(p["latitude"]) for p in locations),
            "longitude": ",".join(str(p["longitude"]) for p in locations),
            "hourly": ",".join(HOURLY_VARIABLES), "models": self.model,
            "timezone": "UTC", "timeformat": "iso8601", "wind_speed_unit": "ms",
            "temperature_unit": "celsius", "precipitation_unit": "mm",
            "past_hours": 74, "forecast_hours": 49,
        }
        key = hashlib.sha256(json.dumps([parameters, locations], sort_keys=True).encode()).hexdigest()
        with self._lock:
            now = self.clock()
            directory = self.cache_dir / key
            cached = self._memory.get(key)
            if cached is None:
                try:
                    record = json.loads((directory / "latest.json").read_text(encoding="utf-8"))
                    cached = decode_snapshot(record, locations)
                except (OSError, ValueError, KeyError, TypeError):
                    cached = None
            age = (now - cached.retrieved_at).total_seconds() if cached else math.inf
            if cached and 0 <= age <= self.ttl:
                return cached
            failed_at = self._last_failure.get(key)
            try:
                if failed_at is not None and (now - failed_at).total_seconds() < 60:
                    raise WeatherUnavailable("Weather refresh is temporarily cooling down")
                request = Request(ENDPOINT + "?" + urlencode(parameters), headers={
                    "User-Agent": "PENELCO-Synthetic-Research/1.0", "Accept": "application/json",
                })
                with self.opener(request, timeout=20) as response:
                    raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise ValueError("Weather response exceeds size limit")
                payload = json.loads(raw)
                retrieved = self.clock()
                record = {"retrieved_at": retrieved.isoformat(), "parameters": parameters,
                          "snapshot_id": hashlib.sha256(raw + retrieved.isoformat().encode()).hexdigest(),
                          "response": payload}
                snapshot = decode_snapshot(record, locations)
                directory.mkdir(parents=True, exist_ok=True)
                body = json.dumps(record, allow_nan=False)
                archive = directory / (retrieved.strftime("%Y%m%dT%H%M%S%fZ") + ".json")
                archive.write_text(body, encoding="utf-8")
                temporary = directory / "latest.partial.json"
                temporary.write_text(body, encoding="utf-8")
                os.replace(temporary, directory / "latest.json")
                self._memory[key] = snapshot
                self._last_failure.pop(key, None)
                return snapshot
            except (OSError, HTTPError, URLError, ValueError, KeyError, TypeError, WeatherUnavailable) as exc:
                self._last_failure[key] = now
                if cached and 0 <= age <= self.max_age:
                    return WeatherSnapshot(cached.retrieved_at, cached.snapshot_id, cached.frames,
                                           cached.locations, stale=True, refresh_failed=True)
                raise WeatherUnavailable("Weather forecast is unavailable; no fresh usable snapshot") from exc
