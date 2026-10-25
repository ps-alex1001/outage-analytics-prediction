"""Independent graph/count/hash audit plus sampled spatial reconstruction.

Run: uv run python scripts/verify_outage_topology_impacts.py
All 6k+ impact sets are checked for exact traversal, IDs, counts and lengths.
Geometry-to-barangay reconstruction covers representative and large impacts.
"""
from collections import defaultdict
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd
from shapely.geometry import shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/impacts/v2_topology_1_2_0'


def main():
    manifest = json.loads((OUT / 'run_manifest.json').read_text())
    for item in manifest['inputs'].values():
        with Path(item['path']).open('rb') as f:
            assert hashlib.file_digest(f, 'sha256').hexdigest() == item['sha256']
    topology = ROOT / 'data/topology/react_data'
    def load(name):
        return json.loads((topology / name).read_text(encoding='utf-8'))
    nodes = {f['properties']['node_id']: f for f in load('network_nodes.geojson')['features']}
    edges = {f['properties']['edge_id']: f for f in load('network_edges.geojson')['features']}
    children, parents = defaultdict(list), {}
    active = set()
    for eid, a, b in load('active_tree.json')['edges']:
        active.add(eid); children[a].append((b, eid)); parents[b] = (a, eid)
    sets = pd.read_parquet(OUT / 'impact_sets.parquet').set_index('impact_set_id')
    mapping = pd.read_parquet(OUT / 'outage_fault_mapping.parquet')
    summary = pd.read_parquet(OUT / 'outage_impact_summary.parquet')
    relationships = pd.read_parquet(OUT / 'outage_affected_barangays.parquet')
    source = mapping.loc[mapping.resolution_status.eq('resolved_modeled')].drop_duplicates('impact_set_id').set_index('impact_set_id')
    types = set(manifest['line_types_counted'])
    for sid, s in sets.iterrows():
        a = source.loc[sid]
        kind = a.fault_source_kind
        identity = a.fault_source_topology_id
        root = edges[identity]['properties']['to_node_id'] if kind == 'edge' else identity
        found_nodes, found_edges, todo = set(), set(), [root]
        while todo:
            n = todo.pop()
            assert n not in found_nodes
            found_nodes.add(n)
            for child, edge in children[n]:
                todo.append(child); found_edges.add(edge)
        if kind == 'edge':
            found_edges.add(identity)
        else:
            assert nodes[root]['properties']['node_type'] == 'transformer' and not children[root]
            found_edges.add(parents[root][1])
        assert found_nodes == set(s.affected_node_ids)
        assert found_edges == set(s.affected_edge_ids)
        assert found_edges <= active
        assert all(nodes[n]['properties']['feeder_id'] == a.feeder_id and nodes[n]['properties']['source_node_id'] == a.substation_node_id for n in found_nodes)
        assert all(edges[e]['properties']['feeder_id'] == a.feeder_id for e in found_edges)
        assert s.affected_pole_count == sum(nodes[n]['properties']['node_type'] == 'pole' for n in found_nodes)
        assert s.affected_transformer_count == sum(nodes[n]['properties']['node_type'] == 'transformer' for n in found_nodes)
        physical = [e for e in found_edges if edges[e]['properties']['edge_type'] in types]
        assert s.affected_line_segment_count == len(physical)
        assert np.isclose(s.affected_line_length_km, sum(edges[e]['properties']['length_m'] for e in physical) / 1000)
        # Verify upstream blue path terminates at the recorded station and has no
        # edge overlap with the isolated downstream set.
        upstream = edges[identity]['properties']['from_node_id'] if kind == 'edge' else parents[identity][0]
        upnodes, upedges = [upstream], []
        while upstream in parents:
            upstream, eid = parents[upstream]
            upnodes.append(upstream); upedges.append(eid)
        assert upnodes[-1] == a.substation_node_id
        assert upnodes == list(s.upstream_node_ids) and upedges == list(s.upstream_edge_ids)
        assert not (set(upedges) & found_edges)
        if a.fault_source_kind == 'node':
            assert a.fault_source_topology_id in found_nodes
        else:
            assert a.fault_source_topology_id in found_edges
    assert len(summary) == len(mapping) == 10608
    assert summary.loc[summary.resolution_status.eq('unresolved'), 'affected_line_length_km'].isna().all()
    for sid, group in relationships.groupby('impact_set_id'):
        assert set(group.barangay_psgc) == set(sets.loc[sid].affected_barangay_psgcs)
    # Reconstruct intersections from original geometries for each isolation type
    # and the largest event-weighted impacts, including cross-barangay effects.
    sampled = set(summary.loc[summary.resolution_status.eq('resolved_modeled')].groupby('impact_scenario').first().impact_set_id)
    sampled.update(sets.nlargest(10, 'affected_transformer_count').index)
    sampled.update(sets.loc[sets.no_downstream_transformers].head(5).index)
    boundaries = load('barangays.geojson')['features']
    polygons = [shape(f['geometry']) for f in boundaries]
    tree = STRtree(polygons)
    geometry_cache = {}
    def codes(kind, identity):
        key = (kind, identity)
        if key not in geometry_cache:
            f = nodes[identity] if kind == 'node' else edges[identity]
            g = shape(f['geometry'])
            geometry_cache[key] = {boundaries[i]['properties']['barangay_psgc'] for i in tree.query(g, predicate='intersects')
                                   if g.geom_type == 'Point' or g.intersection(polygons[i]).length > 1e-12}
        return geometry_cache[key]
    for a in mapping.loc[mapping.resolution_status.eq('resolved_modeled')].itertuples():
        expected_anchor = edges[a.fault_source_topology_id]['properties']['from_node_id'] if a.fault_source_kind == 'edge' else a.fault_source_topology_id
        assert a.source_anchor_node_id == expected_anchor
        if a.fault_source_asset_type != 'feeder_source':
            assert a.origin_barangay_psgc in codes('node', expected_anchor)
    for sid in sorted(sampled):
        s = sets.loc[sid]
        expected = set().union(*(codes('node', n) for n in s.affected_node_ids), *(codes('edge', e) for e in s.affected_edge_ids))
        assert expected == set(s.affected_barangay_psgcs)
        a = source.loc[sid]
        if a.fault_source_asset_type != 'feeder_source':
            origins = mapping.loc[mapping.impact_set_id.eq(sid), 'origin_barangay_psgc']
            assert set(origins) <= codes(a.fault_source_kind, a.fault_source_topology_id)
    result = {'passed': True, 'all_impact_sets_independently_traversed': len(sets),
              'all_ids_active_scope_counts_and_lengths_verified': True,
              'all_upstream_paths_end_at_recorded_station': True,
              'sampled_spatial_intersection_reconstructions': len(sampled),
              'all_original_input_hashes_unchanged': True, 'unresolved_impacts_remain_null': True}
    (OUT / 'independent_validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
