"""Focused regression/statistical tests for the simulation's material guarantees."""
import sys
from pathlib import Path
import unittest
from dataclasses import replace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import outage_environment as env
from generate_synthetic_outages import (BASELINE, Config, calibrate_cause_probabilities,
    cause_odds, draw_causes, draw_durations, municipality_counts, sampling_weights)
from penelco_calibration import CAUSES, location_mapping


class OutageV2Tests(unittest.TestCase):
    def test_exact_original_formula_and_normalization(self):
        np.testing.assert_allclose(BASELINE, [.5, .2, .1, .12, .08])
        np.testing.assert_allclose(env.WEATHER_WEIGHTS, [.3, .35, .2, .15])
        self.assertAlmostEqual(float(env.propensity(np.array([[.5, .2, .3, .1, .4]]), BASELINE)[0]), .364)
        np.testing.assert_allclose(env.Normalizer(10, 20).transform([0, 10, 15, 20, 10000]), [0, 0, .5, 1, 1])
        with self.assertRaises(ValueError):
            env.Normalizer.fit([1, 1, 1], 'constant')

    def test_positive_weight_and_environmental_influence(self):
        cfg = replace(Config(), noise_sigma=0)
        score = np.array([0., .25, .5, .75, 1.])
        w = sampling_weights(score, np.random.default_rng(42), cfg)
        np.testing.assert_allclose(w, np.exp(4 * score))
        self.assertTrue((np.diff(w) > 0).all())
        self.assertGreater(w[0], 0)
        # Stochastic selection must actually select low scores, never only max.
        draws = np.random.default_rng(42).choice(5, size=100000, p=w / w.sum())
        self.assertEqual(set(draws), set(range(5)))
        self.assertGreater((draws == 4).sum(), (draws == 1).sum())

    def test_fixed_seed_and_changed_seed_weights(self):
        score = np.linspace(0, 1, 10000)
        a = sampling_weights(score, np.random.default_rng(42), Config())
        b = sampling_weights(score, np.random.default_rng(42), Config())
        c = sampling_weights(score, np.random.default_rng(43), Config())
        np.testing.assert_array_equal(a, b)
        self.assertFalse(np.array_equal(a, c))
        self.assertTrue(np.isfinite(a).all() and (a > 0).all())

    def test_municipality_counts_stochastic_close_and_conserved(self):
        cfg = Config()
        targets = [.1, .2, .3, .4]
        a, _ = municipality_counts(targets, 10000, np.random.default_rng(42), cfg)
        b, _ = municipality_counts(targets, 10000, np.random.default_rng(43), cfg)
        self.assertEqual(a.sum(), 10000)
        self.assertLessEqual(np.max(np.abs(a / 10000 - targets)), cfg.municipality_tolerance)
        self.assertFalse(np.array_equal(a, b))
        q, _ = municipality_counts(targets, 101, np.random.default_rng(42), replace(cfg, allocation='quota'))
        self.assertEqual(q.sum(), 101)
        self.assertTrue((np.abs(q - 101 * np.array(targets)) <= 1).all())

    def test_cross_boundary_mapping_is_not_forced(self):
        names = {'Samal': '0300812000', 'Orani': '0300809000', 'Abucay': '0300801000',
                 'Dinalupihan': '0300804000', 'Hermosa': '0300805000'}
        for area, circuit in [('Samal', '3'), ('Gugo', '1 & 3'), ('Pinulot', '3'), ('Hermosa', '1')]:
            row = location_mapping(area, circuit, names)
            self.assertEqual(row['mapping_status'], 'ambiguous')
            self.assertEqual(row['municipality_psgc'], '')
        self.assertEqual(location_mapping('Gugo', '3', names)['municipality'], 'Orani')
        self.assertEqual(location_mapping('Gugo', '4', names)['municipality'], 'Abucay')
        self.assertEqual(location_mapping('Do�a', '1', names)['mapping_status'], 'unresolved')

    def test_cause_marginals_and_conditional_variation(self):
        c = np.vstack([np.tile([.1, .2, .1, .02, .01], (20000, 1)),
                       np.tile([.9, .8, .1, .7, .7], (20000, 1))])
        raw = np.vstack([np.full((20000, 4), .1), np.full((20000, 4), .9)])
        target = np.array([.2766, .191, .1015, .1077, .1346, .1786, .01])
        probabilities, _ = calibrate_cause_probabilities(cause_odds(c, raw), target)
        np.testing.assert_allclose(probabilities.mean(axis=0), target, atol=1e-9)
        self.assertGreater(probabilities[-1, 1], probabilities[0, 1])
        self.assertGreater(probabilities[-1, 2], probabilities[0, 2])
        draws = draw_causes(probabilities, np.random.default_rng(42))
        np.testing.assert_allclose(np.bincount(draws, minlength=7) / len(draws), target, atol=.01)
        # Configurable zero animal share remains a valid cause calibration.
        target[-1], target[4] = 0., target[4] + target[-1]
        p, _ = calibrate_cause_probabilities(cause_odds(c, raw), target)
        self.assertTrue((p[:, -1] == 0).all())

    def test_duration_is_separate_positive_skew_and_not_clipped(self):
        n = 50000
        c = np.tile([.5, .5, .5, .25, .25], (n, 1))
        fit = pd.DataFrame({'mu': np.log([50] * len(CAUSES)), 'sigma': [.9] * len(CAUSES)})
        x, _ = draw_durations(np.zeros(n, int), c, np.random.default_rng(42), fit, np.zeros(7), Config())
        self.assertTrue((x > 0).all())
        self.assertGreater(x.mean(), np.median(x))
        self.assertGreater(np.std(x), 20)
        self.assertLess((x == Config().min_duration).mean(), .001)
        self.assertFalse((x == Config().max_duration).any())

    def test_timezone_alignment(self):
        self.assertEqual(env.local_timestamp('2019-01-01 08:00').tz_convert('UTC'), pd.Timestamp('2019-01-01', tz='UTC'))


if __name__ == '__main__':
    unittest.main()
