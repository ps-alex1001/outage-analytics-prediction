"""PENELCO-calibrated Synthetic V2, extending the preserved V1 implementation.

Run: uv run python scripts/generate_synthetic_outages.py
Tests: uv run python -m unittest discover -s scripts/tests -v
Every run creates a new directory under data/synthetic/. Existing inputs, V1,
dashboard and infrastructure are never modified. No ML model is trained.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform

import numpy as np
import pandas as pd

import outage_environment as env
from penelco_calibration import (CAUSES, calibration_tables, fit_duration_models, inspect_penelco)

ROOT = Path(__file__).resolve().parents[1]
VERSION = '2.0.0'
BASELINE = np.array([.50, .20, .10, .12, .08])
SCENARIOS = {
    'baseline': BASELINE,
    'weather_heavier': np.array([.60, .15, .05, .12, .08]),
    'vegetation_heavier': np.array([.40, .30, .10, .12, .08]),
    'terrain_reduced': np.array([.55, .22, .05, .13, .05]),
}


@dataclass(frozen=True)
class Config:
    seed: int = 42
    event_count: int = 0  # 0 means original V1 realized volume.
    epsilon: float = 1.0
    response: float = 4.0
    noise_sigma: float = .25
    animal_share: float = .01
    hour_calibration_strength: float = .25
    geographic_mode: str = 'auto'
    allocation: str = 'multinomial'
    municipality_tolerance: float = .015  # absolute share, 1.5 percentage points
    min_duration: float = 1.0
    max_duration: float = 2886.0  # 3 x observed max; inspected source max 962
    start: str = env.START
    end: str = env.END


@dataclass
class Pool:
    municipality: str
    rows: pd.DataFrame
    components: np.ndarray  # barangay x hour x [H,V,T,WV,RT], float32
    raw: np.ndarray  # barangay x hour x [R,W,C,M]
    ndvi: np.ndarray


def build_pools(data):
    pools = {}
    for municipality, rows in data.geography.groupby('municipality', sort=True):
        components, raw, ndvi = [], [], []
        for row in rows.itertuples(index=False):
            c, r, _, v = env.component_arrays(data, row)
            components.append(c.astype('float32'))
            raw.append(r.astype('float32'))
            ndvi.append(v.astype('float32'))
        pools[municipality] = Pool(municipality, rows.reset_index(drop=True),
                                   np.stack(components), np.stack(raw), np.stack(ndvi))
        print(f'Prepared {municipality}: {len(rows)} barangays', flush=True)
    return pools


def sampling_weights(scores, rng, config):
    """Strictly positive monotonic base + independent zero-mean log perturbation.

    f(S)=expm1(beta*S), epsilon=1 gives exactly V1's exp(4*S) response.
    Noise is N(0,sigma²) in log weight. It is controlled simulation variation,
    not an environmental observation or an event-level outage probability.
    """
    return (config.epsilon + np.expm1(config.response * scores)) * np.exp(
        rng.normal(0, config.noise_sigma, size=np.shape(scores)))


def municipality_counts(targets, n, rng, config):
    p = np.asarray(targets, float)
    env.require(n > 0 and np.isclose(p.sum(), 1) and (p >= 0).all(), 'Invalid count/target shares')
    if config.allocation == 'quota':
        result = np.floor(n * p).astype(int)
        order = np.argsort(-(n * p - result), kind='stable')
        result[order[:n - result.sum()]] += 1
        return result, 1
    # Rejection conditions a multinomial on an explicit closeness envelope.
    # Small runs need a feasible envelope; record its exact value in metadata.
    tolerance = max(config.municipality_tolerance, 2 / n)
    for attempt in range(1, 10001):
        result = rng.multinomial(n, p)
        if np.max(np.abs(result / n - p)) <= tolerance:
            return result, attempt
    raise ValueError('Constrained multinomial envelope too tight; increase tolerance')


def cause_odds(components, raw):
    h, _, _, wv, _ = components.T
    r, w, c, _ = raw.T
    return np.column_stack([
        np.ones(len(h)), .15 + 3.0 * wv,
        .20 + 1.6 * h + .4 * r + .4 * w + .4 * c,
        np.full(len(h), .70), np.full(len(h), .20),
        np.full(len(h), .20), np.full(len(h), .02),
    ])


def calibrate_cause_probabilities(odds, targets):
    """Iterative proportional fitting adjusts intercepts, retaining conditional odds."""
    target = np.asarray(targets, float)
    scale = np.ones(len(target))
    for _ in range(1000):
        weighted = odds * scale
        p = weighted / weighted.sum(axis=1, keepdims=True)
        mean = p.mean(axis=0)
        if np.max(np.abs(mean - target)) < 1e-10:
            return p, scale
        scale *= np.divide(target, mean, out=np.zeros_like(target), where=mean > 0)
        scale /= scale.max()
    raise ValueError('Cause marginal calibration failed to converge')


def draw_causes(probabilities, rng):
    return np.minimum((rng.random(len(probabilities))[:, None] >
                       np.cumsum(probabilities, axis=1)).sum(axis=1), len(CAUSES) - 1)


def duration_effect(components):
    h, _, t, _, rt = components.T
    # Original V1 environmental and access effects. Center on log scale so
    # empirical medians remain a reference, rather than multiplying them twice.
    return np.log((1 + .8 * h) * (1 + .8 * t + .5 * rt))


def draw_durations(causes, components, rng, fits, centers, config):
    values = np.exp(fits.mu.to_numpy()[causes] +
                    fits.sigma.to_numpy()[causes] * rng.standard_normal(len(causes)) +
                    duration_effect(components) - centers[causes])
    # Rejection rather than clipping: avoids a pile-up at either boundary.
    invalid = (values < config.min_duration) | (values > config.max_duration)
    rejected = int(invalid.sum())
    for _ in range(1000):
        if not invalid.any():
            return np.round(values, 2), rejected
        values[invalid] = np.exp(fits.mu.to_numpy()[causes[invalid]] +
            fits.sigma.to_numpy()[causes[invalid]] * rng.standard_normal(int(invalid.sum())) +
            duration_effect(components[invalid]) - centers[causes[invalid]])
        invalid = (values < config.min_duration) | (values > config.max_duration)
        rejected += int(invalid.sum())
    raise ValueError('Duration bounds incompatible with fitted distribution')


def hour_factors(observed, pools, config):
    # Partial years and reporting gaps make strong monthly/year targets unsafe.
    # Weak pooled hour-of-day adjustment only; no monthly/rainy-season multiplier.
    u = observed.loc[~observed.scheduled]
    counts = u.hour.value_counts().reindex(range(24), fill_value=0).to_numpy(float) + 10
    p = counts / counts.sum()
    return (p * 24) ** config.hour_calibration_strength


def simulate(data, pools, geography, cause_table, duration_fits, observed, config,
             scenario='baseline'):
    env.require(config.epsilon > 0 and config.response > 0 and config.noise_sigma >= 0,
                'Positive background/response and nonnegative noise are required')
    env.require(0 <= config.hour_calibration_strength <= 1, 'Invalid hour calibration strength')
    env.require(0 < config.min_duration < config.max_duration, 'Invalid duration bounds')
    rng = np.random.default_rng(np.random.SeedSequence([config.seed, 2]))
    counts, attempts = municipality_counts(geography.applied_target_share, config.event_count, rng, config)
    local = data.times.tz_convert(env.LOCAL_TZ)
    hours = hour_factors(observed, pools, config)[local.hour]
    frames, cdfs = [], {}
    candidate_component_sum = np.zeros(5)
    candidate_score_sum = candidate_hazard_sum = candidate_total = 0
    for i, target in enumerate(geography.itertuples(index=False)):
        pool = pools[target.municipality]
        score = np.einsum('bhc,c->bh', pool.components, SCENARIOS[scenario])
        weights = sampling_weights(score, rng, config)
        weights *= hours[None, :]
        weights[:, -1] = 0  # positive duration cannot start at inclusive end
        cdf = np.cumsum(weights.ravel(), dtype=float)
        cdf /= cdf[-1]
        cdfs[pool.municipality] = cdf
        indices = np.searchsorted(cdf, rng.random(int(counts[i])), side='right')
        b, t = np.divmod(indices, len(data.times))
        rows = pool.rows.iloc[b].reset_index(drop=True)
        frame = rows[['municipality_psgc', 'municipality', 'barangay_psgc', 'barangay']].copy()
        frame['b_index'], frame['time_index'] = b, t
        frame['pool_probability'] = weights.ravel()[indices] / weights.sum()
        frames.append(frame)
        candidate_component_sum += pool.components[:, :-1, :].sum(axis=(0, 1), dtype=float)
        candidate_score_sum += score[:, :-1].sum(dtype=float)
        candidate_hazard_sum += pool.components[:, :-1, 0].sum(dtype=float)
        candidate_total += score[:, :-1].size
    e = pd.concat(frames, ignore_index=True)

    def contexts(frame):
        c, r = np.empty((len(frame), 5)), np.empty((len(frame), 4))
        for m, pos in frame.groupby('municipality', sort=False).indices.items():
            b, t = frame.iloc[pos].b_index.to_numpy(int), frame.iloc[pos].time_index.to_numpy(int)
            c[pos], r[pos] = pools[m].components[b, t], pools[m].raw[b, t]
        return c, r

    components, raw = contexts(e)
    probabilities, intercepts = calibrate_cause_probabilities(cause_odds(components, raw), cause_table.target_share)
    causes = draw_causes(probabilities, rng)
    effects = duration_effect(components)
    centers = np.array([effects[causes == i].mean() if (causes == i).any() else effects.mean()
                        for i in range(len(CAUSES))])
    durations, duration_rejected = draw_durations(causes, components, rng, duration_fits, centers, config)
    collision_rejections = boundary_rejections = 0
    for _ in range(200):
        e['start_time'] = local[e.time_index.to_numpy(int)]
        e['end_time'] = e.start_time + pd.to_timedelta(durations, unit='m')
        invalid = set(e.index[e.end_time > local[-1]])
        boundary_rejections += len(invalid)
        # Accept earlier events, resample conflicting later ones within municipality.
        for _, group in e.sort_values('start_time').groupby('barangay_psgc', sort=False):
            active_until = local[0]
            for index, start, end in group[['start_time', 'end_time']].itertuples():
                if index in invalid:
                    continue
                if start < active_until:
                    invalid.add(index)
                    collision_rejections += 1
                else:
                    active_until = end
        if not invalid:
            break
        positions = np.array(sorted(invalid), int)
        for pos in positions:
            m = e.at[pos, 'municipality']
            cdf = cdfs[m]
            index = np.searchsorted(cdf, rng.random(), side='right')
            b, t = divmod(index, len(data.times))
            row = pools[m].rows.iloc[b]
            e.loc[pos, ['barangay_psgc', 'barangay', 'b_index', 'time_index']] = [row.barangay_psgc, row.barangay, b, t]
            e.at[pos, 'pool_probability'] = cdf[index] - (cdf[index - 1] if index else 0)
        components, raw = contexts(e)
        odds = cause_odds(components[positions], raw[positions]) * intercepts
        causes[positions] = draw_causes(odds / odds.sum(axis=1, keepdims=True), rng)
        durations[positions], rejected = draw_durations(causes[positions], components[positions], rng, duration_fits, centers, config)
        duration_rejected += rejected
    else:
        raise ValueError('Cannot meet count targets without overlapping outages')
    e['duration_minutes'] = durations
    e['cause_category'] = np.asarray(CAUSES)[causes]
    e['cause_is_synthetic'] = True
    e['severity_class'] = np.where(durations < 60, 'Minor', np.where(durations < 240, 'Moderate', 'Major'))
    for i, name in enumerate(env.SCORE_FIELDS):
        e[name] = components[:, i]
    for i, name in enumerate(['rainfall_score', 'wind_score', 'convective_score', 'soil_wetness_score']):
        e[name] = raw[:, i]
    e['synthetic_event_propensity'] = components @ SCENARIOS[scenario]
    e['conditional_pair_sampling_weight_share'] = e.pop('pool_probability')
    e['is_synthetic'], e['generator_version'], e['random_seed'] = True, VERSION, config.seed
    e['geographic_allocation_mode'] = geography.geographic_allocation_mode.iloc[0]
    e['barangay_assignment_basis'] = 'synthetic_environmentally_weighted_random_sampling'
    for name in ['year', 'month', 'hour']:
        e[name] = getattr(e.start_time.dt, name)
    e['day_of_week'] = e.start_time.dt.day_name()
    e['ndvi_context'] = 'retrospective annual composite; not event-time observation'
    e['ndvi_year'] = e.year
    e['ndvi_period_type'] = np.where(e.year == 2026, 'year_to_date', 'full_year')
    e['ndvi_source_start'] = e.year.astype(str) + '-01-01'
    e['ndvi_source_end_exclusive'] = np.where(e.year == 2026, env.NDVI_2026_END_EXCLUSIVE,
                                            (e.year + 1).astype(str) + '-01-01')
    e['weather_spatial_resolution'] = 'municipality'
    for m, positions in e.groupby('municipality', sort=False).indices.items():
        pool = pools[m]
        b, t = e.iloc[positions].b_index.to_numpy(int), e.iloc[positions].time_index.to_numpy(int)
        weather = data.weather[m].iloc[t]
        for name in env.WEATHER_FIELDS:
            e.loc[positions, name] = weather[name].to_numpy()
        for name in [*env.TERRAIN_FIELDS, 'era5_latitude', 'era5_longitude']:
            e.loc[positions, name] = pool.rows.iloc[b][name].to_numpy()
        e.loc[positions, 'ndvi_annual_mean'] = pool.ndvi[b, t]
        e.loc[positions, 'cape_j_kg'] = [data.cape[(pool.rows.iloc[bi].era5_latitude,
                                                 pool.rows.iloc[bi].era5_longitude)][ti]
                                       for bi, ti in zip(b, t)]
    e['era5_grid_resolution_degrees'] = .25
    e['outage_id'] = ['SYN-V2-' + str(config.seed) + '-' + str(i).zfill(7) for i in range(1, len(e) + 1)]
    e = e.drop(columns=['b_index', 'time_index']).sort_values(['start_time', 'barangay_psgc']).reset_index(drop=True)
    stats = {'count_allocation_attempts': attempts, 'collision_resamples': collision_rejections,
             'boundary_resamples': boundary_rejections, 'duration_rejection_draws': duration_rejected,
             'candidate_pair_count': candidate_total, 'candidate_mean_score': candidate_score_sum / candidate_total,
             'candidate_mean_hazard': candidate_hazard_sum / candidate_total,
             'candidate_component_means': (candidate_component_sum / candidate_total).tolist(),
             'duration_effect_log_centers': centers.tolist(), 'cause_ipf_intercepts': intercepts.tolist()}
    return e, stats


def validate_events(e, data, config):
    env.require(len(e) == config.event_count, 'Event target lost')
    env.require(not e.isna().any().any(), 'Missing event context')
    env.require(e.outage_id.is_unique, 'Duplicate outage IDs')
    env.require(not e.duplicated(['barangay_psgc', 'start_time']).any(), 'Duplicate barangay/time')
    keys = ['barangay_psgc', 'barangay', 'municipality_psgc', 'municipality']
    joined = e[keys].merge(data.geography[keys], on=keys, how='left', indicator=True, validate='many_to_one')
    env.require(joined._merge.eq('both').all(), 'Invalid geographic pair')
    env.require(e.start_time.ge(data.times[0]).all() and e.end_time.le(data.times[-1]).all(), 'Unsupported event period')
    env.require(e.duration_minutes.between(config.min_duration, config.max_duration).all(), 'Duration bounds')
    env.require((e.end_time > e.start_time).all(), 'Nonpositive durations')
    env.require(np.allclose((e.end_time - e.start_time).dt.total_seconds() / 60, e.duration_minutes), 'Duration/time mismatch')
    o = e.sort_values(['barangay_psgc', 'start_time'])
    env.require(not (o.start_time < o.groupby('barangay_psgc').end_time.shift()).any(), 'Overlapping events')
    for name in [*env.SCORE_FIELDS, 'synthetic_event_propensity', 'rainfall_score', 'wind_score', 'convective_score', 'soil_wetness_score']:
        env.require(e[name].between(0, 1).all(), f'Invalid score: {name}')
    np.testing.assert_allclose(e.weather_hazard_score, e[['rainfall_score', 'wind_score', 'convective_score', 'soil_wetness_score']].to_numpy() @ env.WEATHER_WEIGHTS, atol=1e-7)
    np.testing.assert_allclose(e.synthetic_event_propensity, e[env.SCORE_FIELDS].to_numpy() @ BASELINE, atol=1e-9)
    env.require(set(e.cause_category) <= set(CAUSES), 'Invalid/scheduled cause')
    return {'passed': True, 'events': len(e), 'overlaps': 0, 'missing_values': 0,
            'unique_ids': True, 'valid_geographic_pairs': True, 'formula_verified': True,
            'positive_consistent_durations': True, 'source_supported_period': True}


def fingerprints(paths):
    result = {}
    for key, path in paths.items():
        with path.open('rb') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
        result[key] = {'path': str(path), 'bytes': path.stat().st_size, 'sha256': digest}
    return result


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=env.json_default, allow_nan=False) + '\n', encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for field in ['seed', 'event_count']:
        p.add_argument('--' + field.replace('_', '-'), type=int, default=getattr(Config(), field))
    for field in ['epsilon', 'response', 'noise_sigma', 'animal_share', 'hour_calibration_strength', 'municipality_tolerance', 'min_duration', 'max_duration']:
        p.add_argument('--' + field.replace('_', '-'), type=float, default=getattr(Config(), field))
    p.add_argument('--geographic-mode', choices=['auto', 'resolved', 'conservative_mixture'], default='auto')
    p.add_argument('--allocation', choices=['multinomial', 'quota'], default='multinomial')
    p.add_argument('--start', default=env.START)
    p.add_argument('--end', default=env.END)
    p.add_argument('--penelco', type=Path, default=ROOT / 'data/reference/penelco/penelco_outages_analysis.csv')
    p.add_argument('--codebook', type=Path, default=ROOT / 'data/reference/penelco/codes.xls')
    p.add_argument('--output', type=Path)
    p.add_argument('--skip-experiments', action='store_true', help='Skip multi-seed/sensitivity experiments; marked incomplete')
    a = p.parse_args()
    cfg = Config(**{k: getattr(a, k) for k in Config.__dataclass_fields__})
    env.require(cfg.seed >= 0 and cfg.event_count >= 0, 'Seed/count must be nonnegative')
    paths = {**env.INPUTS, 'municipalities': ROOT / 'data/boundaries/bataan_municipalities.geojson',
             'penelco': a.penelco, 'codebook': a.codebook,
             'reported_listing': ROOT / 'data/reference/penelco/penelco_reported_listing.txt',
             'reported_substations': ROOT / 'data/topology/audit/reported_substations.csv',
             'reported_feeders': ROOT / 'data/topology/audit/reported_feeders_active.csv',
             'v1_events': ROOT / 'data/outages/synthetic_outage_events.csv',
             'v1_manifest': ROOT / 'data/outages/run_manifest.json'}
    before = fingerprints(paths)
    print('Validating and aligning existing environmental inputs...', flush=True)
    data = env.load_and_validate(cfg.start, cfg.end)
    # Municipality boundary PSGCs and names must agree with authoritative barangays.
    from shapely.geometry import shape
    municipal_doc = json.loads(paths['municipalities'].read_text(encoding='utf-8'))
    boundary_keys = {(f['properties']['municipality_psgc'], f['properties']['municipality']) for f in municipal_doc['features']}
    env.require(boundary_keys == set(data.geography[['municipality_psgc', 'municipality']].itertuples(index=False, name=None)), 'Municipality boundary keys disagree')
    env.require(all(shape(f['geometry']).is_valid for f in municipal_doc['features']), 'Invalid municipality boundaries')
    v1 = pd.read_csv(paths['v1_events'], dtype={'barangay_psgc': str, 'municipality_psgc': str})
    cfg = replace(cfg, event_count=cfg.event_count or len(v1))
    observed, codebook, mapping = inspect_penelco(a.penelco, a.codebook, data.geography)
    gt, ct, calibration = calibration_tables(observed, v1, data.geography, cfg.animal_share, cfg.geographic_mode)
    fits = fit_duration_models(observed)
    print(f'Unscheduled mapping: {calibration["resolved_records"]}/{calibration["eligible_unscheduled"]}; mode {calibration["geographic_mode"]}', flush=True)
    pools = build_pools(data)
    print(f'Sampling {cfg.event_count} V2 events...', flush=True)
    events, stats = simulate(data, pools, gt, ct, fits, observed, cfg)
    validation = validate_events(events, data, cfg)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output = a.output or ROOT / 'data/synthetic' / ('v2_' + stamp)
    env.require(output.resolve().is_relative_to((ROOT / 'data/synthetic').resolve()), 'Output must be a new directory under data/synthetic')
    output.mkdir(parents=True, exist_ok=False)
    print(f'Writing {output}', flush=True)
    events.to_csv(output / 'synthetic_outage_events_v2.csv', index=False, float_format='%.10g')
    readback = pd.read_csv(output / 'synthetic_outage_events_v2.csv', dtype={'barangay_psgc': str, 'municipality_psgc': str})
    for name in ['start_time', 'end_time']:
        readback[name] = pd.to_datetime(readback[name], format='ISO8601', utc=True).dt.tz_convert(env.LOCAL_TZ)
    validation['csv_round_trip'] = validate_events(readback, data, cfg)
    codebook.to_csv(output / 'penelco_outage_codebook.csv', index=False)
    mapping.to_csv(output / 'penelco_location_crosswalk.csv', index=False)
    gt.to_csv(output / 'municipality_calibration.csv', index=False)
    ct.to_csv(output / 'cause_calibration.csv', index=False)
    fits.to_csv(output / 'duration_parameters.csv', index=False)
    observed.drop(columns=['start_time']).to_csv(output / 'penelco_record_audit.csv', index=False)
    observed.loc[observed.scheduled].drop(columns=['start_time']).to_csv(output / 'penelco_scheduled_preserved.csv', index=False)
    observed.loc[~observed.scheduled & observed.mapping_status.ne('resolved')].drop(columns=['start_time']).to_csv(output / 'penelco_unresolved_and_ambiguous.csv', index=False)
    write_json(output / 'environment_inspection.json', data.inspection)
    write_json(output / 'mapping_coverage.json', calibration)
    write_json(output / 'validation.json', validation)
    from outage_v2_report import create_diagnostics
    create_diagnostics(output, data, pools, observed, v1, events, gt, ct, fits, cfg, stats,
                       calibration, run_experiments=not a.skip_experiments)
    after = fingerprints(paths)
    env.require(before == after, 'An input changed during generation')
    code_paths = {x: ROOT / 'scripts' / x for x in ['generate_synthetic_outages.py', 'outage_environment.py',
                  'penelco_calibration.py', 'outage_v2_report.py', 'legacy/generate_synthetic_outages_v1.py']}
    manifest = {'generator_version': VERSION, 'is_synthetic': True, 'config': asdict(cfg),
                'weather_weights_R_W_C_M': env.WEATHER_WEIGHTS.tolist(),
                'scenario_weights_H_V_T_WV_RT': {k: v.tolist() for k, v in SCENARIOS.items()},
                'inputs': before, 'code': fingerprints(code_paths), 'source_inputs_unchanged': before == after,
                'calibration': calibration, 'sampling': stats, 'environment_normalization': data.inspection['normalization'],
                'period': data.inspection['period'], 'software': {'python': platform.python_version(), 'numpy': np.__version__, 'pandas': pd.__version__},
                'experiments_completed': not a.skip_experiments,
                'event_csv': fingerprints({'events': output / 'synthetic_outage_events_v2.csv'})['events'],
                'warning': 'Synthetic barangay history; distributional calibration is not forecasting validation'}
    write_json(output / 'run_manifest.json', manifest)
    print(json.dumps({'output': str(output), 'events': len(events), 'validation': validation['passed'], 'calibration': calibration}, indent=2), flush=True)


if __name__ == '__main__':
    main()
