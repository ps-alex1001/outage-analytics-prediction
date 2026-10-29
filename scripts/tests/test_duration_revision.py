import unittest
import numpy as np
from scripts.revise_outage_durations import randomized_durations


class RandomDurationTests(unittest.TestCase):
    def test_bounds_variation_and_no_ceiling_spike(self):
        original = np.full(2000, 800.)
        result = randomized_durations(original, [f'event-{i}' for i in range(2000)])
        self.assertTrue(((result >= 60) & (result <= 800)).all())
        self.assertGreater((result > 240).mean(), .15)
        self.assertLess((result > 480).mean(), .05)
        self.assertGreater((result < 240).mean(), .60)
        self.assertGreater(len(np.unique(result)), 1500)
        self.assertGreater(result.std(), 20)

    def test_seed_replay_order_and_original_bounds(self):
        values = np.array([30., 120., 121., 180., 800.])
        ids = ['a', 'b', 'c', 'd', 'e']
        a = randomized_durations(values, ids)
        np.testing.assert_array_equal(a, randomized_durations(values, ids))
        np.testing.assert_array_equal(a, randomized_durations(values[::-1], ids[::-1])[::-1])
        np.testing.assert_array_equal(a[:2], values[:2])
        self.assertTrue((a <= values).all())
        self.assertFalse(np.array_equal(a, randomized_durations(values, ids, seed=43)))


if __name__ == '__main__':
    unittest.main()
