"""Frozen native XGBoost or numerical JSON inference; no executable model pickle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from backend.forecast_features import FEATURES


def active_model_directory(root):
    """Explicit environment override, then verified local deployment pointer."""
    if os.getenv("FORECAST_MODEL_DIR"):
        return Path(os.environ["FORECAST_MODEL_DIR"])
    root = Path(root)
    if (root.parent / 'active_bundle.json').is_file():
        try:
            from backend.datasets import dataset_paths
            selected = dataset_paths(root.parent)['model']
            if selected.parent != root.resolve():
                raise ValueError('Bundle model must be in the model directory')
            return selected
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise ModelUnavailable('Invalid active dataset bundle') from exc
    pointer = root / "active_forecast.json"
    if not pointer.exists(): return root / "outage_v2_xgboost"
    try:
        name = json.loads(pointer.read_text())["directory"]
        if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
            raise ValueError("Invalid model directory")
        return root / name
    except (ValueError, OSError, KeyError) as exc:
        raise ModelUnavailable("Invalid active model pointer") from exc


def model_values(model, matrix):
    values = matrix[FEATURES].to_numpy("float32")
    categories = getattr(model, "municipality_categories", [])
    if not categories: return values
    ids = matrix.municipality_psgc.to_numpy()
    encoded = np.column_stack([ids == m for m in categories]).astype("float32")
    if not (encoded.sum(axis=1) == 1).all():
        raise ModelUnavailable("Municipality is outside the fitted categorical contract")
    return np.column_stack([values, encoded])


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
            self.municipality_categories = m.get("municipality_categories", [])
            expected = FEATURES + ["municipality_" + p for p in self.municipality_categories]
            if (m["artifact_version"] not in (1, 2) or m["features"] != expected
                    or (m["artifact_version"] == 1 and self.municipality_categories)
                    or (m["artifact_version"] == 2 and (len(self.municipality_categories) != 12
                        or len(set(self.municipality_categories)) != 12
                        or any(not isinstance(p, str) or len(p) != 10 or not p.isdigit() for p in self.municipality_categories)))
                    or m["model_type"] not in ("xgboost_binary_logistic", "portable_binary_classifier")
                    or m["weather_lag_hours"] != 1 or m["target"] != "outage_next_hour"
                    or m["target_interval"] != "(t,t+1h]"):
                raise ValueError("Unsupported model feature/target contract")
            artifact = "model.ubj" if m["model_type"] == "xgboost_binary_logistic" else "model.json"
            if m.get("model_artifact", artifact) != artifact:
                raise ValueError("Unexpected model artifact")
            if digest(directory / artifact) != m["model_sha256"]:
                raise ValueError("Model checksum mismatch")
            if digest(directory / "parity.json") != m["parity_sha256"]:
                raise ValueError("Reference predictions checksum mismatch")
            self.medians = np.asarray(m["imputer_medians"], dtype="float32")
            if self.medians.shape != (len(expected),) or not np.isfinite(self.medians).all():
                raise ValueError("Invalid fitted imputer")
            self.classifier = None
            if artifact == "model.json":
                from backend.portable_classifier import PortableClassifier
                self.classifier = PortableClassifier(json.loads((directory / artifact).read_text()), len(expected))
            else:
                import xgboost as xgb
                self.booster = xgb.Booster(model_file=directory / artifact)
                self.booster.set_param({"nthread": 2})
                config = json.loads(self.booster.save_config())
                if config["learner"]["objective"]["name"] != "binary:logistic" or self.booster.num_features() != len(expected):
                    raise ValueError("Unexpected model objective or feature count")
            parity = json.loads((directory / "parity.json").read_text(encoding="utf-8"))
            actual = self.predict(np.asarray(parity["inputs"], dtype="float32"))
            expected = np.asarray(parity["expected_scores"], dtype="float32")
            if actual.shape != expected.shape or not np.allclose(actual, expected, rtol=1e-6, atol=1e-8):
                raise ValueError("Local predictions disagree with evaluated Colab model")
            self.parity_rows = len(actual)
        except (OSError, ValueError, KeyError, TypeError, ImportError, OverflowError) as exc:
            raise ModelUnavailable(f"Forecast model unavailable: {exc}") from exc

    def predict(self, values):
        x = np.asarray(values, dtype="float32")
        if x.ndim != 2 or x.shape[1] != len(self.medians) or np.isinf(x).any():
            raise ValueError("Unexpected model input dimension or infinite values")
        x = np.where(np.isnan(x), self.medians, x).astype("float32")
        scores = self.classifier.predict(x) if self.classifier is not None else self.booster.inplace_predict(x)
        if not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
            raise ValueError("Model returned invalid scores")
        return scores
