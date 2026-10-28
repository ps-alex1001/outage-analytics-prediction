"""The evaluated V2 predictor contract, independent of training/data-building tools."""
from __future__ import annotations

import numpy as np
import pandas as pd

TZ = "Asia/Manila"
FEATURES = [
    "rainfall_mm_lag1h", "wind_speed_m_s_lag1h", "wind_gust_m_s_lag1h",
    "temperature_c_lag1h", "humidity_pct_lag1h", "mean_slope_deg",
    "elevation_relief_p95_p05_m", "mean_elevation_m", "rainfall_3h_sum",
    "rainfall_6h_sum", "rainfall_24h_sum", "rainfall_24h_max",
    "consecutive_rainy_hours", "wind_gust_3h_max", "wind_speed_3h_mean",
    "wind_gust_change_3h", "soil_moisture_24h_mean", "cape_3h_max",
    "rainfall_x_slope", "hour_sin", "hour_cos", "month_sin", "month_cos", "is_weekend",
]
TERRAIN = ["mean_slope_deg", "elevation_relief_p95_p05_m", "mean_elevation_m"]
RAW_TO_FEATURE = {
    "precipitation": "rainfall_mm_lag1h", "wind_speed_10m": "wind_speed_m_s_lag1h",
    "wind_gusts_10m": "wind_gust_m_s_lag1h", "temperature_2m": "temperature_c_lag1h",
    "relative_humidity_2m": "humidity_pct_lag1h",
}
HOURLY_VARIABLES = [*RAW_TO_FEATURE, "soil_moisture_7_to_28cm", "cape"]


def hourly_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("Weather timestamps must be timezone-aware")
    if frame.empty or frame.index.hasnans or not frame.index.is_unique:
        raise ValueError("Weather timestamps are empty, null or duplicated")
    f = frame.copy()
    f.index = f.index.tz_convert(TZ)
    if not f.index.equals(f.index.floor("h")):
        raise ValueError("Weather timestamps must be hourly aligned")
    f = f.sort_index()
    return f.reindex(pd.date_range(f.index.min(), f.index.max(), freq="h"))


def weather_features(frame: pd.DataFrame) -> pd.DataFrame:
    """At cutoff t use complete source labels t-W hours through t-1 hour."""
    f = hourly_frame(frame)
    result = f[list(RAW_TO_FEATURE)].rename(columns=RAW_TO_FEATURE).copy()
    rain, gust = f.precipitation, f.wind_gusts_10m
    for window in (3, 6, 24):
        result[f"rainfall_{window}h_sum"] = rain.rolling(window, min_periods=window).sum()
    result["rainfall_24h_max"] = rain.rolling(24, min_periods=24).max()
    streak, count, known = [], 0, False
    for value in rain:
        if not np.isfinite(value):
            count, known = 0, False
            streak.append(np.nan)
        elif value <= 0.1:
            count, known = 0, True
            streak.append(0)
        else:
            count = min(count + 1, 72)
            streak.append(count if known or count == 72 else np.nan)
    result["consecutive_rainy_hours"] = streak
    result["wind_gust_3h_max"] = gust.rolling(3, min_periods=3).max()
    result["wind_speed_3h_mean"] = f.wind_speed_10m.rolling(3, min_periods=3).mean()
    result["wind_gust_change_3h"] = gust - gust.shift(3)
    result["soil_moisture_24h_mean"] = f.soil_moisture_7_to_28cm.rolling(24, min_periods=24).mean()
    result.index += pd.Timedelta(hours=1)
    return result.astype("float32")


def cape_features(frame: pd.DataFrame) -> pd.Series:
    values = hourly_frame(frame).cape.rolling(3, min_periods=3).max().astype("float32")
    values.index += pd.Timedelta(hours=1)
    return values


def build_rows(geography, frames, cutoffs):
    """Join each barangay to its original municipal point and ERA5-cell location."""
    weather = {key: weather_features(value).reindex(cutoffs)
               for key, value in frames.items() if key.startswith("weather:")}
    cape = {key: cape_features(value).reindex(cutoffs)
            for key, value in frames.items() if key.startswith("cape:")}
    rows = []
    for place in geography:
        f = weather[place["weather_location_id"]].copy()
        f["cape_3h_max"] = cape[place["cape_location_id"]]
        for name in TERRAIN:
            f[name] = np.float32(place[name])
        f["rainfall_x_slope"] = (f.rainfall_24h_sum * f.mean_slope_deg).astype("float32")
        f["hour_sin"] = np.sin(2 * np.pi * cutoffs.hour / 24)
        f["hour_cos"] = np.cos(2 * np.pi * cutoffs.hour / 24)
        f["month_sin"] = np.sin(2 * np.pi * (cutoffs.month - 1) / 12)
        f["month_cos"] = np.cos(2 * np.pi * (cutoffs.month - 1) / 12)
        f["is_weekend"] = (cutoffs.dayofweek >= 5).astype("float32")
        f = f[FEATURES].astype("float32")
        f["prediction_time"] = cutoffs
        f["barangay_psgc"] = place["barangay_psgc"]
        rows.append(f)
    return pd.concat(rows, ignore_index=True)
