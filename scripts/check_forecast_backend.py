"""Check installed inference assets; --live also requests and scores a real weather snapshot."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.forecast import ForecastService


def check(live=False):
    service = ForecastService()
    report = service.metadata()
    if report["status"] != "ready":
        raise RuntimeError(report["error"])
    if live:
        result = service.latest(24)
        available = [p for p in result["points"] if p["score"] is not None]
        report["live_check"] = {
            "issued_at": result["issued_at"], "status": result["status"],
            "weather_snapshot_id": result["weather"]["snapshot_id"],
            "points": len(result["points"]), "scored_points": len(available),
            "missing_features": sorted({f for p in result["points"] for f in p["missing_features"]}),
            "score_min": min((p["score"] for p in available), default=None),
            "score_max": max((p["score"] for p in available), default=None),
        }
        if len(result["points"]) != 5688 or not available:
            raise RuntimeError("Live check did not produce the expected usable province forecast: " + json.dumps(report["live_check"]))
        directory = ROOT / "data/forecasts"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "backend_validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check(args.live), indent=2))
