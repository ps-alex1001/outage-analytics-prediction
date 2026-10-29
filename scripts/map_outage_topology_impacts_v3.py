"""Precompute V3 event-to-topology impacts without regenerating either input.

Run from the repository root: uv run python scripts/map_outage_topology_impacts.py
"""
from pathlib import Path
import argparse
from dataclasses import asdict
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.impact_model import DEFAULT_WEIGHTS, ProtectionPolicy, ImpactGraph, SpatialEligibility, assign_source as original_assign_source, choose_branch_source, impact_set, read

EVENTS = ROOT / 'data/synthetic/v3_20261029T115229_718511Z/synthetic_outage_events_v3.csv'
TOPOLOGY = ROOT / 'data/topology/react_data'
OUTPUT = ROOT / 'data/impacts/v3_topology_1_0_0'


CAUSE_ADAPTER = {'Weather':'Weather / Storm', 'Tree contact':'Tree Contact',
    'Equipment Failure':'Equipment / Technical', 'Supply':'Supply Interruption',
    'Animal':'Animal Interference', 'Other':'Other'}


def assign_source(event, *args, **kwargs):
    # Change the selection-rule key only; exported event taxonomy stays V3.
    return original_assign_source({**event, 'cause_category': CAUSE_ADAPTER[event['cause_category']]}, *args, **kwargs)


def fingerprints(paths):
    result = {}
    for k, p in paths.items():
        with p.open('rb') as f:
            result[k] = {'path': str(p), 'sha256': hashlib.file_digest(f, 'sha256').hexdigest(), 'bytes': p.stat().st_size}
    return result


def generate(output, seed=20261025, weights=None, policy=ProtectionPolicy(min_transformers=1, min_line_km=0, max_upstream_hops=0, max_upstream_km=0)):
    weights = weights or DEFAULT_WEIGHTS
    assert seed >= 0
    assert not output.exists(), 'Refusing to overwrite a completed impact run'
    assert output.resolve().is_relative_to((ROOT / 'data/impacts').resolve())
    paths = {'outages': EVENTS, **{name: TOPOLOGY / name for name in ['network_nodes.geojson', 'network_edges.geojson', 'active_tree.json',
             'feeders.json', 'substations.geojson', 'barangays.geojson', 'dataset_info.json']}}
    before = fingerprints(paths)
    print('Loading the current exported active network...', flush=True)
    graph = ImpactGraph.load(TOPOLOGY)
    print('Intersecting assets with barangay boundaries and validating metric lengths...', flush=True)
    spatial = SpatialEligibility(graph, read(TOPOLOGY / 'barangays.geojson'))
    events = pd.read_csv(EVENTS, dtype={'barangay_psgc': str, 'municipality_psgc': str})
    assert events.outage_id.is_unique and events.generator_version.eq('v3').all()
    assignments, sets, members, barangay_rows = [], {}, [], []
    alternate_changed = resolved = 0
    for event in events.sort_values('outage_id').to_dict('records'):
        a = assign_source(event, graph, spatial, seed, weights, aligned_origin=True)
        assert a == assign_source(event, graph, spatial, seed, weights, aligned_origin=True), 'Seed replay failed'
        alt = assign_source(event, graph, spatial, seed + 1, weights, aligned_origin=True)
        alternate_changed += a.get('fault_source_topology_id') != alt.get('fault_source_topology_id')
        a['outage_id'] = event['outage_id']
        a['origin_barangay_psgc'], a['origin_municipality_psgc'] = event['barangay_psgc'], event['municipality_psgc']
        a['random_seed'], a['impact_generator_version'] = seed, '3.0.0'
        a.pop('assumed_isolation_component_id', None)
        if a['resolution_status'] == 'resolved_modeled':
            resolved += 1
            a.update(choose_branch_source(event, graph, spatial, a, policy))
            if a['fault_source_asset_type'] != 'feeder_source':
                assert event['barangay_psgc'] in spatial.asset_barangays[('node', a['source_anchor_node_id'])]
            key = f"{a['fault_source_kind']}:{a['fault_source_topology_id']}:{a['impact_scenario']}"
            sid = 'impact-' + hashlib.sha256(key.encode()).hexdigest()[:24]
            a['impact_set_id'] = sid
            if sid not in sets:
                impact = impact_set(graph, spatial, a)
                impact['impact_set_id'] = sid
                sets[sid] = impact
                for kind, keyname in [('node', 'affected_node_ids'), ('edge', 'affected_edge_ids')]:
                    for identity in impact[keyname]:
                        members.append({'impact_set_id': sid, 'topology_kind': kind, 'topology_id': identity,
                                        'relationship': 'modeled_loss_of_supply',
                                        'is_simulated_fault_source': kind == a['fault_source_kind'] and identity == a['fault_source_topology_id']})
            impact = sets[sid]
            for code in impact['affected_barangay_psgcs']:
                barangay_rows.append({'outage_id': event['outage_id'], 'impact_set_id': sid, 'barangay_psgc': code,
                                      **{k: v for k, v in spatial.properties[code].items() if k != 'barangay_psgc'},
                                      'is_origin_barangay': code == event['barangay_psgc'],
                                      'relationship': 'contains_affected_modeled_infrastructure; not_whole_barangay_outage'})
        else:
            a['impact_set_id'] = None
        assignments.append(a)
        if len(assignments) % 2000 == 0:
            print(f'Assigned {len(assignments):,}/{len(events):,} events; {len(sets):,} shared impact sets', flush=True)
    mapping = pd.DataFrame(assignments)
    sets_frame = pd.DataFrame(sets.values()).sort_values('impact_set_id')
    fields = ['impact_set_id', 'affected_line_segment_count', 'affected_line_length_km', 'affected_pole_count',
              'affected_transformer_count', 'affected_feeder_ids', 'affected_barangay_count', 'large_modeled_impact', 'no_downstream_transformers']
    summary = events[['outage_id', 'municipality_psgc', 'municipality', 'barangay_psgc', 'barangay', 'start_time',
                      'end_time', 'duration_minutes', 'cause_category']].merge(mapping, on='outage_id', validate='one_to_one')
    summary = summary.merge(sets_frame[fields], on='impact_set_id', how='left', validate='many_to_one')
    assert len(summary) == len(events)
    # Unresolved impact metrics remain null, never a misleading zero.
    assert summary.loc[summary.resolution_status.eq('unresolved'), 'affected_transformer_count'].isna().all()
    assert all(set(s['affected_edge_ids']) <= graph.active for s in sets.values())
    after = fingerprints(paths)
    assert before == after, 'Original event/topology files changed'
    output.mkdir(parents=True, exist_ok=False)
    mapping.to_csv(output / 'outage_fault_mapping.csv', index=False)
    mapping.to_parquet(output / 'outage_fault_mapping.parquet', index=False)
    sets_frame.to_parquet(output / 'impact_sets.parquet', index=False)
    pd.DataFrame(members).to_parquet(output / 'affected_assets.parquet', index=False)
    pd.DataFrame(barangay_rows).to_parquet(output / 'outage_affected_barangays.parquet', index=False)
    summary.to_parquet(output / 'outage_impact_summary.parquet', index=False)
    summary.drop(columns=['affected_feeder_ids']).to_csv(output / 'outage_impact_summary.csv', index=False)
    eligible = []
    for code in sorted(spatial.properties):
        eligible.append({**spatial.properties[code], 'connected_line_candidates': len(spatial.candidates[code]['line']),
                         'connected_transformer_candidates': len(spatial.candidates[code]['transformer']),
                         'disconnected_local_candidates': spatial.local_disconnected[code]})
    pd.DataFrame(eligible).to_csv(output / 'barangay_candidate_audit.csv', index=False)
    measures = ['affected_line_segment_count', 'affected_line_length_km', 'affected_pole_count', 'affected_transformer_count', 'affected_barangay_count']
    summary[measures].describe(percentiles=[.5, .9, .95, .99]).to_csv(output / 'impact_size_distribution.csv')
    summary.groupby(['cause_category', 'resolution_status']).size().rename('events').to_csv(output / 'resolution_by_cause.csv')
    large = summary.loc[summary.large_modeled_impact.eq(True)]
    large.to_parquet(output / 'large_impact_review.parquet', index=False)
    report = {'total_outages': len(events), 'mapped_outages': resolved, 'mapped_percentage': 100 * resolved / len(events),
              'unresolved_reasons': dict(Counter(mapping.loc[mapping.resolution_status.eq('unresolved'), 'unresolved_reason'])),
              'shared_impact_sets': len(sets), 'normalized_asset_memberships': len(members),
              'outage_barangay_relationships': len(barangay_rows), 'active_edges_only': True,
              'same_seed_identical_assignments': True, 'alternate_seed_changed_assignments': alternate_changed,
              'large_modeled_outages': len(large),
              'resolved_events_with_no_downstream_transformers': int(summary.no_downstream_transformers.eq(True).sum()),
              'disconnected_network_nodes_excluded': len(graph.nodes) - len(graph.connected),
              'disconnected_transformers_excluded': sum(f['properties']['node_type'] == 'transformer' for i, f in graph.nodes.items() if i not in graph.connected),
              'length_validation': spatial.length_validation, 'source_hashes_unchanged': before == after}
    report['upstream_source_promotions'] = int(mapping.source_promoted_upstream.eq(True).sum())
    report['resolved_events_below_source_target'] = int(mapping.source_target_met.eq(False).sum())
    report['resolved_events_with_no_distribution_lines'] = int(summary.affected_line_segment_count.eq(0).sum())
    previous = output / 'not_a_previous_v3_run.parquet'
    if previous.is_file():
        old = pd.read_parquet(previous)
        comparison = old[['outage_id', 'affected_transformer_count', 'affected_line_length_km', 'fault_source_topology_id']].merge(
            summary[['outage_id', 'affected_transformer_count', 'affected_line_length_km', 'fault_source_topology_id']],
            on='outage_id', suffixes=('_before', '_after'), validate='one_to_one')
        report['changed_source_assignments'] = int(comparison.fault_source_topology_id_before.fillna('').ne(comparison.fault_source_topology_id_after.fillna('')).sum())
        comparison.to_csv(output / 'impact_size_comparison.csv', index=False)
        report['previous_zero_line_events'] = int(old.affected_line_segment_count.eq(0).sum())
        report['previous_median_transformers'] = float(old.affected_transformer_count.median())
        report['previous_median_line_km'] = float(old.affected_line_length_km.median())
        report['new_median_transformers'] = float(summary.affected_transformer_count.median())
        report['new_median_line_km'] = float(summary.affected_line_length_km.median())
    config = {'generator_version': '3.0.0', 'seed': seed, 'cause_type_weights': weights,
              'source_selection_policy': asdict(policy), 'cause_adapter': CAUSE_ADAPTER, 'branch_promotion_default': 'disabled; retain natural downstream footprint',
              'sampling': 'cause-weighted type then uniform within origin-anchored connected assets; bounded geographic upstream source promotion; supply uniform among serving modeled feeders',
              'line_types_counted': sorted(__import__('backend.impact_model', fromlist=['LINE_TYPES']).LINE_TYPES),
              'crs_export': 'EPSG:4326', 'crs_length_validation': 'EPSG:32651', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'inputs': before, 'code': fingerprints({'model': ROOT / 'backend/impact_model.py', 'pipeline': Path(__file__)}),
              'validation': report}
    (output / 'run_manifest.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    (output / 'methodology.md').write_text(f'''# Synthetic outage-to-topology impact mapping

V3 default policy disables upstream promotion (zero hops). The inherited discussion below describes the optional expansion mechanism only. Natural downstream extent is retained, including small/zero-line impacts.\n\nV3 and the imported `react_data` topology are read-only inputs. This step does not regenerate outages, infrastructure or the environmental propensity formula. All fault assignments and loss-of-supply scenarios are synthetic, with unverified connectivity/protection assumptions. No customer or surface-area estimates are produced.

## Assignment and geography

Assets must geometrically intersect the original event barangay polygon and belong to an active feeder connected to an exported modeled station. Point contacts of line endpoints alone are insufficient. Nodes on a shared boundary can be eligible in both polygons. Exact polygon membership preserves PSGC keys and avoids ambiguous asset label strings. Assets are never chosen merely as nearest neighbors, and origin municipality/barangay values are never changed.

Cause weights choose line versus transformer type, then select uniformly among geographically eligible members of that type. Defaults: Tree 9:1, Weather 7:3, Equipment 1:9, Animal 3:7, Other and Unknown 1:1. Available-type weights renormalize if one pool is empty. These are configurable assumptions in the JSON weights input; they do not establish a real failure mechanism. Supply Interruption chooses uniformly among valid modeled feeders with connected eligible assets in the origin polygon, and assigns that feeder's active station-departure edge as its upstream source-loss point. It does not fail the whole substation or unrelated feeders. Event-ID-hashed NumPy seed streams make assignment reproducible and independent of input row order.

## Source selection and traversal

The directed active tree is authoritative. Version 1.2 selects one source at the start of the affected downstream branch. Local line sources must intersect the original event barangay and have their upstream anchor node inside that polygon. Small sampled branches can be replaced by the nearest admissible upstream line source reaching {policy.min_transformers} transformers and {policy.min_line_km:g} km of lines. Search stays within the same active feeder and original barangay, limited to {policy.max_upstream_hops} steps, {policy.max_upstream_km:g} km upstream travel, {policy.max_expanded_transformers} transformers and {policy.max_expanded_line_km:g} km total line length. Already-large sources and feeder-source scenarios are preserved. Targets are soft: the largest admissible branch found is retained if the target cannot be met. Initial sampled source IDs are retained only as provenance. They are not additional faults or map markers.

The final source is the sole traversal origin. There is no separate upstream cut. The ripple is anchored at the selected edge's from-node, or the selected transformer point. It shows a simulated starting location, not timed propagation. A translucent screen-space halo follows actual affected lines for visibility and is not measured coverage or an area polygon. Logical transformer attachments remain zero route length; sparse exceptions can still have no physical line. Unresolved metrics stay null. No disconnected assets or geometric crossings create supply paths.

## Measurements and barangays

Distribution counts/length include distribution_line_section, road_evidenced_extension and inferred_local_transfer. Logical transformer attachments have zero route length. Station leads/junction connectors participate in connectivity but are not counted as distribution-line segments. Every active distribution length is checked against EPSG:32651 projected geometry to a maximum 1m or 1% tolerance, then stored source lengths are summed once per unique edge. Export geometries remain EPSG:4326.

Affected barangays are calculated from intersections of the affected node/edge geometries, including logical components and boundary contacts as specified above. Their outlines denote infrastructure locations, never complete barangay electricity loss. No geographic surface areas or affected customer counts are inferred.

## Normalized outputs

- `outage_fault_mapping.csv` / Parquet: one row per event, its original PSGCs, simulated fault, feeder, source, downstream scenario, assignment method/uncertainty, status and impact_set_id.
- `affected_assets.parquet`: unique impact_set_id-to-node/edge memberships, preserving topology IDs. Join on impact_set_id to associate each outage. Repeated feeder/branch impacts share membership lists rather than duplicating millions of rows.
- `impact_sets.parquet`: compact node/edge ID lists, upstream paths and summary measures; no duplicated GeoJSON.
- `outage_impact_summary.csv` / Parquet: one row per original outage and supported measures, null on unresolved assignments.
- `outage_affected_barangays.parquet`: per-event relationships to barangays containing affected infrastructure, with original-barangay flag.
- `barangay_candidate_audit.csv`, `impact_size_distribution.csv`, `resolution_by_cause.csv` and `large_impact_review.parquet`: eligibility, distribution and flagged-impact audits.

## Results and limits

Mapped {resolved:,}/{len(events):,} events ({100 * resolved / len(events):.2f}%). Unresolved reasons: {dict(Counter(mapping.loc[mapping.resolution_status.eq('unresolved'), 'unresolved_reason']))}. Excluded {report['disconnected_transformers_excluded']} disconnected transformers. {len(large)} events exceed the large-impact review criterion (at least 1,000 transformers or 20 barangays). These are flags for inspection, not proven topology errors; feeder-wide outages can legitimately be large within this model. The effect-size distribution is provided as event-weighted percentiles, so shared supply interruption sets still count once per event. {report['resolved_events_with_no_downstream_transformers']} resolved events have no downstream transformer.

Exact seed replay passes for every event; seed+1 changes {alternate_changed:,} assignments. All affected IDs, scope constraints, active edges and metric lengths are validated. Source input hashes are unchanged. The FastAPI service checks the event/topology fingerprints on first load to reject stale precomputation. The UI retrieves precomputed IDs and cached feeder-scoped geometries, not the full provincial network per selection.

The local Spatial & Event Analysis page was a placeholder at implementation time; the selectable table and synchronized details are implemented there. Existing analytical and network pages are preserved. No verified PENELCO coverage, failure locations or restoration switching sequence is claimed.
''', encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=OUTPUT)
    p.add_argument('--seed', type=int, default=20261025)
    p.add_argument('--weights', type=Path, help='JSON cause-to-type sampling weights')
    p.add_argument('--min-transformers', type=int, default=1)
    p.add_argument('--min-line-km', type=float, default=0.)
    p.add_argument('--max-upstream-hops', type=int, default=0)
    p.add_argument('--max-upstream-km', type=float, default=0.)
    p.add_argument('--max-expanded-transformers', type=int, default=100)
    p.add_argument('--max-expanded-line-km', type=float, default=10.)
    a = p.parse_args()
    policy = ProtectionPolicy(**{k: getattr(a, k) for k in ProtectionPolicy.__dataclass_fields__})
    assert policy.min_transformers >= 1 and policy.min_line_km >= 0 and policy.max_upstream_hops >= 0 and policy.max_upstream_km >= 0
    assert policy.max_expanded_transformers >= policy.min_transformers and policy.max_expanded_line_km >= policy.min_line_km
    generate(a.output, a.seed, read(a.weights) if a.weights else None, policy)
