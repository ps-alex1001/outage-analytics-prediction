"""Native XGBoost inference; never unpickle or retrain a model in the service."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from backend.forecast_features import FEATURES


class ModelUnavailable(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ForecastModel:
    def __init__(self, directory):
        directory = Path(directory)
        try:
            self.manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            m = self.manifest
            if (m["artifact_version"] != 1 or m["features"] != FEATURES
                    or m["model_type"] != "xgboost_binary_logistic"
                    or m["weather_lag_hours"] != 1 or m["target"] != "outage_next_hour"
                    or m["target_interval"] != "(t,t+1h]"):
                raise ValueError("Unsupported model feature/target contract")
            if digest(directory / "model.ubj") != m["model_sha256"]:
                raise ValueError("Model checksum mismatch")
            if digest(directory / "parity.json") != m["parity_sha256"]:
                raise ValueError("Reference predictions checksum mismatch")
            self.medians = np.asarray(m["imputer_medians"], dtype="float32")
            if self.medians.shape != (24,) or not np.isfinite(self.medians).all():
                raise ValueError("Invalid fitted imputer")
            import xgboost as xgb
            self.booster = xgb.Booster(model_file=directory / "model.ubj")
            self.booster.set_param({"nthread": 2})
            config = json.loads(self.booster.save_config())
            if config["learner"]["objective"]["name"] != "binary:logistic" or self.booster.num_features() != 24:
                raise ValueError("Unexpected model objective or feature count")
            parity = json.loads((directory / "parity.json").read_text(encoding="utf-8"))
            actual = self.predict(np.asarray(parity["inputs"], dtype="float32"))
            expected = np.asarray(parity["expected_scores"], dtype="float32")
            if actual.shape != expected.shape or not np.allclose(actual, expected, rtol=1e-6, atol=1e-8):
                raise ValueError("Local predictions disagree with evaluated Colab model")
            self.parity_rows = len(actual)
        except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
            raise ModelUnavailable(f"Forecast model unavailable: {exc}") from exc

    def predict(self, values):
        x = np.asarray(values, dtype="float32")
        if x.ndim != 2 or x.shape[1] != 24 or np.isinf(x).any():
            raise ValueError("Expected an n x 24 matrix without infinite values")
        x = np.where(np.isnan(x), self.medians, x).astype("float32")
        scores = self.booster.inplace_predict(x)
        if not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
            raise ValueError("Model returned invalid scores")
        return scores
