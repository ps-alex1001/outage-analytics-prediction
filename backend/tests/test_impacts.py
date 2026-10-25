"""Radial isolation semantics plus checks against the completed V2 impact run."""
import json
from pathlib import Path
import unittest

import pandas as pd
from fastapi import HTTPException

from backend.impact_model import ImpactGraph, ProtectionPolicy, assign_source, choose_isolation, choose_branch_source, impact_set, DEFAULT_WEIGHTS
from backend import impacts


def fixture():
    def node(i, kind, feeder='f1', station='s1', x=120.5):
        return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [x, 14.7]},
                'properties': {'node_id': i, 'asset_id': i, 'node_type': kind, 'feeder_id': feeder,
                               'source_node_id': station}}
    def edge(i, a, b, kind='distribution_line_section', feeder='f1', station='s1', active=True):
        return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [120.5, 14.7]},
                'properties': {'edge_id': i, 'from_node_id': a, 'to_node_id': b, 'edge_type': kind,
                               'feeder_id': feeder, 'source_node_id': station, 'is_tree_edge': active,
                               'length_m': 40. if kind == 'distribution_line_section' else 0.}}
    nodes = [node('s1', 'substation', None), node('a', 'pole'), node('b', 'pole'), node('c', 'pole'),
             node('t1', 'transformer'), node('t2', 'transformer'), node('u', 'pole', 'unresolved', None),
             node('tu', 'transformer', 'unresolved', None), node('s2', 'substation', None, 's2'),
             node('d', 'pole', 'f2', 's2'), node('t3', 'transformer', 'f2', 's2')]
    edges = [edge('lead1', 's1', 'a', 'inferred_station_lead'), edge('ab', 'a', 'b'), edge('ac', 'a', 'c'),
             edge('bt1', 'b', 't1', 'transformer_attachment'), edge('at2', 'a', 't2', 'transformer_attachment'),
             edge('utu', 'u', 'tu', 'transformer_attachment', 'unresolved', None),
             edge('candidate', 'b', 'c', active=False), edge('lead2', 's2', 'd', 'inferred_station_lead', 'f2', 's2'),
             edge('dt3', 'd', 't3', 'transformer_attachment', 'f2', 's2')]
    tree = {'edges': [[e['properties'][k] for k in ['edge_id', 'from_node_id', 'to_node_id']]
                      for e in edges if e['properties']['is_tree_edge']]}
    feeders = [{'feeder_id': fid, 'root_node_id': root, 'substation_node_id': station}
               for fid, root, station in [('f1', 'a', 's1'), ('f2', 'd', 's2')]]
    return ImpactGraph({'features': nodes}, {'features': edges}, tree, feeders)


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.graph = fixture()

    def test_raw_transformer_trace_remains_terminal(self):
        nodes, edges, upnodes, upedges = self.graph.trace('node', 't1')
        self.assertEqual(nodes, ['t1'])
        self.assertEqual(edges, ['bt1'])
        self.assertEqual(upnodes, ['b', 'a', 's1'])
        self.assertEqual(upedges, ['ab', 'lead1'])

    def test_final_source_is_the_only_loss_origin(self):
        class Spatial:
            asset_barangays = {}
        result = impact_set(self.graph, Spatial(), {
            'fault_source_kind': 'edge', 'fault_source_topology_id': 'ab', 'feeder_id': 'f1',
            'assumed_isolation_kind': 'edge', 'assumed_isolation_component_id': 'lead1'})
        self.assertEqual(set(result['affected_node_ids']), {'b', 't1'})
        self.assertNotIn('lead1', result['affected_edge_ids'])

    def test_line_fault_only_follows_destination_subtree(self):
        nodes, edges, _, upstream = self.graph.trace('edge', 'ab')
        self.assertEqual(nodes, ['b', 't1'])
        self.assertEqual(edges, ['ab', 'bt1'])
        self.assertEqual(upstream, ['lead1'])
        self.assertNotIn('candidate', edges)

    def test_upstream_supply_loss_is_one_feeder_not_station(self):
        nodes, edges, _, _ = self.graph.trace('edge', 'lead1')
        self.assertEqual(set(nodes), {'a', 'b', 'c', 't1', 't2'})
        self.assertNotIn('d', nodes)
        self.assertNotIn('lead2', edges)

    def test_disconnected_assignment_is_not_zero_or_forced(self):
        with self.assertRaises(ValueError):
            self.graph.trace('node', 'tu')
        class Spatial:
            properties = {'code': {'barangay': 'Origin', 'municipality': 'Place', 'municipality_psgc': 'm'}}
            candidates = {'code': {'line': [], 'transformer': []}}
            local_disconnected = {'code': 1}
        a = assign_source({'outage_id': 'event', 'barangay_psgc': 'code', 'barangay': 'Origin', 'municipality': 'Place', 'municipality_psgc': 'm', 'cause_category': 'Unknown'}, self.graph, Spatial(), 42, DEFAULT_WEIGHTS)
        self.assertEqual(a['resolution_status'], 'unresolved')
        self.assertEqual(a['unresolved_reason'], 'local_assets_supply_disconnected')
        self.assertNotIn('fault_source_asset_id', a)

    def test_line_with_no_downstream_transformer_is_valid(self):
        nodes, edges, _, _ = self.graph.trace('edge', 'ac')
        self.assertEqual(nodes, ['c'])
        self.assertEqual(edges, ['ac'])

    def test_inactive_candidate_cannot_be_a_fault_path(self):
        with self.assertRaises(ValueError):
            self.graph.trace('edge', 'candidate')

    def test_transformer_protection_expands_without_moving_fault(self):
        a = {'fault_source_kind': 'node', 'fault_source_topology_id': 't1',
             'fault_source_asset_type': 'transformer', 'feeder_id': 'f1'}
        result = choose_isolation(self.graph, a, ProtectionPolicy(min_transformers=1, min_line_km=.03))
        self.assertEqual(result['assumed_isolation_component_id'], 'ab')
        self.assertTrue(result['isolation_expanded'])
        self.assertTrue(result['protection_target_met'])
        self.assertEqual(a['fault_source_topology_id'], 't1')
        n, e, _, _ = self.graph.trace(result['assumed_isolation_kind'], result['assumed_isolation_component_id'])
        self.assertEqual(set(n), {'b', 't1'})
        self.assertIn('ab', e)
        self.assertNotIn('d', n)

    def test_protection_targets_are_not_forced_across_station_or_size_limit(self):
        a = {'fault_source_kind': 'node', 'fault_source_topology_id': 't1',
             'fault_source_asset_type': 'transformer', 'feeder_id': 'f1'}
        result = choose_isolation(self.graph, a)
        self.assertFalse(result['protection_target_met'])
        self.assertEqual(result['protection_limit_reason'], 'active_feeder_boundary')
        self.assertNotEqual(result['assumed_isolation_component_id'], 'lead1')
        bounded = choose_isolation(self.graph, a, ProtectionPolicy(max_expanded_line_km=.01))
        self.assertEqual(bounded['assumed_isolation_component_id'], 't1')
        self.assertEqual(bounded['protection_limit_reason'], 'expanded_size_limit')

    def test_existing_large_and_supply_scenarios_are_preserved(self):
        a = {'fault_source_kind': 'edge', 'fault_source_topology_id': 'ab',
             'fault_source_asset_type': 'line', 'feeder_id': 'f1'}
        result = choose_isolation(self.graph, a, ProtectionPolicy(min_transformers=1, min_line_km=.03))
        self.assertFalse(result['isolation_expanded'])
        source = {**a, 'fault_source_topology_id': 'lead1', 'fault_source_asset_type': 'feeder_source'}
        self.assertFalse(choose_isolation(self.graph, source)['isolation_expanded'])

    def test_promotion_moves_the_actual_source_and_respects_origin_anchor(self):
        class Spatial:
            candidates = {'code': {'line': ['ab'], 'transformer': ['t1']}}
            asset_barangays = {('node', 'a'): {'code'}}
        assignment = {'fault_source_kind': 'node', 'fault_source_topology_id': 't1',
                      'fault_source_asset_type': 'transformer', 'feeder_id': 'f1',
                      'impact_scenario': 'isolated_transformer_branch', 'assignment_method': 'sampled'}
        result = choose_branch_source({'barangay_psgc':'code'}, self.graph, Spatial(), assignment,
                                     ProtectionPolicy(min_transformers=1, min_line_km=.03))
        self.assertEqual(result['fault_source_topology_id'], 'ab')
        self.assertEqual(result['source_anchor_node_id'], 'a')
        self.assertEqual(result['initial_source_topology_id'], 't1')
        self.assertTrue(result['source_promoted_upstream'])
        Spatial.asset_barangays = {('node', 'a'): {'another_barangay'}}
        blocked = choose_branch_source({'barangay_psgc':'code'}, self.graph, Spatial(), assignment)
        self.assertEqual(blocked['fault_source_topology_id'], 't1')
        self.assertFalse(blocked['source_promoted_upstream'])


@unittest.skipUnless((impacts.IMPACTS_PATH / 'run_manifest.json').is_file(), 'Precomputed V2 impacts not installed')
class ImpactDataTests(unittest.TestCase):
    def test_source_promotion_and_small_exceptions_are_explicit(self):
        frame, _, manifest = impacts.dataset()
        self.assertEqual(manifest['generator_version'], '1.2.0')
        self.assertNotIn('assumed_isolation_component_id', frame.columns)
        mapped = frame.resolution_status.eq('resolved_modeled')
        self.assertTrue(frame.loc[mapped, 'source_anchor_node_id'].notna().all())
        small = frame.loc[mapped & frame.source_target_met.eq(False)]
        self.assertTrue(small.source_selection_limit_reason.notna().all())
        promoted = frame.loc[frame.source_promoted_upstream.eq(True)]
        self.assertGreater(len(promoted), 0)
        self.assertTrue(promoted.fault_source_topology_id.ne(promoted.initial_source_topology_id).all())
        policy = manifest['source_selection_policy']
        self.assertTrue(promoted.affected_transformer_count.le(policy['max_expanded_transformers']).all())
        self.assertTrue(promoted.affected_line_length_km.le(policy['max_expanded_line_km']).all())

    def test_unresolved_metrics_are_null_and_origin_is_preserved(self):
        frame, _, _ = impacts.dataset()
        unresolved = frame.loc[frame.resolution_status.eq('unresolved')]
        self.assertGreater(len(unresolved), 0)
        self.assertTrue(unresolved.affected_transformer_count.isna().all())
        self.assertTrue(frame.barangay_psgc.eq(frame.origin_barangay_psgc).all())
        for oid in [unresolved.index[0], frame.loc[frame.resolution_status.eq('resolved_modeled')].index[0]]:
            d = impacts.event_impact(oid)
            json.dumps(d, allow_nan=False)
            if d['event']['resolution_status'] == 'unresolved':
                self.assertIsNone(d['impact'])

    def test_table_filters_sort_and_pages(self):
        one = impacts.list_events(q='', year=2026, municipality_psgc=None, barangay_psgc=None,
            cause_category='Tree Contact', status='resolved_modeled', sort='duration_minutes', direction='desc', page=1, page_size=10)
        self.assertEqual(len(one['events']), 10)
        minutes = [x['duration_minutes'] for x in one['events']]
        self.assertEqual(minutes, sorted(minutes, reverse=True))
        self.assertTrue(all(x['cause_category'] == 'Tree Contact' and x['start_time'].startswith('2026') for x in one['events']))
        with self.assertRaises(HTTPException):
            impacts.event_impact('nonexistent')

    def test_shared_memberships_match_summary(self):
        frame, sets, _ = impacts.dataset()
        members = pd.read_parquet(impacts.IMPACTS_PATH / 'affected_assets.parquet')
        self.assertFalse(members.duplicated(['impact_set_id', 'topology_kind', 'topology_id']).any())
        counts = members.groupby(['impact_set_id', 'topology_kind']).size()
        for sid, row in sets.iterrows():
            self.assertEqual(counts.get((sid, 'node'), 0), len(row.affected_node_ids))
            self.assertEqual(counts.get((sid, 'edge'), 0), len(row.affected_edge_ids))
        self.assertEqual(len(frame), 10608)

    def test_geometry_scope_is_connected_feeder_and_excludes_candidates(self):
        data = impacts.get_metadata()
        self.assertEqual(data['total_events'], 10608)
        frame, _, _ = impacts.dataset()
        fid = frame.loc[frame.resolution_status.eq('resolved_modeled'), 'feeder_id'].iloc[0]
        raw, _ = impacts.feeder_payload(fid)
        network = json.loads(raw)
        active = {e[0] for e in network['tree']['edges']}
        self.assertEqual(active, {e['properties']['edge_id'] for e in network['edges']['features']})
        self.assertTrue(all(e['properties']['feeder_id'] == fid for e in network['edges']['features']))


if __name__ == '__main__':
    unittest.main()
