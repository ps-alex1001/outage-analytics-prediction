"""Radial source-to-downstream impact model over the imported active tree only.

Raw traversal matches topology.mjs traceSelection. Small scenarios select a
higher geographically eligible source; all loss is traced from that final source.
"""
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import shape
from shapely.strtree import STRtree
from rasterio.warp import transform as project

LINE_TYPES = {'distribution_line_section', 'road_evidenced_extension', 'inferred_local_transfer'}
DEFAULT_WEIGHTS = {
    'Tree Contact': {'line': 9., 'transformer': 1.},
    'Weather / Storm': {'line': 7., 'transformer': 3.},
    'Equipment / Technical': {'line': 1., 'transformer': 9.},
    'Animal Interference': {'line': 3., 'transformer': 7.},
    'Other': {'line': 1., 'transformer': 1.},
    'Unknown': {'line': 1., 'transformer': 1.},
}


@dataclass(frozen=True)
class ProtectionPolicy:
    min_transformers: int = 3
    min_line_km: float = .5
    max_upstream_hops: int = 30
    max_upstream_km: float = 1.
    max_expanded_transformers: int = 100
    max_expanded_line_km: float = 10.


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class ImpactGraph:
    def __init__(self, nodes, edges, tree, feeders):
        self.nodes = {f['properties']['node_id']: f for f in nodes['features']}
        self.edges = {f['properties']['edge_id']: f for f in edges['features']}
        self.feeders = {f['feeder_id']: f for f in feeders}
        if len(self.nodes) != len(nodes['features']) or len(self.edges) != len(edges['features']):
            raise ValueError('Duplicate topology IDs')
        self.children, self.parents, self.active = defaultdict(list), {}, set()
        for eid, a, b in tree['edges']:
            if eid in self.active or b in self.parents:
                raise ValueError('Duplicate active edge or multiple active parents')
            e = self.edges[eid]['properties']
            if [a, b] != [e['from_node_id'], e['to_node_id']] or not e['is_tree_edge']:
                raise ValueError('Active edge orientation/flag mismatch')
            u, v = self.nodes[a]['properties'], self.nodes[b]['properties']
            if u.get('source_node_id') != v.get('source_node_id') or e.get('source_node_id') != v.get('source_node_id'):
                raise ValueError('Active edge crosses station components')
            if u['node_type'] != 'substation' and u.get('feeder_id') != v.get('feeder_id'):
                raise ValueError('Active edge crosses feeder components')
            if e.get('feeder_id') != v.get('feeder_id'):
                raise ValueError('Active edge feeder mismatch')
            self.active.add(eid)
            self.children[a].append((b, eid))
            self.parents[b] = (a, eid)
        self.order, self.tin, self.tout, self.connected = [], {}, {}, set()
        for root in sorted(set(self.nodes) - set(self.parents)):
            source = self.nodes[root]['properties']['node_type'] == 'substation'
            stack = [(root, False)]
            while stack:
                node, exiting = stack.pop()
                if exiting:
                    self.tout[node] = len(self.order)
                    continue
                if node in self.tin:
                    raise ValueError('Cycle or repeated node in active tree')
                self.tin[node] = len(self.order)
                self.order.append(node)
                if source:
                    self.connected.add(node)
                    if self.nodes[node]['properties'].get('source_node_id') != root:
                        raise ValueError('Root source metadata mismatch')
                stack.append((node, True))
                for child, _ in sorted(self.children[node], reverse=True):
                    stack.append((child, False))
        if len(self.order) != len(self.nodes):
            raise ValueError('Active graph contains a rootless cycle')
        for fid, f in self.feeders.items():
            root = f['root_node_id']
            parent = self.parents.get(root)
            if root not in self.connected or not parent or parent[0] != f['substation_node_id']:
                raise ValueError('Exported feeder lacks direct active source association')
            if self.nodes[root]['properties']['feeder_id'] != fid:
                raise ValueError('Feeder root metadata mismatch')
        # O(1) branch-size checks let the protection search avoid materializing
        # every ancestor subtree for every event.
        self.subtree_metrics = {}
        for n in reversed(self.order):
            transformers = int(self.nodes[n]['properties']['node_type'] == 'transformer')
            metres, segments = 0., 0
            for child, eid in self.children[n]:
                ct, cm, cs = self.subtree_metrics[child]
                transformers += ct; metres += cm; segments += cs
                e = self.edges[eid]['properties']
                if e['edge_type'] in LINE_TYPES:
                    metres += e['length_m']; segments += 1
            self.subtree_metrics[n] = (transformers, metres, segments)

    def branch_metrics(self, kind, identity):
        root = self.edges[identity]['properties']['to_node_id'] if kind == 'edge' else identity
        t, metres, segments = self.subtree_metrics[root]
        if kind == 'edge' and self.edges[identity]['properties']['edge_type'] in LINE_TYPES:
            metres += self.edges[identity]['properties']['length_m']; segments += 1
        return t, metres / 1000, segments

    @classmethod
    def load(cls, directory):
        return cls(read(directory / 'network_nodes.geojson'), read(directory / 'network_edges.geojson'),
                   read(directory / 'active_tree.json'), read(directory / 'feeders.json'))

    def trace(self, kind, identity):
        if kind == 'edge':
            if identity not in self.active:
                raise ValueError('Fault edge is not in the active tree')
            ep = self.edges[identity]['properties']
            start, upstream = ep['to_node_id'], ep['from_node_id']
            selected_edges = {identity}
        elif kind == 'node':
            start = upstream = identity
            selected_edges = set()
        else:
            raise ValueError('Unknown source component kind')
        if start not in self.connected:
            raise ValueError('No modeled upstream supply association')
        nodes = self.order[self.tin[start]:self.tout[start]]
        edges = {self.parents[n][1] for n in nodes if n != start}
        edges |= selected_edges
        # A transformer attachment is a logical isolated branch, not a new line.
        if kind == 'node' and self.nodes[start]['properties']['node_type'] == 'transformer':
            if self.children[start]:
                raise ValueError('Transformer isolation requires terminal attachment')
            if start in self.parents:
                edges.add(self.parents[start][1])
                upstream = self.parents[start][0]
        upstream_nodes, upstream_edges = [], []
        while True:
            upstream_nodes.append(upstream)
            if upstream not in self.parents:
                break
            upstream, eid = self.parents[upstream]
            upstream_edges.append(eid)
        return sorted(nodes), sorted(edges), upstream_nodes, upstream_edges


class SpatialEligibility:
    """Exact boundary intersections, independent of stale asset label strings."""
    def __init__(self, graph, boundaries):
        features = boundaries['features']
        self.properties = {f['properties']['barangay_psgc']: f['properties'] for f in features}
        self.polygons = np.array([shape(f['geometry']) for f in features], dtype=object)
        if not shapely.is_valid(self.polygons).all():
            raise ValueError('Invalid barangay geometry')
        tree = STRtree(self.polygons)
        codes = [f['properties']['barangay_psgc'] for f in features]
        self.asset_barangays = {}
        self.candidates = defaultdict(lambda: {'line': [], 'transformer': []})
        self.local_disconnected = defaultdict(int)
        for kind, assets in [('node', graph.nodes), ('edge', graph.edges)]:
            ids = sorted(assets)
            geometries = np.array([shape(assets[i]['geometry']) for i in ids], dtype=object)
            pairs = tree.query(geometries, predicate='intersects')
            # Polygon-touch alone is not enough to place a line in a barangay.
            for ai, pi in pairs.T:
                identity, geom = ids[ai], geometries[ai]
                if geom.geom_type != 'Point' and geom.intersection(self.polygons[pi]).length <= 1e-12:
                    continue
                code = codes[pi]
                self.asset_barangays.setdefault((kind, identity), set()).add(code)
                p = assets[identity]['properties']
                active = (identity in graph.connected if kind == 'node' else
                          identity in graph.active and p['to_node_id'] in graph.connected)
                eligible_type = ('transformer' if kind == 'node' and p['node_type'] == 'transformer' and not graph.children[identity]
                                 else 'line' if kind == 'edge' and p['edge_type'] in LINE_TYPES else None)
                if eligible_type:
                    if active and p.get('feeder_id') in graph.feeders:
                        self.candidates[code][eligible_type].append(identity)
                    elif not active:
                        self.local_disconnected[code] += 1
        self.length_validation = self.validate_lengths(graph)

    @staticmethod
    def validate_lengths(graph):
        ids = [eid for eid in sorted(graph.active) if graph.edges[eid]['properties']['edge_type'] in LINE_TYPES]
        geoms = np.array([shape(graph.edges[i]['geometry']) for i in ids], dtype=object)
        def convert(x, y):
            return project('EPSG:4326', 'EPSG:32651', x, y)
        lengths = shapely.length(shapely.transform(geoms, convert, interleaved=False))
        stored = np.array([graph.edges[i]['properties']['length_m'] for i in ids], float)
        if not np.isfinite(stored).all() or (stored < 0).any():
            raise ValueError('Invalid stored distribution line lengths')
        delta = np.abs(lengths - stored)
        if (delta > np.maximum(1., .01 * lengths)).any():
            raise ValueError('Stored line length differs from UTM51N geometry by >1m/1%')
        return {'crs': 'EPSG:32651', 'active_distribution_segments_checked': len(ids),
                'max_absolute_error_m': float(delta.max()), 'sum_stored_m': float(stored.sum()),
                'sum_projected_m': float(lengths.sum())}


def assign_source(event, graph, spatial, seed, weights, aligned_origin=False):
    code = str(event['barangay_psgc'])
    props = spatial.properties.get(code)
    if not props or any(str(event[k]) != str(props[k]) for k in ['barangay', 'municipality', 'municipality_psgc']):
        raise ValueError('Outage origin does not match authoritative geography')
    pool = spatial.candidates[code]
    if aligned_origin and event['cause_category'] != 'Supply Interruption':
        pool = {**pool, 'line': [eid for eid in pool['line'] if code in spatial.asset_barangays.get(
            ('node', graph.edges[eid]['properties']['from_node_id']), set())]}
    # Event-ID stable streams make replay independent of input row ordering.
    hashed = int.from_bytes(hashlib.sha256(event['outage_id'].encode()).digest()[:16], 'big')
    rng = np.random.default_rng(np.random.SeedSequence([seed, hashed]))
    counts = {k: len(v) for k, v in pool.items()}
    if not any(counts.values()):
        return {'resolution_status': 'unresolved', 'unresolved_reason': 'local_assets_supply_disconnected' if spatial.local_disconnected[code] else 'no_eligible_local_connected_assets',
                'eligible_local_lines': counts['line'], 'eligible_local_transformers': counts['transformer']}
    if event['cause_category'] == 'Supply Interruption':
        feeder_ids = sorted({(graph.nodes[i] if k == 'transformer' else graph.edges[i])['properties']['feeder_id']
                            for k in pool for i in pool[k]})
        fid = str(rng.choice(feeder_ids))
        root = graph.feeders[fid]['root_node_id']
        identity = graph.parents[root][1]
        kind, scenario, mechanism = 'edge', 'modeled_feeder_source_loss', 'upstream feeder loss; mechanism unspecified'
        method = 'uniform_connected_feeders_serving_origin_assets'
        candidate_count = len(feeder_ids)
        local_kind = 'feeder_source'
    else:
        if event['cause_category'] not in weights:
            raise ValueError('Cause missing from assignment configuration')
        types = [k for k in ['line', 'transformer'] if counts[k] and weights[event['cause_category']][k] > 0]
        if not types:
            return {'resolution_status': 'unresolved', 'unresolved_reason': 'no_candidate_with_positive_cause_weight',
                    'eligible_local_lines': counts['line'], 'eligible_local_transformers': counts['transformer']}
        p = np.array([weights[event['cause_category']][k] for k in types])
        local_kind = str(rng.choice(types, p=p / p.sum()))
        identity = str(rng.choice(sorted(pool[local_kind])))
        kind = 'node' if local_kind == 'transformer' else 'edge'
        fid = (graph.nodes[identity] if kind == 'node' else graph.edges[identity])['properties']['feeder_id']
        scenario = 'isolated_transformer_branch' if kind == 'node' else 'isolated_line_downstream'
        mechanism = 'unspecified' if event['cause_category'] in {'Other', 'Unknown'} else 'synthetic cause-informed source type'
        method, candidate_count = 'cause_weighted_type_then_uniform_local_asset', counts[local_kind]
    asset = graph.nodes[identity] if kind == 'node' else graph.edges[identity]
    return {'resolution_status': 'resolved_modeled', 'unresolved_reason': None,
            'fault_source_asset_id': asset['properties'].get('asset_id', identity),
            'fault_source_topology_id': identity, 'fault_source_kind': kind,
            'fault_source_asset_type': local_kind, 'feeder_id': fid,
            'substation_node_id': graph.feeders[fid]['substation_node_id'],
            'substation_asset_id': graph.feeders[fid].get('substation_asset_id'),
            'substation_name': graph.feeders[fid].get('substation_name'),
            'assignment_method': method, 'assignment_uncertainty': 'synthetic_spatial_and_topology_proxy; no verified fault location',
            'impact_scenario': scenario,
            'mechanism_assumption': mechanism, 'selected_type_candidate_count': candidate_count,
            'eligible_local_lines': counts['line'], 'eligible_local_transformers': counts['transformer']}


def choose_isolation(graph, assignment, policy=ProtectionPolicy(), eligible_edges=None):
    """Choose the nearest adequate upstream branch within the SAME active feeder.

    Targets are soft; hop/distance/size/source boundaries are hard expansion
    limits. Sparse exceptions stay explicitly below target, never invented.
    """
    kind, identity = assignment['fault_source_kind'], assignment['fault_source_topology_id']
    original_t, original_km, _ = graph.branch_metrics(kind, identity)
    def enough(t, km):
        return t >= policy.min_transformers and km >= policy.min_line_km
    chosen_kind, chosen_id = kind, identity
    hops, distance = 0, 0.
    reason = 'already_meets_target'
    if assignment['fault_source_asset_type'] == 'feeder_source':
        reason = 'existing_feeder_source_scenario'
    elif not enough(original_t, original_km):
        reason = 'active_feeder_boundary'
        current = graph.edges[identity]['properties']['to_node_id'] if kind == 'edge' else identity
        # Skip the already selected line edge. Transformer attachments are
        # traversed but do not count as route distance or a physical red line.
        if kind == 'edge':
            current = graph.edges[identity]['properties']['from_node_id']
        examined_hops, examined_distance = 0, 0.
        while current in graph.parents:
            parent, eid = graph.parents[current]
            if graph.nodes[parent]['properties']['node_type'] == 'substation':
                break  # A local fault is not promoted to an entire feeder loss.
            e = graph.edges[eid]['properties']
            if e.get('feeder_id') != assignment['feeder_id'] or eid not in graph.active:
                raise ValueError('Protection search left the active feeder')
            examined_hops += 1
            examined_distance += max(0., e['length_m']) / 1000
            if examined_hops > policy.max_upstream_hops:
                reason = 'upstream_hop_limit'; break
            if examined_distance > policy.max_upstream_km:
                reason = 'upstream_distance_limit'; break
            t, km, segments = graph.branch_metrics('edge', eid)
            if t > policy.max_expanded_transformers or km > policy.max_expanded_line_km:
                reason = 'expanded_size_limit'; break
            if segments and (eligible_edges is None or eid in eligible_edges):
                chosen_kind, chosen_id = 'edge', eid
                hops, distance = examined_hops, examined_distance
                if enough(t, km):
                    reason = 'target_met'; break
            current = parent
    t, km, _ = graph.branch_metrics(chosen_kind, chosen_id)
    expanded = (chosen_kind, chosen_id) != (kind, identity)
    return {'assumed_isolation_kind': chosen_kind, 'assumed_isolation_component_id': chosen_id,
            'isolation_expanded': expanded, 'protection_target_met': enough(t, km),
            'protection_limit_reason': reason, 'isolation_upstream_hops': hops,
            'isolation_upstream_distance_km': distance,
            'source_only_transformer_count': original_t, 'source_only_line_length_km': original_km,
            'protection_policy': 'bounded_upstream_local_branch_v1'}


def choose_branch_source(event, graph, spatial, assignment, policy=ProtectionPolicy()):
    """Select one source at the start of the downstream loss, preserving origin geography.

    A small sampled branch may be replaced by an upstream source. There is no
    additional isolation location: traversal and the ripple use this final source.
    """
    code = str(event['barangay_psgc'])
    eligible = {eid for eid in spatial.candidates[code]['line'] if code in spatial.asset_barangays.get(
        ('node', graph.edges[eid]['properties']['from_node_id']), set())}
    selected = choose_isolation(graph, assignment, policy, eligible)
    kind, identity = selected['assumed_isolation_kind'], selected['assumed_isolation_component_id']
    asset = graph.edges[identity] if kind == 'edge' else graph.nodes[identity]
    promoted = selected['isolation_expanded']
    return {
        'initial_source_topology_id': assignment['fault_source_topology_id'],
        'initial_source_kind': assignment['fault_source_kind'],
        'initial_source_asset_type': assignment['fault_source_asset_type'],
        'fault_source_kind': kind, 'fault_source_topology_id': identity,
        'fault_source_asset_id': asset['properties'].get('asset_id', identity),
        'fault_source_asset_type': 'line' if promoted else assignment['fault_source_asset_type'],
        'source_anchor_node_id': asset['properties']['from_node_id'] if kind == 'edge' else identity,
        'source_promoted_upstream': promoted,
        'source_target_met': selected['protection_target_met'],
        'source_selection_limit_reason': selected['protection_limit_reason'],
        'source_upstream_hops': selected['isolation_upstream_hops'],
        'source_upstream_distance_km': selected['isolation_upstream_distance_km'],
        'impact_scenario': 'upstream_branch_source' if promoted else assignment['impact_scenario'],
        'assignment_method': 'origin_eligible_upstream_branch_source' if promoted else assignment['assignment_method'],
    }


def impact_set(graph, spatial, assignment):
    kind, identity = assignment['fault_source_kind'], assignment['fault_source_topology_id']
    nodes, edges, upstream_nodes, upstream_edges = graph.trace(kind, identity)
    if (kind == 'node' and identity not in nodes) or (kind == 'edge' and identity not in edges):
        raise ValueError('Expanded impact no longer contains the original fault')
    fid = assignment['feeder_id']
    if any(graph.nodes[n]['properties'].get('feeder_id') != fid for n in nodes):
        raise ValueError('Impact propagates across feeders')
    if any(graph.edges[e]['properties'].get('feeder_id') != fid for e in edges):
        raise ValueError('Affected edge in unrelated feeder')
    if not set(edges) <= graph.active:
        raise ValueError('Inactive edge reached')
    transformer_count = sum(graph.nodes[n]['properties']['node_type'] == 'transformer' for n in nodes)
    pole_count = sum(graph.nodes[n]['properties']['node_type'] == 'pole' for n in nodes)
    line_edges = [e for e in edges if graph.edges[e]['properties']['edge_type'] in LINE_TYPES]
    barangays = sorted(set().union(*(spatial.asset_barangays.get(('node', n), set()) for n in nodes),
                                   *(spatial.asset_barangays.get(('edge', e), set()) for e in edges)))
    return {'affected_node_ids': nodes, 'affected_edge_ids': edges, 'upstream_node_ids': upstream_nodes,
            'upstream_edge_ids': upstream_edges, 'affected_barangay_psgcs': barangays,
            'affected_line_segment_count': len(line_edges),
            'affected_line_length_km': sum(graph.edges[e]['properties']['length_m'] for e in line_edges) / 1000,
            'affected_pole_count': pole_count, 'affected_transformer_count': transformer_count,
            'affected_feeder_ids': [fid], 'affected_barangay_count': len(barangays),
            'impact_is_synthetic': True, 'is_verified_connectivity': False,
            'large_modeled_impact': transformer_count >= 1000 or len(barangays) >= 20,
            'no_downstream_transformers': transformer_count == 0}
