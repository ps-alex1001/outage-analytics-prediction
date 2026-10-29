"""Focused scenario identity, chronology, missingness and request contracts."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
from pydantic import ValidationError
from fastapi import FastAPI, HTTPException

from backend.scenario import Adjustments, ScenarioRequest, adjust_frames, intensity, router, simulate, simulate_scenario
from backend.tests.test_forecast import NOW, catalog, frame
from backend.weather_forecast import WeatherSnapshot, WeatherUnavailable


class ScenarioTests(unittest.TestCase):
    def engine(self, f=None, stale=False):
        f = frame() if f is None else f
        snapshot = WeatherSnapshot(NOW, "same-snapshot", {"weather:a": f, "cape:a": f}, [], stale)
        model = SimpleNamespace(manifest={"model_id": "frozen-test-model"},
                                predict=lambda x: np.clip(x[:, 0] / 100, 0, 1))
        return SimpleNamespace(model=lambda: model, catalog=catalog, clock=lambda: NOW,
            weather=SimpleNamespace(get=Mock(return_value=snapshot), model="ecmwf_ifs025"))

    def test_zero_adjustments_identity_and_single_snapshot(self):
        engine = self.engine()
        for mode in ("constant", "storm"):
            result = simulate(ScenarioRequest(mode=mode), engine)
            self.assertEqual(len(result["points"]), 24)
            self.assertTrue(all(p["difference"] == 0 for p in result["points"]))
            self.assertTrue(all(w["baseline"] == w["scenario"] for w in result["weather_hourly"]))
            self.assertEqual(result["weather"]["snapshot_id"], "same-snapshot")
        self.assertEqual(engine.weather.get.call_count, 2)

    def test_history_immutable_geography_offsets_and_unit_conversion(self):
        f = frame(); other = f.copy(); other.precipitation += 2
        first = pd.Timestamp(NOW).ceil("h")
        frames = {"weather:a": f, "weather:b": other}
        changed, _ = adjust_frames(frames, first, ScenarioRequest(adjustments=Adjustments(precipitation=4, wind_speed=36)))
        pd.testing.assert_frame_equal(changed["weather:a"].loc[:first - pd.Timedelta(hours=1)], f.loc[:first - pd.Timedelta(hours=1)])
        self.assertEqual(changed["weather:a"].loc[first, "wind_speed_10m"], 13)
        self.assertEqual(changed["weather:a"].loc[first, "wind_gusts_10m"], 13)
        self.assertEqual(changed["weather:b"].loc[first, "precipitation"] - changed["weather:a"].loc[first, "precipitation"], 2)
        self.assertEqual(f.loc[first, "precipitation"], 0)

    def test_cutoff_alignment_and_rolling_recalculation(self):
        result = simulate(ScenarioRequest(adjustments=Adjustments(precipitation=5)), self.engine())
        a, b = result["points"][:2]
        self.assertEqual(a["difference"], 0)
        self.assertGreater(b["difference"], 0)
        self.assertEqual(pd.Timestamp(b["last_weather_label"]), pd.Timestamp(a["prediction_cutoff"]))
        self.assertEqual(pd.Timestamp(a["target_end"]) - pd.Timestamp(a["target_start"]), pd.Timedelta(hours=1))
        self.assertEqual(result["weather_hourly"][0]["scenario"]["precipitation"], 0)
        self.assertEqual(result["weather_hourly"][1]["scenario"]["precipitation"], 5)

    def test_missing_weather_never_imputed(self):
        f = frame(); first = pd.Timestamp(NOW).ceil("h")
        f.loc[first, "precipitation"] = np.nan
        result = simulate(ScenarioRequest(adjustments=Adjustments(precipitation=5)), self.engine(f))
        p = result["points"][1]
        self.assertIsNone(p["baseline_score"])
        self.assertIsNone(p["scenario_score"])
        self.assertIsNone(p["difference"])
        self.assertIn("rainfall_24h_sum", p["scenario_missing_features"])

    def test_clipping_and_nulls(self):
        f = frame(); first = pd.Timestamp(NOW).ceil("h")
        f.loc[first, "cape"] = np.nan
        adjusted, counts = adjust_frames({"weather:a": f}, first, ScenarioRequest(adjustments=Adjustments(precipitation=-50, humidity=50, soil_moisture=-.4, cape=3000)))
        row = adjusted["weather:a"].loc[first]
        self.assertEqual(row.relative_humidity_2m, 100)
        self.assertEqual(row.soil_moisture_7_to_28cm, 0)
        self.assertTrue(np.isnan(row.cape))
        self.assertGreater(counts["weather:a"]["humidity"], 0)

    def test_stale_is_rejected_without_prediction(self):
        engine = self.engine(stale=True)
        with self.assertRaises(WeatherUnavailable): simulate(ScenarioRequest(), engine)
        with patch("backend.scenario.simulate", side_effect=WeatherUnavailable("stale")):
            with self.assertRaises(HTTPException) as caught: simulate_scenario(ScenarioRequest())
            self.assertEqual(caught.exception.status_code, 503)

    def test_deterministic_storm_and_validation(self):
        p = intensity("storm")
        np.testing.assert_array_equal(p, intensity("storm"))
        np.testing.assert_allclose(p, p[::-1])
        self.assertEqual(p.max(), 1)
        self.assertGreater(p[11], p[0])
        for payload in ({"humidity": 51}, {"temperature": float("nan")}, {"cape": float("inf")}, {"extra": 1}):
            with self.assertRaises(ValidationError): Adjustments(**payload)
        with self.assertRaises(ValidationError): ScenarioRequest(mode="random")

    def test_post_contract_and_validation(self):
        app = FastAPI(); app.include_router(router)

        async def request(payload):
            messages = []
            async def send(message): messages.append(message)
            async def receive():
                return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}
            await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                "scheme": "http", "path": "/api/scenario/simulate", "query_string": b"",
                "headers": [(b"content-type", b"application/json")], "server": ("test", 80),
                "client": ("test", 1), "root_path": ""}, receive, send)
            return messages[0]["status"], json.loads(b"".join(m.get("body", b"") for m in messages[1:]))

        with patch("backend.scenario.service", return_value=self.engine()):
            status, body = asyncio.run(request({"mode": "storm", "adjustments": {"precipitation": 5}}))
            self.assertEqual(status, 200)
            self.assertEqual(body["points"][0]["barangay_psgc"], "0300801001")
            self.assertEqual(body["weather_units"]["wind_speed"], "km/h")
            self.assertEqual(asyncio.run(request({"adjustments": {"humidity": 51}}))[0], 422)
            self.assertEqual(asyncio.run(request({"mode": "random"}))[0], 422)

    def test_installed_model_and_full_province_zero_identity(self):
        from backend.forecast import ForecastService, geography
        from backend.forecast_model import ModelUnavailable
        engine = ForecastService(clock=lambda: NOW)
        try:
            engine.model()
        except ModelUnavailable:
            self.skipTest("Native model export unavailable")
        places, _ = geography()
        frames = {p[key]: frame() for p in places for key in ("weather_location_id", "cape_location_id")}
        engine.weather = SimpleNamespace(model="ecmwf_ifs025", get=Mock(return_value=WeatherSnapshot(NOW, "full-province", frames, [])))
        result = simulate(ScenarioRequest(), engine)
        self.assertEqual(len(result["points"]), 237 * 24)
        self.assertEqual(len({p["barangay_psgc"] for p in result["points"]}), 237)
        self.assertTrue(all(p["difference"] == 0 for p in result["points"]))
        self.assertEqual(engine.weather.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
