"""Statistical and integrity checks for the V3 simulation contract."""
import sys
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generate_synthetic_outages_v3 import (CAUSES, Config, SOFT_TARGETS, calibrate_causes,
    calibrate_occurrence, cause_logits, remove_collisions, sigmoid, softmax, validate_config, draw_durations)
import outage_environment as env


class OutageV3Tests(unittest.TestCase):
    def test_original_formula_coefficients_and_robust_scaling(self):
        np.testing.assert_array_equal(env.WEATHER_WEIGHTS, [.30, .35, .20, .15])
        np.testing.assert_array_equal(env.SCENARIOS["baseline"], [.50, .20, .10, .12, .08])
        self.assertAlmostEqual(env.propensity(np.array([[.5, .2, .3, .1, .4]]), env.SCENARIOS["baseline"])[0], .364)
        np.testing.assert_array_equal(env.Normalizer(0, 4.2).transform([0, 2.1, 4.2, 100]), [0, .5, 1, 1])

    def test_v2_duration_sampler_and_mixed_fractional_seconds(self):
        self.assertEqual(draw_durations.__module__, "generate_synthetic_outages")
        fits = pd.DataFrame({"mu": np.log([60.] * 6), "sigma": [.8] * 6})
        duration, _ = draw_durations(np.zeros(20000, dtype=int), np.zeros((20000, 5)),
                                     np.random.default_rng(42), fits, np.zeros(6), Config())
        self.assertGreater(duration.mean(), np.median(duration))
        self.assertTrue(((duration >= 1) & (duration <= 2886)).all())
        values = pd.Series(["2019-01-01 08:01:02.500+08:00", "2019-01-01 08:01:02+08:00"])
        parsed = pd.to_datetime(values, format="ISO8601", utc=True)
        self.assertEqual((parsed.iloc[0] - parsed.iloc[1]).total_seconds(), .5)

    def test_soft_geography_and_environmental_remainder(self):
        names = ["Abucay", "Bagac", "Dinalupihan", "Morong", "Samal"]
        centers = np.linspace(.05, .95, 10)
        histogram = np.array([np.full(10, n) for n in [10000, 12000, 20000, 16000, 14000]])
        cfg = Config(target_total_events=6000)
        alpha, gamma, table = calibrate_occurrence(histogram, centers, names, cfg)
        self.assertAlmostEqual(table.calibrated_expected_events.sum(), 6000, places=5)
        for name, target in SOFT_TARGETS.items():
            actual = table.set_index("municipality").loc[name, "calibrated_expected_share"]
            self.assertLess(abs(actual-target), .005)
        self.assertEqual(gamma[0], 0)
        self.assertEqual(gamma[3], 0)
        self.assertAlmostEqual(table.iloc[3].calibrated_expected_events/table.iloc[0].calibrated_expected_events, 1.6)
        self.assertGreater(sigmoid(alpha), 0)
        self.assertGreater(sigmoid(alpha+cfg.beta), sigmoid(alpha))

    def test_ridge_is_a_soft_target(self):
        names = ["Bagac", "Dinalupihan", "Samal", "Other"]
        hist = np.full((4, 10), 10000)
        centers = np.linspace(.05, .95, 10)
        _, small, _ = calibrate_occurrence(hist, centers, names, Config())
        _, large, table = calibrate_occurrence(hist, centers, names, Config(municipality_regularization=1000))
        self.assertLess(np.linalg.norm(large), np.linalg.norm(small))
        self.assertNotAlmostEqual(table.iloc[0].calibrated_expected_share, SOFT_TARGETS["Bagac"], places=3)

    def test_cause_calibration_only_constrains_weather_and_animal(self):
        rng = np.random.default_rng(123)
        components = rng.random((2000, 5))
        raw = rng.random((2000, 4))
        cfg = Config()
        logits = cause_logits(components, raw, rng.random(2000), cfg)
        intercepts, p = calibrate_causes(logits, np.ones(2000), cfg)
        self.assertAlmostEqual(p[:, 0].mean(), .4, places=8)
        self.assertAlmostEqual(p[:, 4].mean(), .03, places=8)
        np.testing.assert_array_equal(intercepts[[1, 2, 3, 5]], 0)
        self.assertTrue((p > 0).all())
        np.testing.assert_allclose(p.sum(axis=1), 1)

    def test_heat_changes_equipment_and_supply_relative_odds(self):
        cfg = Config()
        components = np.tile([.3, .4, .2, .1, .1], (2, 1))
        raw = np.tile([.1, .4, .2, .3], (2, 1))
        p = softmax(cause_logits(components, raw, np.array([0., 1.]), cfg))
        for k in [2, 3]:
            self.assertGreater(p[1, k], p[0, k])
            self.assertGreater(p[0, k], 0)
        self.assertLess(p[1, 3], 1)

    def test_tree_and_weather_separate_stochastic_weights(self):
        cfg = Config()
        components = np.array([[.3, .2, .2, .0, .1], [.3, .9, .2, .8, .1]])
        raw = np.tile([.1, .9, .2, .3], (2, 1))
        p = softmax(cause_logits(components, raw, np.zeros(2), cfg))
        self.assertGreater(p[1, 1], p[0, 1])
        self.assertTrue((p[:, 0] > 0).all())
        self.assertEqual(len(CAUSES), 6)
        self.assertNotIn("Lightning", CAUSES)

    def test_collision_suppression_and_boundary_no_replacements(self):
        times = pd.date_range("2020-01-01", periods=6, freq="h", tz="Asia/Manila")
        indices, collisions, boundary = remove_collisions(np.array([0, 1, 2, 4]), np.array([120., 10., 30., 90.]), times)
        np.testing.assert_array_equal(indices, [0, 2])
        self.assertEqual(collisions, 1)
        self.assertEqual(boundary, 1)

    def test_realized_count_is_not_a_quota(self):
        rng = np.random.default_rng(42)
        q = np.full(100000, .006)
        counts = np.array([(rng.random(len(q)) < q).sum() for _ in range(50)])
        self.assertGreater(counts.std(), 10)
        self.assertLess(abs(counts.mean()-600), 15)

    def test_invalid_configuration(self):
        for cfg in [Config(seed=-1), Config(beta=0), Config(weather_target=.99, animal_target=.03),
                    Config(supply_equipment_split=0), Config(min_duration=3000)]:
            with self.assertRaises(ValueError):
                validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
