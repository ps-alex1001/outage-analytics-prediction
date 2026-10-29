"""Scoped checks for local storm selection, historical timing and missing weather."""
import json
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException

from backend import storm_replay as replay
from backend.forecast_features import TZ
from backend.scenario import ScenarioRequest, simulate
from backend.tests.test_forecast import NOW, catalog, frame
from backend.tests import test_scenario as scenario_tests


class ReplayTests(unittest.TestCase):
    def event(self):
        first = pd.Timestamp(NOW).ceil("h").tz_convert(TZ)
        return {"id": "test", "window_start": first.isoformat(), "title": "test"}

    def test_only_2025_2026_and_original_ids_no_invented_names(self):
        data = replay.tracks()
        self.assertTrue(set(data.time.dt.year).issubset({2025, 2026}))
        events = replay.events()
        self.assertTrue(events)
        self.assertTrue(all(e["name"] is None and e["title"] == f'Storm {e["id"]}' for e in events))
        self.assertTrue(any(not e["available"] for e in events))
        self.assertEqual(len({e["id"] for e in events}), len(events))
        with self.assertRaises(HTTPException) as caught: replay.historical_replay("not-an-id")
        self.assertEqual(caught.exception.status_code, 404)
        missing = next(e for e in events if not e["available"])
        with self.assertRaises(HTTPException) as caught: replay.historical_replay(missing["id"])
        self.assertEqual(caught.exception.status_code, 422)

    def test_historical_cutoffs_missingness_and_unit_conversion(self):
        f = frame(); first = pd.Timestamp(self.event()["window_start"])
        f.loc[first, "precipitation"] = np.nan
        track = pd.DataFrame(columns=["time"])
        model = SimpleNamespace(manifest={"model_id": "test"}, predict=lambda x: np.full(len(x), .003))
        places = catalog()[0]
        for p in places: p.update(latitude=14.8, longitude=120.5)
        result = replay.build_replay(self.event(), track, places, {"weather:a": f, "cape:a": f}, model)
        a, b = result["points"][:2]
        self.assertEqual(a["score"], .003)
        self.assertIsNone(b["score"])
        self.assertEqual(b["status"], "insufficient_weather")
        self.assertIn("rainfall_24h_sum", b["missing_features"])
        self.assertEqual(pd.Timestamp(a["target_start"])-pd.Timestamp(a["last_weather_label"]), pd.Timedelta(hours=1))
        self.assertEqual(pd.Timestamp(a["target_end"])-pd.Timestamp(a["target_start"]), pd.Timedelta(hours=1))
        self.assertAlmostEqual(result["weather_hourly"][0]["wind"], 10.8)
        self.assertIsNone(result["weather_hourly"][1]["rainfall"])
        self.assertEqual(a["barangay_psgc"], "0300801001")

    def test_forecasted_uses_scenario_predictions_and_is_explicitly_synthetic(self):
        engine = scenario_tests.ScenarioTests().engine()
        batch = simulate(ScenarioRequest(), engine)
        batch["points"][0]["scenario_score"] = .07
        places = catalog()[0]
        for p in places: p.update(latitude=14.8, longitude=120.5)
        with patch.object(replay, "simulate", return_value=batch) as run, patch.object(replay, "geography", return_value=(places, [])):
            result = replay.forecasted_replay()
        self.assertEqual(run.call_args.args[0].mode, "storm")
        self.assertEqual(result["event"]["title"], "Forecasted")
        self.assertEqual(result["kind"], "synthetic_forecast")
        self.assertTrue(result["provenance"]["synthetic"])
        self.assertEqual(result["points"][0]["score"], .07)
        self.assertEqual(result["track"], [])

    def test_real_historical_replay_all_237_and_missing_gap_suppression(self):
        event = next(e for e in replay.events() if e["available"])
        result = replay.historical_replay(event["id"])
        self.assertEqual(len(result["points"]), 5688)
        self.assertEqual(len(result["weather_locations"]), 7)
        self.assertEqual(len(result["weather_hourly"]), 7 * 24)
        self.assertEqual(len({p["barangay_psgc"] for p in result["points"]}), 237)
        self.assertTrue(all(p["score"] is None or 0 <= p["score"] <= 1 for p in result["points"]))
        self.assertEqual(sum(p["score"] is not None for p in result["points"]), 5688)
        # Regression: raw CSV "Balanga" must join the V2 "City of Balanga" alias.
        self.assertTrue(all(p["score"] is not None for p in result["points"] if p["municipality"] in ("City of Balanga", "Pilar")))
        self.assertIs(result, replay.historical_replay(event["id"]))
        places, _ = replay.geography()
        places = replay.historical_places(places)
        first = pd.Timestamp(event["window_start"])
        frames = replay.load_history(places, first)
        self.assertTrue(all(len(f) == 98 for f in frames.values()))
        identity = places[0]["weather_location_id"]
        frames[identity].loc[first-pd.Timedelta(hours=1), "precipitation"] = np.nan
        altered = replay.build_replay(event, replay.tracks().loc[replay.tracks().storm_id == event["id"]], places, frames, replay.service().model())
        affected = next(p for p in altered["points"] if p["barangay_psgc"] == places[0]["barangay_psgc"])
        self.assertIsNone(affected["score"])
        receipt = replay.DATA_ROOT / "storm_replays/replay_validation.json"
        receipt.write_text(json.dumps({"event": event, "points": len(result["points"]),
            "available_scores": sum(p["score"] is not None for p in result["points"]),
            "missing_scores": sum(p["score"] is None for p in result["points"]),
            "max_score": max(p["score"] for p in result["points"] if p["score"] is not None),
            "model_id": result["model_id"], "lookback_hours": result["provenance"]["lookback_hours"]}, indent=2), encoding="utf-8")

    def test_replay_api_routes_and_unavailable_response(self):
        app = FastAPI(); app.include_router(replay.router)
        async def request(path):
            messages = []
            async def send(message): messages.append(message)
            async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
            await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
                "scheme": "http", "path": path, "query_string": b"", "headers": [],
                "server": ("test", 80), "client": ("test", 1), "root_path": ""}, receive, send)
            return messages[0]["status"], json.loads(b"".join(m.get("body", b"") for m in messages[1:]))
        status, body = asyncio.run(request("/api/storm-replays"))
        self.assertEqual(status, 200); self.assertEqual(body["years"], [2025, 2026])
        with patch.object(replay, "forecasted_replay", return_value={"kind": "synthetic_forecast"}):
            self.assertEqual(asyncio.run(request("/api/storm-replays/forecasted")), (200, {"kind": "synthetic_forecast"}))
        with patch.object(replay, "historical_replay", side_effect=OSError("missing weather")):
            self.assertEqual(asyncio.run(request("/api/storm-replays/example"))[0], 503)


if __name__ == "__main__": unittest.main()
