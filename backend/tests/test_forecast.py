"""Feature parity, source failure handling, native inference and API contracts."""
import asyncio
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
from fastapi import FastAPI

from backend import forecast
from backend.forecast_features import FEATURES, HOURLY_VARIABLES, build_rows, weather_features
from backend.forecast_model import ForecastModel, ModelUnavailable
from backend.weather_forecast import EXPECTED_UNITS, OpenMeteoClient, WeatherSnapshot, WeatherUnavailable, decode_snapshot

NOW = datetime(2026, 10, 27, 12, 15, tzinfo=timezone.utc)


def frame():
    index = pd.date_range(pd.Timestamp(NOW).floor("h") - pd.Timedelta(hours=74), periods=125, freq="h")
    return pd.DataFrame({"precipitation": np.zeros(len(index)), "temperature_2m": 28.,
        "relative_humidity_2m": 75., "wind_speed_10m": 3., "wind_gusts_10m": 5.,
        "soil_moisture_7_to_28cm": .25, "cape": 500.}, index=index)


def catalog():
    return [{"barangay_psgc": "0300801001", "barangay": "Bangkal",
             "municipality_psgc": "0300801000", "municipality": "Abucay",
             "weather_location_id": "weather:a", "cape_location_id": "cape:a",
             "mean_slope_deg": 12.22, "mean_elevation_m": 301.6,
             "elevation_relief_p95_p05_m": 183.6}], [
        {"latitude": 14.797891, "longitude": 120.5028, "ids": ["weather:a", "cape:a"]}]


def record():
    f = frame()
    return {"retrieved_at": NOW.isoformat(), "snapshot_id": "test-snapshot", "response": {
        "utc_offset_seconds": 0, "latitude": 14.8, "longitude": 120.5,
        "hourly_units": {"time": "iso8601", **EXPECTED_UNITS},
        "hourly": {"time": f.index.strftime("%Y-%m-%dT%H:%M").tolist(),
                   **{name: f[name].tolist() for name in HOURLY_VARIABLES}}}}


class FeatureTests(unittest.TestCase):
    def test_v2_builder_parity_including_gaps_and_streaks(self):
        from scripts.build_ml_dataset_v2 import engineer_weather
        f = frame()
        rng = np.random.default_rng(17)
        f.precipitation = rng.choice([0., .1, .2, 2.], len(f))
        f.wind_gusts_10m = rng.uniform(0, 20, len(f))
        f.iloc[30, f.columns.get_loc("precipitation")] = np.nan
        original = f.rename(columns={"precipitation": "precipitation_mm", "wind_speed_10m": "wind_speed_10m_m_s",
            "wind_gusts_10m": "wind_gusts_10m_m_s", "temperature_2m": "temperature_2m_c",
            "relative_humidity_2m": "relative_humidity_2m_pct", "soil_moisture_7_to_28cm": "soil_moisture_7_to_28cm_m3_m3"})
        original["soil_moisture_0_to_7cm_m3_m3"] = .2
        original["surface_pressure_hpa"] = 1000.
        expected = engineer_weather(original)
        actual = weather_features(f)
        for name in actual:
            np.testing.assert_array_equal(actual[name].to_numpy(), expected[name].to_numpy())
        self.assertTrue(actual.index.equals(expected.index.tz_convert("Asia/Manila")))

    def test_full_row_matches_saved_v2_builder_contract(self):
        from scripts.build_ml_dataset_v2 import calendar_features, engineer_cape
        f = frame()
        f.loc[f.index[-10]:, "precipitation"] = 1.
        cutoffs = f.index[-5:].tz_convert("Asia/Manila")
        result = build_rows(catalog()[0], {"weather:a": f, "cape:a": f}, cutoffs)
        self.assertEqual(list(result[FEATURES]), FEATURES)
        cal = calendar_features(cutoffs)
        for name in cal:
            np.testing.assert_array_equal(result[name].to_numpy(), cal[name].to_numpy())
        cape = engineer_cape(f.cape).reindex(cutoffs).cape_3h_max
        np.testing.assert_array_equal(result.cape_3h_max, cape)
        np.testing.assert_allclose(result.rainfall_x_slope, result.rainfall_24h_sum * np.float32(12.22))

    def test_future_values_cannot_change_an_earlier_cutoff(self):
        f = frame(); t = pd.Timestamp(NOW).floor("h")
        before = weather_features(f).loc[t].copy()
        f.loc[t:, "precipitation"] = 9999.
        f.loc[t:, "wind_gusts_10m"] = 9999.
        pd.testing.assert_series_equal(before, weather_features(f).loc[t])

    def test_missing_hour_is_not_zero_and_does_not_cross_location(self):
        f = frame(); t = f.index[-1] + pd.Timedelta(hours=1)
        f = f.drop(f.index[-12])
        self.assertTrue(np.isnan(weather_features(f).loc[t, "rainfall_24h_sum"]))
        self.assertEqual(weather_features(frame()).loc[t, "rainfall_24h_sum"], 0)

    def test_streak_boundary_and_cap(self):
        f = frame(); f.precipitation = 1.
        result = weather_features(f)
        self.assertTrue(np.isnan(result.consecutive_rainy_hours.iloc[70]))
        self.assertEqual(result.consecutive_rainy_hours.iloc[71], 72)
        f.iloc[0, f.columns.get_loc("precipitation")] = .1
        self.assertEqual(weather_features(f).consecutive_rainy_hours.iloc[1], 1)

    def test_duplicate_and_naive_times_are_rejected(self):
        for f in [pd.concat([frame(), frame().iloc[:1]]), frame().tz_localize(None)]:
            with self.assertRaises(ValueError): weather_features(f)


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.now = NOW
        self.calls = []

    def opener(self, request, timeout):
        self.calls.append(request)
        return io.BytesIO(json.dumps(record()["response"]).encode())

    def client(self):
        return OpenMeteoClient(self.directory.name, opener=self.opener, clock=lambda: self.now)

    def test_cache_and_request_units(self):
        client = self.client(); locations = catalog()[1]
        first = client.get(locations); second = client.get(locations)
        self.assertEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(len(self.calls), 1)
        query = parse_qs(urlparse(self.calls[0].full_url).query)
        self.assertEqual(query["wind_speed_unit"], ["ms"])
        self.assertEqual(query["past_hours"], ["74"])
        self.assertEqual(query["models"], ["ecmwf_ifs025"])
        self.assertTrue(first.frames["weather:a"].equals(first.frames["cape:a"]))
        self.assertEqual(len(list(Path(self.directory.name).glob("*/*.json"))), 2)
        # A new client can use the validated disk cache without a network call.
        self.client().get(locations)
        self.assertEqual(len(self.calls), 1)

    def test_stale_cache_is_marked_and_expired_cache_fails(self):
        client = self.client(); client.get(catalog()[1])
        client.opener = lambda *a, **k: (_ for _ in ()).throw(URLError("offline"))
        self.now += timedelta(minutes=31)
        stale = client.get(catalog()[1]); self.assertTrue(stale.stale and stale.refresh_failed)
        self.now += timedelta(hours=1)
        with self.assertRaises(WeatherUnavailable): client.get(catalog()[1])

    def test_units_duplicates_and_wrong_location_count_fail(self):
        for problem in ("units", "duplicate", "count"):
            r = record()
            if problem == "units": r["response"]["hourly_units"]["wind_speed_10m"] = "km/h"
            if problem == "duplicate": r["response"]["hourly"]["time"][1] = r["response"]["hourly"]["time"][0]
            if problem == "count": r["response"] = [r["response"], r["response"]]
            with self.assertRaises(ValueError): decode_snapshot(r, catalog()[1])

    def test_nulls_preserved_and_bad_ranges_rejected(self):
        r = record(); r["response"]["hourly"]["precipitation"][4] = None
        self.assertTrue(np.isnan(decode_snapshot(r, catalog()[1]).frames["weather:a"].precipitation.iloc[4]))
        r["response"]["hourly"]["relative_humidity_2m"][2] = 101
        with self.assertRaises(ValueError): decode_snapshot(r, catalog()[1])

    def test_negative_cape_is_missing_not_zero_and_is_reported(self):
        r = record(); r["response"]["hourly"]["cape"][4] = -20.
        snapshot = decode_snapshot(r, catalog()[1])
        self.assertTrue(np.isnan(snapshot.frames["cape:a"].cape.iloc[4]))
        self.assertEqual(snapshot.locations[0]["invalid_value_counts"], {"cape": 1})
        self.assertEqual(r["response"]["hourly"]["cape"][4], -20.)


class FakeModel:
    manifest = {"model_id": "test-model", "training_rows": 10368667}
    parity_rows = 153
    def predict(self, values): return np.full(len(values), .002, dtype="float32")


class FakeWeather:
    model = "ecmwf_ifs025"
    stale = False
    def get(self, locations):
        return WeatherSnapshot(NOW, "fake", {"weather:a": frame(), "cape:a": frame()}, [], stale=self.stale)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.weather = FakeWeather()
        self.service = forecast.ForecastService(weather=self.weather, clock=lambda: NOW, catalog=catalog)
        self.service._model = FakeModel()

    def test_full_future_intervals_scores_and_no_operational_alert(self):
        result = self.service.latest(24)
        self.assertEqual(len(result["points"]), 24)
        self.assertFalse(result["automatic_alerts"])
        first = result["points"][0]
        self.assertEqual(first["prediction_cutoff"], "2026-10-27T21:00:00+08:00")
        self.assertEqual(first["last_weather_label"], "2026-10-27T20:00:00+08:00")
        self.assertEqual(first["target_end"], "2026-10-27T22:00:00+08:00")
        self.assertEqual(first["status"], "experimental")
        self.assertAlmostEqual(first["score"], .002)
        self.assertNotIn("probability", first)
        self.assertNotIn("alert", first)
        self.assertIsNone(result["weather"]["run_initialized_at"])
        self.assertEqual(first["current_outage_status"], "unknown_no_live_feed")

    def test_stale_weather_suppresses_scores(self):
        self.weather.stale = True
        result = self.service.latest(1)
        self.assertEqual(result["points"][0]["status"], "stale_weather")
        self.assertIsNone(result["points"][0]["score"])

    def test_missing_required_window_suppresses_score(self):
        snapshot = self.weather.get([])
        snapshot.frames["weather:a"].loc[pd.Timestamp(NOW).floor("h"), "precipitation"] = np.nan
        self.weather.get = lambda _: snapshot
        first = self.service.latest(1)["points"][0]
        self.assertEqual(first["status"], "insufficient_weather")
        self.assertIsNone(first["score"])
        self.assertIn("rainfall_mm_lag1h", first["missing_features"])

    def test_unknown_geographic_id_returns_404(self):
        with self.assertRaises(forecast.HTTPException) as caught:
            self.service.latest(1, "9999999999")
        self.assertEqual(caught.exception.status_code, 404)

    def test_readiness_does_not_fetch_weather(self):
        with patch.object(self.weather, "get", side_effect=AssertionError("network used")):
            self.assertEqual(self.service.metadata()["status"], "ready")

    def test_missing_model_reports_unavailable(self):
        with TemporaryDirectory() as directory:
            service = forecast.ForecastService(model_dir=directory, weather=self.weather, catalog=catalog)
            self.assertEqual(service.metadata()["status"], "unavailable")
            with self.assertRaises(ModelUnavailable): service.latest(1)

    def test_api_validation_and_service_errors(self):
        app = FastAPI(); app.include_router(forecast.router)
        async def request(query):
            messages = []
            async def send(message): messages.append(message)
            async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
            await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
                "scheme": "http", "path": "/api/forecast/latest", "query_string": query.encode(),
                "headers": [], "server": ("test", 80), "client": ("test", 1), "root_path": ""}, receive, send)
            return messages[0]["status"], json.loads(b"".join(m.get("body", b"") for m in messages[1:]))
        with patch.object(forecast, "service", return_value=self.service):
            self.assertEqual(asyncio.run(request("hours=0"))[0], 422)
            self.assertEqual(asyncio.run(request("municipality_psgc=bad"))[0], 422)
            status, body = asyncio.run(request("hours=1"))
            self.assertEqual(status, 200); self.assertEqual(len(body["points"]), 1)
            with patch.object(self.service, "model", side_effect=ModelUnavailable("missing model")):
                self.assertEqual(asyncio.run(request("hours=1"))[0], 503)


@unittest.skipUnless((forecast.DATA_ROOT / "models/outage_v2_xgboost/manifest.json").is_file(), "Install the evaluated native model export")
class InstalledModelTests(unittest.TestCase):
    def test_native_predictions_match_colab_and_tampering_fails(self):
        directory = forecast.DATA_ROOT / "models/outage_v2_xgboost"
        model = ForecastModel(directory)
        self.assertEqual(model.parity_rows, 153)
        self.assertEqual(model.manifest["training_rows"], 10368667)
        with TemporaryDirectory() as tmp:
            for name in ("manifest.json", "model.ubj", "parity.json"):
                (Path(tmp) / name).write_bytes((directory / name).read_bytes())
            with (Path(tmp) / "model.ubj").open("ab") as f: f.write(b"tampered")
            with self.assertRaises(ModelUnavailable): ForecastModel(tmp)

    def test_real_catalog_mapping_is_complete(self):
        places, locations = forecast.geography()
        self.assertEqual(len(places), 237)
        self.assertEqual(len(locations), 11)
        self.assertEqual(len({p["weather_location_id"] for p in places}), 7)
        self.assertEqual(len({p["cape_location_id"] for p in places}), 4)


if __name__ == "__main__":
    unittest.main()
