import unittest
from dataclasses import replace
from backend.tests.test_impacts import fixture
from scripts.expand_small_impacts import select_ancestor, POLICY


class UpstreamTests(unittest.TestCase):
    def test_zero_line_transformer_moves_to_nearest_physical_ancestor(self):
        graph = fixture()
        selected = select_ancestor(graph, 'node', 't1', 'f1', replace(POLICY, min_transformers=1, min_line_km=.03))
        self.assertEqual(selected['identity'], 'ab')
        self.assertEqual(selected['hops'], 2)
        self.assertFalse(selected['minimum_physical_branch_exception'])

    def test_adequate_source_is_unchanged(self):
        selected = select_ancestor(fixture(), 'edge', 'ab', 'f1', replace(POLICY, min_transformers=1, min_line_km=.03))
        self.assertEqual(selected['identity'], 'ab')
        self.assertEqual(selected['hops'], 0)

    def test_zero_line_exception_is_explicit_when_small_budget_cannot_reach_lines(self):
        graph = fixture()
        selected = select_ancestor(graph, 'node', 't1', 'f1', replace(POLICY, max_upstream_hops=1))
        self.assertEqual(selected['identity'], 'ab')
        self.assertTrue(selected['minimum_physical_branch_exception'])
        self.assertGreater(graph.branch_metrics(selected['kind'], selected['identity'])[2], 0)

    def test_existing_physical_branch_respects_distance_budget(self):
        selected = select_ancestor(fixture(), 'edge', 'ab', 'f1', replace(POLICY, max_upstream_km=0))
        self.assertEqual(selected['identity'], 'ab')

    def test_cross_feeder_is_rejected(self):
        with self.assertRaises(ValueError):
            select_ancestor(fixture(), 'node', 't1', 'f2')


if __name__ == '__main__':
    unittest.main()
