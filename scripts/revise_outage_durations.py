"""Create and activate a reversible presentation/simulation revision of V3.

Preserve onsets, IDs, causes and trained model artifacts. Shorten the duration
tail and explicitly assign unresolved events to synthetic connected scenarios.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
from datetime import datetime, timezone

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1] / 'data'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compress_durations(values):
    values = np.asarray(values, dtype=float)
    # Smooth at 120 minutes, strictly increasing, no pile-up at the upper cap.
    return np.round(np.where(values <= 120, values,
                             120 + 120 * (-np.expm1(-np.maximum(values - 120, 0) / 120))), 2).clip(.01, 240)


def randomized_durations(values, event_ids, seed=42):
    """Keep short events; redraw long ordinary events from bounded lognormal.

    Event-keyed streams make replay independent of row ordering. Reject rather
    than clip so there is no spike at the ceiling, even after minute rounding.
    """
    result = np.asarray(values, dtype=float).copy()
    for i, (original, oid) in enumerate(zip(result.copy(), event_ids)):
        if original <= 120:
            continue
        key = int.from_bytes(hashlib.sha256(str(oid).encode()).digest()[:8], 'big')
        rng = np.random.default_rng(np.random.SeedSequence([seed, key, 710]))
        center = min(180., max(100., .7 * original))
        upper = min(original, 999.)
        for _ in range(10000):
            draw = round(float(rng.lognormal(np.log(center), .55)), 2)
            if 60 <= draw <= upper:
                result[i] = draw
                break
        else:
            raise ValueError('Bounded duration draw did not converge')
    return result


def main():
    pointer = ROOT / 'active_bundle.json'
    previous = json.loads(pointer.read_text())
    if previous.get('event_revision') == 'random-long-tail-connected-fallback-v4':
        raise ValueError('This revision is already active; refusing repeated compression')
    source = ROOT / previous['events']
    old_impacts = ROOT / previous['impacts']
    manifest = json.loads((old_impacts / 'run_manifest.json').read_text())
    for item in manifest['inputs'].values():
        assert digest(item['path']) == item['sha256'], 'Stale source bundle'
    events = pd.read_csv(source, dtype={'barangay_psgc': str, 'municipality_psgc': str})
    before = events.get('original_duration_minutes', events.duration_minutes).copy()
    events['original_duration_minutes'] = before
    events['duration_minutes'] = randomized_durations(before, events.outage_id)
    rare = before.loc[before.gt(1440)].sort_values(ascending=False, kind='stable').head(3).index
    events.loc[rare, 'duration_minutes'] = before.loc[rare].clip(upper=2880)
    start = pd.to_datetime(events.start_time, format='ISO8601', utc=True)
    events['end_time'] = (start + pd.to_timedelta(events.duration_minutes, unit='m')).dt.tz_convert('Asia/Manila').astype(str)
    events['severity_class'] = np.where(events.duration_minutes < 60, 'Minor', np.where(events.duration_minutes < 240, 'Moderate', 'Major'))
    events['duration_revision'] = 'seeded-long-tail-with-three-original-day-scale-exceptions-v4'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    event_dir = ROOT / 'synthetic' / ('v3_duration_' + stamp)
    impact_dir = ROOT / 'impacts' / ('v3_connected_' + stamp)
    event_dir.mkdir()
    target = event_dir / 'synthetic_outage_events_v3.csv'
    events.to_csv(target, index=False)
    mapping = pd.read_parquet(old_impacts / 'outage_fault_mapping.parquet').set_index('outage_id', drop=False)
    summary = pd.read_parquet(old_impacts / 'outage_impact_summary.parquet').set_index('outage_id', drop=False)
    relationships = pd.read_parquet(old_impacts / 'outage_affected_barangays.parquet')
    unresolved = mapping.index[mapping.resolution_status.eq('unresolved')].tolist()
    donors = summary.loc[summary.resolution_status.eq('resolved_modeled')].copy()
    extra = []
    for frame in [mapping, summary]:
        for column, default in [('random_connected_fallback', False), ('fallback_donor_event_id', ''), ('fallback_original_reason', '')]:
            if column not in frame:
                frame[column] = default
    for oid in unresolved:
        origin = summary.loc[oid].copy()
        pool = donors.loc[donors.municipality_psgc.eq(origin.municipality_psgc)]
        assert len(pool), 'No connected scenario in origin municipality'
        same_cause = pool.loc[pool.cause_category.eq(origin.cause_category)]
        if len(same_cause):
            pool = same_cause
        pool = pool.sort_index()
        rng = np.random.default_rng(np.random.SeedSequence([42, int.from_bytes(hashlib.sha256(oid.encode()).digest()[:8], 'big')]))
        donor = pool.index[int(rng.integers(len(pool)))]
        for frame in [mapping, summary]:
            original = frame.loc[oid].copy()
            frame.loc[oid] = frame.loc[donor]
            for key in ['outage_id', 'origin_barangay_psgc', 'origin_municipality_psgc',
                        'municipality_psgc', 'municipality', 'barangay_psgc', 'barangay',
                        'start_time', 'end_time', 'duration_minutes', 'cause_category']:
                if key in frame.columns:
                    frame.at[oid, key] = original[key]
            frame.at[oid, 'random_connected_fallback'] = True
            frame.at[oid, 'fallback_donor_event_id'] = donor
            frame.at[oid, 'fallback_original_reason'] = original.unresolved_reason
            frame.at[oid, 'assignment_method'] = 'synthetic_random_connected_same_municipality_fallback'
        rows = relationships.loc[relationships.outage_id.eq(donor)].copy()
        rows['outage_id'] = oid
        rows['is_origin_barangay'] = rows.barangay_psgc.eq(origin.barangay_psgc)
        extra.append(rows)
    if extra:
        relationships = pd.concat([relationships, *extra], ignore_index=True)
    indexed_events = events.set_index('outage_id')
    for field in ['start_time', 'end_time', 'duration_minutes']:
        summary[field] = indexed_events[field].reindex(summary.index)
    summary['affected_feeder_ids'] = summary.affected_feeder_ids.map(lambda value: np.asarray(value).reshape(-1).tolist())
    # Only normalized, unchanged asset membership tables are copied.
    impact_dir.mkdir()
    for name in ['impact_sets.parquet', 'affected_assets.parquet', 'barangay_candidate_audit.csv']:
        shutil.copy2(old_impacts / name, impact_dir / name)
    mapping.reset_index(drop=True).to_parquet(impact_dir / 'outage_fault_mapping.parquet', index=False)
    mapping.reset_index(drop=True).to_csv(impact_dir / 'outage_fault_mapping.csv', index=False)
    summary.reset_index(drop=True).to_parquet(impact_dir / 'outage_impact_summary.parquet', index=False)
    summary.reset_index(drop=True).drop(columns=['affected_feeder_ids']).to_csv(impact_dir / 'outage_impact_summary.csv', index=False)
    relationships.to_parquet(impact_dir / 'outage_affected_barangays.parquet', index=False)
    sets = pd.read_parquet(impact_dir / 'impact_sets.parquet')
    assert mapping.resolution_status.eq('resolved_modeled').all()
    assert set(mapping.impact_set_id) <= set(sets.impact_set_id)
    assert set(summary.index) == set(events.outage_id) and summary.index.is_unique
    assert events.duration_minutes.between(.01, 2880).all()
    assert events.duration_minutes.ge(1000).sum() == len(rare)
    assert (events.duration_minutes <= before).all()
    ordered = events.assign(start=start).sort_values(['barangay_psgc', 'start'])
    ends = pd.to_datetime(ordered.end_time, format='ISO8601', utc=True)
    assert not (ordered.start < ends.groupby(ordered.barangay_psgc).shift()).any()
    validation = {'total_outages': len(events), 'mapped_outages': len(events), 'mapped_percentage': 100.,
                  'shared_impact_sets': len(sets), 'random_connected_fallback_events': int(mapping.random_connected_fallback.sum()),
                  'rare_long_events': len(rare), 'ordinary_duration_cap_minutes': 999,
                  'events_over_four_hours': int(events.duration_minutes.gt(240).sum()),
                  'events_over_eight_hours': int(events.duration_minutes.gt(480).sum()),
                  'events_over_twelve_hours': int(events.duration_minutes.gt(720).sum()),
                  'ordinary_events_at_rounded_240_minutes': int(events.loc[~events.index.isin(rare), 'duration_minutes'].round().eq(240).sum()),
                  'duration_seed': 42,
                  'unresolved_reasons': {}, 'active_edges_only': True, 'duration_max_minutes': float(events.duration_minutes.max()),
                  'changed_durations': int((before != events.duration_minutes).sum()),
                  'original_duration_p99': float(before.quantile(.99)), 'revised_duration_p99': float(events.duration_minutes.quantile(.99)),
                  'no_overlaps': True, 'same_municipality_fallback': True}
    manifest['inputs']['outages'] = {'path': str(target), 'sha256': digest(target), 'bytes': target.stat().st_size}
    manifest['validation'] = validation
    manifest['revision_source'] = str(old_impacts)
    manifest['revision_script_sha256'] = digest(__file__)
    note = ('Duration revision: retain original durations <=120 min. Redraw longer events using event-ID-keyed NumPy seed 42, '
            'lognormal median min(180,max(100,0.7*original)), sigma=0.55; reject outside [60,min(original,999)] minutes. '
            'Round to .01 minute; no clipping or four-hour ceiling. Durations above four hours are possible, with a sparse extended tail. '
            'Exception: retain the three longest original events exceeding 24 hours, bounded at 48 hours; no other event reaches 1,000 minutes. '
            'This is a dashboard simulation assumption, not an empirically established restoration limit. '
            'Severity thresholds remain unchanged. '
            'Unresolved events receive seeded random existing connected scenarios in the same municipality, preferring the same cause. '
            'Original event locations remain unchanged; fallback assets can lie in a different barangay. No disconnected network edge is reconnected. '
            'The ML panel and trained model remain frozen to the original V3 training snapshot; revised durations are not a retraining or new evaluation. '
            'Regenerate active-window masks before training a new model on this revision.')
    for directory in [event_dir, impact_dir]:
        (directory / 'revision.json').write_text(json.dumps({'validation': validation, 'previous_bundle': previous, 'note': note}, indent=2))
        (directory / 'README.md').write_text(note, encoding='utf-8')
    (impact_dir / 'run_manifest.json').write_text(json.dumps(manifest, indent=2))
    (impact_dir / 'validation.json').write_text(json.dumps(validation, indent=2))
    bundle = {**previous, 'events': target.relative_to(ROOT).as_posix(), 'impacts': impact_dir.relative_to(ROOT).as_posix(),
              'event_revision': 'random-long-tail-connected-fallback-v4', 'training_events': previous.get('training_events', previous['events'])}
    temp = ROOT / 'active_bundle.revision.tmp'
    temp.write_text(json.dumps(bundle, indent=2))
    os.replace(temp, pointer)
    print(json.dumps({'bundle': bundle, 'validation': validation}, indent=2))


if __name__ == '__main__':
    main()
