"""Move small simulated impact sources to nearby ancestors; preserve events.

Run: uv run python scripts/expand_small_impacts.py
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.impact_model import ImpactGraph, SpatialEligibility, ProtectionPolicy, impact_set, read

POLICY = ProtectionPolicy(min_transformers=3, min_line_km=.2, max_upstream_hops=30,
                          max_upstream_km=.75, max_expanded_transformers=100,
                          max_expanded_line_km=5.)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def select_ancestor(graph, kind, identity, feeder, policy=POLICY):
    """Nearest adequate ancestor, with bounded optional enlargement.

    Zero-line branches must reach the nearest ancestor containing actual
    distribution lines, even if that minimum step exceeds a soft size budget.
    Such unavoidable steps are reported, never split into invented topology.
    """
    t0, km0, segments0 = graph.branch_metrics(kind, identity)
    adequate = lambda t, km, segments: segments > 0 and t >= policy.min_transformers and km >= policy.min_line_km
    result = {'kind': kind, 'identity': identity, 'hops': 0, 'km': 0.,
              'reason': 'already_meets_target', 'minimum_physical_branch_exception': False}
    if adequate(t0, km0, segments0):
        return result
    current = graph.edges[identity]['properties']['from_node_id'] if kind == 'edge' else identity
    hops, distance = 0, 0.
    result['reason'] = 'no_eligible_upstream_branch'
    for _ in range(len(graph.nodes)):
        if current not in graph.parents:
            break
        parent, eid = graph.parents[current]
        e = graph.edges[eid]['properties']
        if eid not in graph.active or e['feeder_id'] != feeder:
            raise ValueError('Upstream adjustment left the active feeder')
        hops += 1
        distance += max(0., e['length_m']) / 1000
        t, km, segments = graph.branch_metrics('edge', eid)
        station_edge = graph.nodes[parent]['properties']['node_type'] == 'substation'
        in_budget = (hops <= policy.max_upstream_hops and distance <= policy.max_upstream_km
                     and t <= policy.max_expanded_transformers and km <= policy.max_expanded_line_km
                     and not station_edge)
        if segments and (in_budget or (segments0 == 0 and result['identity'] == identity)):
            result = {'kind': 'edge', 'identity': eid, 'hops': hops, 'km': distance,
                      'reason': 'target_met' if adequate(t, km, segments) else 'best_nearby_branch',
                      'minimum_physical_branch_exception': not in_budget}
            if adequate(t, km, segments) or not in_budget:
                return result
        if not in_budget and segments0 > 0:
            result['reason'] = 'bounded_upstream_limit'
            break
        if station_edge:
            break
        current = parent
    if segments0 == 0 and graph.branch_metrics(result['kind'], result['identity'])[2] == 0:
        raise ValueError(f'Feeder {feeder} has no upstream physical distribution branch for {identity}')
    return result


def main():
    data = ROOT / 'data'
    pointer = data / 'active_bundle.json'
    previous = read(pointer)
    if previous.get('impact_revision') == 'nearby-upstream-small-branches-v1':
        raise ValueError('This impact revision is already active')
    old = data / previous['impacts']
    manifest = read(old / 'run_manifest.json')
    for item in manifest['inputs'].values():
        assert digest(item['path']) == item['sha256'], 'Stale source bundle'
    events = pd.read_csv(data / previous['events'], dtype={'barangay_psgc': str, 'municipality_psgc': str})
    event_hash = digest(data / previous['events'])
    mapping = pd.read_parquet(old / 'outage_fault_mapping.parquet')
    before = pd.read_parquet(old / 'outage_impact_summary.parquet')
    print('Loading active topology and barangay intersections...', flush=True)
    graph = ImpactGraph.load(data / 'topology/react_data')
    spatial = SpatialEligibility(graph, read(data / 'topology/react_data/barangays.geojson'))
    print('Selecting nearby upstream sources for small branches...', flush=True)
    selections, sets, members, relationships, assignments = {}, {}, [], [], []
    for a in mapping.to_dict('records'):
        kind, identity = a['fault_source_kind'], a['fault_source_topology_id']
        key = (kind, identity, a['feeder_id'])
        if key not in selections:
            selections[key] = select_ancestor(graph, *key)
        selected = selections[key]
        moved = (selected['kind'], selected['identity']) != (kind, identity)
        a['previous_source_kind'], a['previous_source_topology_id'] = kind, identity
        a['minimum_physical_branch_exception'] = selected['minimum_physical_branch_exception']
        a['source_promoted_upstream'] = moved
        a['source_upstream_hops'], a['source_upstream_distance_km'] = selected['hops'], selected['km']
        a['source_selection_limit_reason'] = selected['reason']
        if moved:
            a['fault_source_kind'], a['fault_source_topology_id'] = selected['kind'], selected['identity']
            asset = graph.edges[selected['identity']]['properties']
            station_edge = graph.nodes[asset['from_node_id']]['properties']['node_type'] == 'substation'
            a.update(fault_source_asset_id=asset.get('asset_id', selected['identity']),
                     fault_source_asset_type='feeder_source' if station_edge else 'line',
                     source_anchor_node_id=asset['from_node_id'],
                     impact_scenario='upstream_branch_source', assignment_method='nearby_active_ancestor_branch')
        t, km, segments = graph.branch_metrics(a['fault_source_kind'], a['fault_source_topology_id'])
        a['source_target_met'] = t >= POLICY.min_transformers and km >= POLICY.min_line_km
        a['source_anchor_outside_origin'] = a['origin_barangay_psgc'] not in spatial.asset_barangays.get(('node', a['source_anchor_node_id']), set())
        source_key = f"{a['fault_source_kind']}:{a['fault_source_topology_id']}:{a['impact_scenario']}"
        sid = 'impact-' + hashlib.sha256(source_key.encode()).hexdigest()[:24]
        a['impact_set_id'] = sid
        if sid not in sets:
            impact = impact_set(graph, spatial, a)
            assert impact['affected_line_segment_count'] > 0
            impact['impact_set_id'] = sid
            sets[sid] = impact
            for kind, field in [('node', 'affected_node_ids'), ('edge', 'affected_edge_ids')]:
                members += [{'impact_set_id': sid, 'topology_kind': kind, 'topology_id': identity,
                             'relationship': 'modeled_loss_of_supply',
                             'is_simulated_fault_source': kind == a['fault_source_kind'] and identity == a['fault_source_topology_id']}
                            for identity in impact[field]]
        for code in sets[sid]['affected_barangay_psgcs']:
            relationships.append({'outage_id': a['outage_id'], 'impact_set_id': sid, 'barangay_psgc': code,
                                  **{k: v for k, v in spatial.properties[code].items() if k != 'barangay_psgc'},
                                  'is_origin_barangay': code == a['origin_barangay_psgc'],
                                  'relationship': 'contains_affected_modeled_infrastructure; not_whole_barangay_outage'})
        assignments.append(a)
    mapping = pd.DataFrame(assignments)
    sets_frame = pd.DataFrame(sets.values())
    fields = ['impact_set_id', 'affected_line_segment_count', 'affected_line_length_km', 'affected_pole_count',
              'affected_transformer_count', 'affected_feeder_ids', 'affected_barangay_count', 'large_modeled_impact', 'no_downstream_transformers']
    summary = events[['outage_id', 'municipality_psgc', 'municipality', 'barangay_psgc', 'barangay',
                      'start_time', 'end_time', 'duration_minutes', 'cause_category']].merge(mapping, on='outage_id', validate='one_to_one')
    summary = summary.merge(sets_frame[fields], on='impact_set_id', how='left', validate='many_to_one')
    assert len(summary) == len(events) and summary.affected_line_segment_count.gt(0).all()
    assert summary.barangay_psgc.eq(summary.origin_barangay_psgc).all()
    assert summary.resolution_status.eq('resolved_modeled').all()
    assert digest(data / previous['events']) == event_hash
    after = summary.set_index('outage_id'); prior = before.set_index('outage_id')
    assert np.allclose(after.duration_minutes.sort_index(), prior.duration_minutes.sort_index())
    assert (after.affected_line_length_km >= prior.affected_line_length_km).all()
    assert (after.affected_transformer_count >= prior.affected_transformer_count).all()
    validation = {**manifest['validation'], 'zero_line_events_before': int(before.affected_line_segment_count.eq(0).sum()),
                  'zero_line_events_after': 0, 'promoted_events': int(mapping.source_promoted_upstream.sum()),
                  'small_events_before': int((before.affected_transformer_count.lt(3) | before.affected_line_length_km.lt(.2)).sum()),
                  'small_events_after': int((summary.affected_transformer_count.lt(3) | summary.affected_line_length_km.lt(.2)).sum()),
                  'minimum_physical_branch_exceptions': int(mapping.minimum_physical_branch_exception.sum()),
                  'source_anchor_outside_origin_events': int(mapping.source_anchor_outside_origin.sum()),
                  'maximum_upstream_hops': int(mapping.source_upstream_hops.max()),
                  'maximum_upstream_distance_km': float(mapping.source_upstream_distance_km.max()),
                  'shared_impact_sets': len(sets), 'event_file_unchanged': True}
    output = data / 'impacts' / ('v3_upstream_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
    output.mkdir()
    mapping.to_parquet(output / 'outage_fault_mapping.parquet', index=False)
    mapping.to_csv(output / 'outage_fault_mapping.csv', index=False)
    sets_frame.to_parquet(output / 'impact_sets.parquet', index=False)
    pd.DataFrame(members).to_parquet(output / 'affected_assets.parquet', index=False)
    pd.DataFrame(relationships).to_parquet(output / 'outage_affected_barangays.parquet', index=False)
    summary.to_parquet(output / 'outage_impact_summary.parquet', index=False)
    summary.drop(columns='affected_feeder_ids').to_csv(output / 'outage_impact_summary.csv', index=False)
    manifest.update(validation=validation, source_selection_policy=asdict(POLICY), revision_source=str(old),
                    upstream_revision_script_sha256=digest(__file__))
    (output / 'run_manifest.json').write_text(json.dumps(manifest, indent=2))
    (output / 'validation.json').write_text(json.dumps(validation, indent=2))
    (output / 'previous_bundle.json').write_text(json.dumps(previous, indent=2))
    (output / 'methodology.md').write_text('Small synthetic footprints move to the nearest adequate active ancestor in the same feeder. '
        'Soft targets: 3 transformers and 200 m distribution lines; optional movement limited to 30 hops/750 m, 100 transformers/5 km. '
        'Zero-line cases must reach the nearest ancestor containing real distribution segments; unavoidable larger jumps are explicitly flagged. '
        'Origin labels remain unchanged; an upstream source may cross a barangay boundary. No new network links or disconnected assets are added. '
        'Previous source IDs, movement distance and exceptions remain in each assignment. Event durations and onset labels are unchanged.', encoding='utf-8')
    bundle = {**previous, 'impacts': output.relative_to(data).as_posix(), 'impact_revision': 'nearby-upstream-small-branches-v1'}
    temp = data / 'active_bundle.upstream.tmp'
    temp.write_text(json.dumps(bundle, indent=2))
    os.replace(temp, pointer)
    print(json.dumps({'output': str(output), 'validation': validation}, indent=2), flush=True)


if __name__ == '__main__':
    main()
