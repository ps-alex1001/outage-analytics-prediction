"""Reproducible V1 / observed / V2 comparisons and simulation experiments."""
from __future__ import annotations

from dataclasses import replace
import json

import numpy as np
import pandas as pd

import outage_environment as env
from penelco_calibration import CAUSES, calibration_tables, fit_duration_models


def markdown(frame, decimals=3):
    """Small tables without adding a tabulate dependency."""
    def cell(x):
        if pd.isna(x):
            return 'unavailable'
        if isinstance(x, (float, np.floating)):
            return f'{x:.{decimals}f}'
        return str(x).replace('|', '/')
    return '\n'.join(['| ' + ' | '.join(map(str, frame.columns)) + ' |',
                      '| ' + ' | '.join(['---'] * len(frame.columns)) + ' |'] +
                     ['| ' + ' | '.join(cell(v) for v in row) + ' |'
                      for row in frame.itertuples(index=False, name=None)])


def duration_summary(frame, source, column='cause_category'):
    rows = []
    for label, values in [('All', frame.duration_minutes), *[(k, g.duration_minutes) for k, g in frame.groupby(column)]]:
        rows.append({'source': source, 'cause_category': label, 'count': len(values), 'mean': values.mean(),
                     'median': values.median(), 'p90': values.quantile(.9), 'p95': values.quantile(.95),
                     'p99': values.quantile(.99), 'min': values.min(), 'max': values.max()})
    return rows


def concentration(frame, geography):
    shares = frame.barangay_psgc.value_counts(normalize=True)
    rows = []
    for m, g in geography.groupby('municipality', sort=True):
        counts = frame.loc[frame.municipality.eq(m), 'barangay_psgc'].value_counts().reindex(g.barangay_psgc, fill_value=0)
        p = counts / max(1, counts.sum())
        rows.append({'municipality': m, 'events': int(counts.sum()), 'barangays': len(g),
                     'zero_event_barangays': int(counts.eq(0).sum()), 'top_barangay_share': p.max(),
                     'hhi': np.square(p).sum(), 'hhi_relative_to_uniform': len(g) * np.square(p).sum()})
    return pd.DataFrame(rows), {'province_hhi': float(np.square(shares).sum()),
                                'top10_barangay_share': float(shares.nlargest(10).sum())}


def tv(a, b):
    keys = sorted(set(a.index) | set(b.index))
    return float(np.abs(a.reindex(keys, fill_value=0) - b.reindex(keys, fill_value=0)).sum() / 2)


def experiment_summary(e, baseline, geo, targets, stats, label, config):
    by_m, con = concentration(e, geo)
    counts = e.municipality.value_counts(normalize=True).reindex(targets.municipality, fill_value=0).to_numpy()
    left = e.groupby(['municipality', 'barangay_psgc']).size() / len(e)
    right = baseline.groupby(['municipality', 'barangay_psgc']).size() / len(baseline)
    return {'experiment': label, 'seed': config.seed, 'epsilon': config.epsilon,
            'response': config.response, 'noise_sigma': config.noise_sigma, 'events': len(e),
            'max_municipality_target_deviation_pp': float(np.max(np.abs(counts - targets.applied_target_share)) * 100),
            'barangay_total_variation_vs_baseline': tv(left, right),
            'mean_propensity': e.synthetic_event_propensity.mean(), 'mean_hazard': e.weather_hazard_score.mean(),
            'mean_hhi_relative_to_uniform': by_m.hhi_relative_to_uniform.mean(),
            'max_within_municipality_barangay_share': by_m.top_barangay_share.max(),
            'median_duration': e.duration_minutes.median(), 'p99_duration': e.duration_minutes.quantile(.99),
            'collision_resamples': stats['collision_resamples'], **con}


def create_diagnostics(output, data, pools, observed, v1, events, gt, ct, fits, cfg, stats,
                       calibration, run_experiments=True):
    from generate_synthetic_outages import SCENARIOS, simulate, write_json
    u = observed.loc[~observed.scheduled]
    # V1's combined Other/Unknown cannot be defensibly separated after generation.
    v1 = v1.copy()
    v1['cause_category'] = v1.cause_category.replace({'Vegetation / Tree Contact': 'Tree Contact'})
    municipality = gt.copy()
    municipality['v1_count'] = municipality.municipality.map(v1.municipality.value_counts()).fillna(0).astype(int)
    municipality['v2_count'] = municipality.municipality.map(events.municipality.value_counts()).fillna(0).astype(int)
    municipality['v2_share'] = municipality.v2_count / len(events)
    municipality['v2_minus_target_pp'] = 100 * (municipality.v2_share - municipality.applied_target_share)
    municipality['v2_minus_resolved_observed_pp'] = 100 * (municipality.v2_share - municipality.resolved_observed_share)
    municipality.to_csv(output / 'municipality_comparison.csv', index=False)
    causes = ct.copy()
    causes['v2_count'] = causes.cause_category.map(events.cause_category.value_counts()).fillna(0).astype(int)
    causes['v2_share'] = causes.v2_count / len(events)
    causes['v2_minus_target_pp'] = 100 * (causes.v2_share - causes.target_share)
    causes['v1_count_comparable_categories_only'] = causes.cause_category.map(v1.cause_category.value_counts())
    causes['v1_share_comparable_categories_only'] = causes.v1_count_comparable_categories_only / len(v1)
    causes.to_csv(output / 'cause_comparison.csv', index=False)
    pd.DataFrame([{'source': s, 'cause_category': c, 'count': n, 'share': n / len(d)}
                  for s, d in [('PENELCO unscheduled', u), ('V1', v1), ('V2', events)]
                  for c, n in d.cause_category.value_counts().items()]).to_csv(output / 'cause_comparison_long.csv', index=False)
    durations = pd.DataFrame(duration_summary(u, 'PENELCO unscheduled') + duration_summary(v1, 'V1') + duration_summary(events, 'V2'))
    durations.to_csv(output / 'duration_comparison.csv', index=False)
    frequency = data.geography[['municipality_psgc', 'municipality', 'barangay_psgc', 'barangay']].copy()
    for source, frame in [('v1', v1), ('v2', events)]:
        frequency[source + '_count'] = frequency.barangay_psgc.map(frame.barangay_psgc.value_counts()).fillna(0).astype(int)
        frequency[source + '_share'] = frequency[source + '_count'] / len(frame)
    frequency.to_csv(output / 'barangay_comparison.csv', index=False)
    concentration_rows = []
    concentrations = {}
    for source, frame in [('V1', v1), ('V2', events)]:
        table, con = concentration(frame, data.geography)
        table.insert(0, 'source', source)
        concentration_rows.append(table)
        concentrations[source] = con
    pd.concat(concentration_rows).to_csv(output / 'barangay_concentration.csv', index=False)
    # Historical temporal counts: show fractions and partial-period flags, never
    # extrapolate 2026 to a full year or assume changes reflect true reliability.
    temporal = []
    annual = []
    for source, frame in [('PENELCO unscheduled', u), ('V1', v1), ('V2', events)]:
        frame = frame.copy()
        frame['season'] = np.where(frame.month.between(5, 10), 'May-October descriptive bin', 'November-April descriptive bin')
        for dimension in ['year', 'month', 'hour', 'season']:
            for value, count in frame[dimension].value_counts().sort_index().items():
                temporal.append({'source': source, 'dimension': dimension, 'value': value,
                                 'count': count, 'share': count / len(frame),
                                 'period_note': ('2026 through August' if source == 'PENELCO unscheduled' else '2026 through June')})
        for year, group in frame.groupby('year'):
            annual.append({'source': source, 'year': year, 'count': len(group), 'months_with_events': group.month.nunique(),
                           'partial_year': year == 2026, 'reporting_completeness': 'unknown' if source.startswith('PENELCO') else 'simulation target'})
    pd.DataFrame(temporal).to_csv(output / 'temporal_comparison.csv', index=False)
    annual = pd.DataFrame(annual)
    annual.to_csv(output / 'annual_reporting_audit.csv', index=False)
    coverage_by_year = u.groupby(['year', 'mapping_status']).size().unstack(fill_value=0).reindex(columns=['resolved', 'ambiguous', 'unresolved'], fill_value=0)
    coverage_by_year['eligible'] = coverage_by_year.sum(axis=1)
    coverage_by_year['resolved_fraction'] = coverage_by_year.resolved / coverage_by_year.eligible
    coverage_by_year.to_csv(output / 'mapping_coverage_by_year.csv')
    selection = u.groupby(['cause_category', 'mapping_status']).size().unstack(fill_value=0).reindex(columns=['resolved', 'ambiguous', 'unresolved'], fill_value=0)
    selection['eligible'] = selection.sum(axis=1)
    selection['resolved_fraction'] = selection.resolved / selection.eligible
    selection.to_csv(output / 'mapping_selection_by_cause.csv')
    # Correct environmental comparison for municipality allocation. Event means
    # cannot be compared to an unweighted provincial grid when targets differ.
    environmental = []
    fields = [*env.SCORE_FIELDS, 'synthetic_event_propensity']
    score_bins = []
    for source, frame in [('V1', v1), ('V2', events)]:
        for i, feature in enumerate(fields):
            mean = 0.
            for m, group in frame.groupby('municipality'):
                pool = pools[m]
                arr = pool.components[:, :-1, i] if i < 5 else np.einsum('bhc,c->bh', pool.components[:, :-1], SCENARIOS['baseline'])
                mean += len(group) / len(frame) * float(arr.mean(dtype=float))
            environmental.append({'source': source, 'feature': feature, 'event_mean': frame[feature].mean(),
                                  'municipality_matched_candidate_mean': mean,
                                  'event_minus_candidate_mean': frame[feature].mean() - mean})
    for m, pool in pools.items():
        scores = np.einsum('bhc,c->bh', pool.components[:, :-1], SCENARIOS['baseline'])
        edges = np.linspace(0, 1, 11)
        candidate_counts = np.histogram(scores, edges)[0]
        event_counts = np.histogram(events.loc[events.municipality.eq(m), 'synthetic_event_propensity'], edges)[0]
        for j in range(10):
            score_bins.append({'municipality': m, 'score_bin_lower': edges[j], 'score_bin_upper': edges[j + 1],
                               'candidate_pairs': candidate_counts[j], 'events': event_counts[j],
                               'events_per_million_eligible_pairs': (1e6 * event_counts[j] / candidate_counts[j]) if candidate_counts[j] else None})
    environmental = pd.DataFrame(environmental)
    environmental.to_csv(output / 'environmental_relationships.csv', index=False)
    pd.DataFrame(score_bins).to_csv(output / 'propensity_bin_selection.csv', index=False)
    events[['duration_minutes', 'synthetic_event_propensity', *env.SCORE_FIELDS,
            'rainfall_score', 'wind_score', 'convective_score']].corr(method='spearman').to_csv(output / 'event_environment_spearman.csv')
    conditional = events.groupby('cause_category')[[*env.SCORE_FIELDS, 'rainfall_score', 'wind_score', 'convective_score']].mean()
    conditional.to_csv(output / 'cause_environment_means.csv')
    # Pooled/smoothed descriptive cause differences, never independent tiny fits.
    conditional_observed = []
    pooled = u.cause_category.value_counts(normalize=True)
    for dimension, frame in [('municipality', u.loc[u.mapping_status.eq('resolved')]), ('season', u)]:
        for key, group in frame.groupby(dimension):
            if len(group) < 30:
                continue
            counts = group.cause_category.value_counts()
            for cause in CAUSES[:-1]:
                conditional_observed.append({'dimension': dimension, 'group': key, 'n': len(group), 'cause_category': cause,
                    'raw_count': int(counts.get(cause, 0)), 'pooled_pseudocount': 20,
                    'smoothed_share': (counts.get(cause, 0) + 20 * pooled.get(cause, 0)) / (len(group) + 20)})
    pd.DataFrame(conditional_observed).to_csv(output / 'observed_conditional_causes.csv', index=False)
    # Questionable duplicate sensitivity excludes later repeated content only in
    # a diagnostic copy, preserving all raw rows and the primary calibration.
    raw_keys = ['interruption_start', 'duration_minutes', 'reported_area', 'circuit', 'cause_code',
                'weather_code', 'isolation_device_code', 'equipment_failed_code']
    distinct = observed.drop_duplicates(raw_keys)
    dg, dc, _ = calibration_tables(distinct, v1, data.geography, cfg.animal_share, cfg.geographic_mode)
    duplicate_sensitivity = gt[['municipality', 'applied_target_share']].merge(dg[['municipality', 'applied_target_share']], on='municipality', suffixes=('_retained', '_deduplicated'))
    duplicate_sensitivity['difference_pp'] = 100 * (duplicate_sensitivity.applied_target_share_deduplicated - duplicate_sensitivity.applied_target_share_retained)
    duplicate_sensitivity.to_csv(output / 'duplicate_geography_sensitivity.csv', index=False)
    dc[['cause_category', 'target_share']].merge(ct[['cause_category', 'target_share']], on='cause_category', suffixes=('_deduplicated', '_retained')).to_csv(output / 'duplicate_cause_sensitivity.csv', index=False)
    # Chronological calibration diagnostic: fit only 2021-2024, compare 2025-Aug
    # 2026. It evaluates distribution drift, not a forecasting model.
    train, hold = observed.loc[observed.year <= 2024], observed.loc[observed.year >= 2025]
    train_g, train_c, _ = calibration_tables(train, v1, data.geography, cfg.animal_share, cfg.geographic_mode)
    hold_g, hold_c, _ = calibration_tables(hold, v1, data.geography, cfg.animal_share, cfg.geographic_mode)
    held_causes = train_c[['cause_category', 'target_share']].merge(hold_c[['cause_category', 'observed_share', 'target_share']], on='cause_category', suffixes=('_train', '_heldout'))
    held_causes.to_csv(output / 'heldout_cause_comparison.csv', index=False)
    train_g[['municipality', 'resolved_observed_share', 'applied_target_share']].merge(hold_g[['municipality', 'resolved_observed_share', 'applied_target_share']], on='municipality', suffixes=('_train', '_heldout')).to_csv(output / 'heldout_geography_comparison.csv', index=False)
    train_fits = fit_duration_models(train)
    train_fits.to_csv(output / 'heldout_training_duration_parameters.csv', index=False)
    held_rows = []
    for row in train_fits.itertuples(index=False):
        actual = hold.loc[~hold.scheduled & hold.cause_category.eq(row.cause_category), 'duration_minutes']
        if len(actual):
            held_rows.append({'cause_category': row.cause_category, 'train_fit_n': row.fit_count, 'heldout_n': len(actual),
                              'fitted_median': np.exp(row.mu), 'heldout_median': actual.median(),
                              'fitted_p90': np.exp(row.mu + 1.2815515655446004 * row.sigma), 'heldout_p90': actual.quantile(.9)})
    pd.DataFrame(held_rows).to_csv(output / 'heldout_duration_comparison.csv', index=False)
    heldout = {'training_period': '2021-2024', 'evaluation_period': '2025-August 2026',
               'train_unscheduled': int((~train.scheduled).sum()), 'heldout_unscheduled': int((~hold.scheduled).sum()),
               'cause_target_total_variation': float(np.abs(held_causes.target_share_train - held_causes.target_share_heldout).sum() / 2),
               'municipality_resolved_share_total_variation': float(np.abs(train_g.resolved_observed_share - hold_g.resolved_observed_share).sum() / 2),
               'interpretation': 'Distribution drift only. Crosswalk uses a later undated reference, not an as-of-2024 operational resource. No prediction accuracy is measured.'}
    write_json(output / 'heldout_validation.json', heldout)
    experiment_rows = [experiment_summary(events, events, data.geography, gt, stats, 'baseline', cfg)]
    randomness = []
    reproducible = None
    if run_experiments:
        print('Checking deterministic replay...', flush=True)
        repeat, repeated_stats = simulate(data, pools, gt, ct, fits, observed, cfg)
        reproducible = events.equals(repeat) and stats == repeated_stats
        env.require(reproducible, 'Fixed-seed replay failed')
        del repeat
        variants = [(f'seed_{s}', replace(cfg, seed=s), 'baseline') for s in [43, 44] if s != cfg.seed]
        variants += [(s, cfg, s) for s in SCENARIOS if s != 'baseline']
        variants += [(name, replace(cfg, **change), 'baseline') for name, change in [
            ('background_0.5', {'epsilon': .5}), ('background_2', {'epsilon': 2.}),
            ('response_3', {'response': 3.}), ('response_5', {'response': 5.}),
            ('noise_0', {'noise_sigma': 0.}), ('noise_0.5', {'noise_sigma': .5})]]
        for label, config, scenario in variants:
            print('Experiment: ' + label, flush=True)
            sample, bookkeeping = simulate(data, pools, gt, ct, fits, observed, config, scenario)
            summary = experiment_summary(sample, events, data.geography, gt, bookkeeping, label, config)
            experiment_rows.append(summary)
            # No experimental event copies retained; small diagnostics only.
            for dimension in ['barangay_psgc', 'municipality', 'year', 'month', 'cause_category']:
                p = events[dimension].value_counts(normalize=True)
                q = sample[dimension].value_counts(normalize=True)
                randomness.append({'experiment': label, 'dimension': dimension, 'total_variation_vs_baseline': tv(p, q)})
            del sample
    experiments = pd.DataFrame(experiment_rows)
    experiments.to_csv(output / 'randomness_and_sensitivity.csv', index=False)
    pd.DataFrame(randomness).to_csv(output / 'randomness_distribution_comparison.csv', index=False)
    # Year-level concentration detects persistent static-ranking dominance.
    yearly = []
    for year, frame in events.groupby('year'):
        table, _ = concentration(frame, data.geography)
        table['year'] = year
        yearly.append(table)
    pd.concat(yearly).to_csv(output / 'barangay_concentration_by_year.csv', index=False)
    gains = environmental.loc[environmental.source.eq('V2')].set_index('feature').event_minus_candidate_mean
    base_con = experiments.iloc[0]
    seed_rows = experiments.loc[experiments.experiment.str.startswith('seed_')]
    weight_rows = experiments.loc[experiments.experiment.isin(list(SCENARIOS)[1:])]
    tuning_rows = experiments.loc[experiments.experiment.str.startswith(('background_', 'response_', 'noise_'))]
    checks = {
        'fixed_seed_exact_replay': reproducible,
        'municipality_targets_within_envelope': bool(municipality.v2_minus_target_pp.abs().max() <= 100 * max(cfg.municipality_tolerance, 2 / len(events)) + 1e-9),
        'cause_max_target_deviation_pp': float(causes.v2_minus_target_pp.abs().max()),
        'weather_hazard_selection_gain': float(gains['weather_hazard_score']),
        'propensity_selection_gain': float(gains['synthetic_event_propensity']),
        'baseline_mean_hhi_relative_to_uniform': float(base_con.mean_hhi_relative_to_uniform),
        'seed_barangay_tv_range': [float(seed_rows.barangay_total_variation_vs_baseline.min()), float(seed_rows.barangay_total_variation_vs_baseline.max())] if len(seed_rows) else [],
        'weight_max_province_hhi_change_fraction': float((weight_rows.province_hhi / base_con.province_hhi - 1).abs().max()) if len(weight_rows) else None,
        'tuning_max_province_hhi_change_fraction': float((tuning_rows.province_hhi / base_con.province_hhi - 1).abs().max()) if len(tuning_rows) else None,
        'experiments_completed': run_experiments,
        'questionable_unscheduled_rows': int(u.questionable_duplicate.sum()),
    }
    env.require(checks['municipality_targets_within_envelope'], 'Municipality allocation outside specified envelope')
    if len(events) >= 5000:
        env.require(gains['weather_hazard_score'] > 0 and gains['synthetic_event_propensity'] > 0,
                    'Environmental selection influence lost')
        env.require(checks['cause_max_target_deviation_pp'] < 3, 'Cause calibration unexpectedly far from target')
    write_json(output / 'statistical_validation.json', checks)
    report = f'''# Synthetic Outage V2 validation and comparison

This is a retrospective simulation. Barangay assignments are synthetic and probabilistic. No verified barangay PENELCO outage history or forecasting accuracy is implied.

## Source fitness and geography

The supplied cleaned CSV has {len(observed):,} unique record IDs. Scheduled records ({int(observed.scheduled.sum()):,}) are preserved separately and excluded from the {len(u):,} unscheduled calibration records. Unknown causes remain eligible. Cause, weather and failed-equipment codes match `codes.xls` Sheet1. One isolating-device record has code 301 labeled Unmapped code; it is flagged and retained without recoding. Device fields are not calibration inputs. The original XLS report identifies the location as a substation/load center. PENELCO timestamps have no timezone metadata; Asia/Manila is an explicit local-reporting assumption.

There are {int(observed.questionable_duplicate.sum())} identical-content rows with distinct record IDs, including {int(u.questionable_duplicate.sum())} unscheduled rows. They remain in primary calibration because the export cannot establish whether they are duplicate reporting. Deduplicated sensitivity tables disclose their effect. No missing required PENELCO values or nonpositive durations were found.

Reference-supported municipality proxies resolve {calibration['resolved_records']:,}/{len(u):,} eligible records ({100 * calibration['resolved_fraction']:.2f}%). {calibration['ambiguous_records']} are ambiguous and {calibration['unresolved_records']} unresolved. These proxies have medium reference confidence and are not verified outage footprints. The undated reported feeder listing is not a historical circuit-connectivity archive. Cross-boundary service, circuit aliases, special NGCP/MF identifiers and corrupted area names cannot be assigned confidently. `penelco_location_crosswalk.csv` records every area/circuit decision and its evidence. No real record receives an invented barangay assignment.

The chosen mode is **{calibration['geographic_mode']}**. Province-wide observed calibration is supportable under the configured coverage rules: **{calibration['provincewide_calibration_supported']}**. In conservative mode, each municipality target is `resolved_count / all_eligible + unresolved_fraction × V1_municipality_share`. This preserves the observed resolved mass and distributes uncertain simulation mass using the existing V1 environmental geography. It does not impute real missing locations. Resolved-subset shares and applied targets are separate columns below. V1 is a simulation prior, not an infrastructure/exposure denominator. Direct application of resolved shares remains an explicit optional mode.

{markdown(municipality[['municipality', 'resolved_observed_count', 'resolved_observed_share', 'applied_target_share', 'v2_count', 'v2_share', 'v2_minus_target_pp']])}

Selection bias is visible in `mapping_coverage_by_year.csv` and `mapping_selection_by_cause.csv`: support depends on circuit and station, and the resolved subset need not represent all reported interruptions. The maximum applied-target deviation is {municipality.v2_minus_target_pp.abs().max():.3f} percentage points. Differences from resolved-only shares are also exported, including intentional fallback differences.

## Causes and durations

Animal Interference takes {100 * cfg.animal_share:.2f} percentage point(s) from Other as a synthetic-only assumption. Original observations are not reclassified. V1 combines Other and Unknown, so those categories cannot be separately compared to V1. The complete V1 category counts are retained in `cause_comparison_long.csv`.

{markdown(causes[['cause_category', 'observed_count', 'observed_share', 'target_share', 'v2_share', 'v2_minus_target_pp']])}

{markdown(durations.loc[durations.cause_category.eq('All'), ['source', 'count', 'mean', 'median', 'p90', 'p95', 'p99', 'min', 'max']])}

Durations are positively skewed draws from cause-specific lognormal median/p90 fits. Environmental and terrain/access effects from V1 are centered on the log scale to avoid multiplying already calibrated restoration durations twice. Bounds are {cfg.min_duration:g} to {cfg.max_duration:g} minutes with rejection sampling, not clipping. Animal durations pool to Other, not an invented observed animal sample. {stats['duration_rejection_draws']} duration draws were rejected. Inspect the quantile differences above; agreement is approximate and no fitted duration model is operationally verified.

## Temporal patterns and reporting

{markdown(annual)}

Observed coverage is 2021 through August 2026, with unknown reporting completeness. Synthetic coverage is {data.inspection['period']['start_local']} through {data.inspection['period']['end_local']}. The first eight local hours of 2019 lack ERA5 coverage and are excluded; no filling occurs. All 2026 simulation summaries are partial through June. `temporal_comparison.csv` contains year/month/hour and descriptive May–October versus November–April bins. These bins are not fitted climatological season definitions. 2021–2025 common-period counts and 2026 common January–June counts are available in the aligned tables. Raw annual differences cannot establish reliability trends or complete underlying event rates.

Environmental weights govern timing. A weak pooled hour-of-day factor (strength {cfg.hour_calibration_strength:g}, with 10 pseudocounts per hour) preserves some reporting-clock patterns. No observed annual frequencies, monthly quota or manual rainy-season multiplier is applied, so seasonality remains driven by actual source weather. Applying later calibration to 2019–2020 is simulation extrapolation without PENELCO observations for those years.

## Environmental selection and concentration

{markdown(environmental)}

Candidate means are weighted to each event source's municipality allocation. Positive event-minus-candidate values show imposed selection effects, not discovered failure mechanisms. Event-only correlations in `event_environment_spearman.csv` do not estimate occurrence risk. `propensity_bin_selection.csv` gives event counts and candidate exposure per municipality/score bin. Weather is shared within municipalities, and ERA5 cells are shared across barangays; these are not independent weather observations.

{markdown(pd.concat(concentration_rows))}

All barangays retain positive probability before active-window exclusion. High NDVI/terrain rankings never determine selection. `barangay_concentration_by_year.csv` exposes persistent concentration and small-sample year variation. Neither uniform allocation nor a maximum-score assignment is used.

## Reproducibility, randomness and sensitivity

{markdown(experiments)}

Fixed-seed exact replay: **{reproducible}**. Different seeds must alter individual assignments while preserving aggregate targets. Tables compare geographic total variation, concentration, temporal shares and causes. The default epsilon=1 and response=4 preserve V1's exp(4S) response exactly before noise. Noise sigma=0.25 gives a one-standard-deviation weight multiplier exp(0.25)=1.284. The table tests lower/higher background, response and noise. The baseline is retained for continuity with V1 when the measured concentration and environmental effects remain moderate; these parameter choices are heuristic, not statistically identified by PENELCO barangay observations. Formula variants are sensitivity experiments only. The baseline production formula is unchanged.

Weight sensitivity maximum relative province HHI change: {checks['weight_max_province_hhi_change_fraction']}. Background/response/noise sensitivity maximum: {checks['tuning_max_province_hhi_change_fraction']}. Relative changes above 20% should be treated as substantial geographical concentration sensitivity and reviewed, not hidden. Seed differences include multinomial allocation noise; within-municipality concentration is reported separately to aid interpretation.

## Chronological calibration diagnostic

Fit period 2021–2024 has {heldout['train_unscheduled']} unscheduled records. Evaluation period 2025–August 2026 has {heldout['heldout_unscheduled']}. Cause-target total variation is {heldout['cause_target_total_variation']:.4f}; resolved-municipality-share total variation is {heldout['municipality_resolved_share_total_variation']:.4f}. `heldout_duration_comparison.csv` compares train-only fitted median/p90 to later observed quantiles. These show distribution stability or drift. The undated geographic reference is not an as-of-time operational input, and no predictive model was trained. The production dataset uses all available unscheduled records for descriptive calibration.

## Integrity and interpretation

Primary and CSV round-trip checks cover exact event count, IDs, positive and timestamp-consistent durations, supported dates, score formula, complete context, valid municipality/barangay pairs, duplicate starts and zero overlapping active barangay events. Conflicting pairs were resampled {stats['collision_resamples']} times within the assigned municipality, preserving target counts; {stats['boundary_resamples']} boundary draws were resampled. Resampling changes the independent candidate law slightly by conditioning on valid nonoverlapping intervals. Candidate pair weight shares are sampling diagnostics before active-window conditioning, not actual outage probabilities.

Input SHA-256 hashes, code hashes, normalization parameters, software versions and all numerical settings are recorded in `run_manifest.json`. Scheduled originals are retained in `penelco_scheduled_preserved.csv`; source input bytes and V1 remain unchanged. No dashboard, infrastructure generator, asset failures, customer counts, impact claims or ML forecasting interface is changed or created.
'''
    # Aligned periods avoid comparing a full summer with a January-June window.
    aligned = []
    for source, frame in [('PENELCO unscheduled', u), ('V1', v1), ('V2', events)]:
        for period, mask in [('2021-2025', frame.year.between(2021, 2025)), ('2026 January-June', frame.year.eq(2026) & frame.month.le(6))]:
            g = frame.loc[mask]
            for dim in ['month', 'hour', 'cause_category']:
                for key, count in g[dim].value_counts().sort_index().items():
                    aligned.append({'source': source, 'period': period, 'dimension': dim, 'value': key,
                                    'count': count, 'share': count / len(g)})
    pd.DataFrame(aligned).to_csv(output / 'aligned_period_comparison.csv', index=False)
    (output / 'validation_report.md').write_text(report, encoding='utf-8')
    (output / 'methodology.md').write_text(methodology(cfg, calibration, data), encoding='utf-8')
    (output / 'README.md').write_text('''# Synthetic Outage V2

Start with `validation_report.md`, `methodology.md` and `run_manifest.json`.
Primary output: `synthetic_outage_events_v2.csv`.

All barangay assignments, event occurrence, causes, duration and severity are synthetic.
The geography mode explicitly distinguishes observed resolved evidence from fallback mass.
The dataset is for retrospective GIS simulation, not verified PENELCO barangay history.

Reproduce from the repository root:

```powershell
uv run python scripts/generate_synthetic_outages.py
uv run python -m unittest discover -s scripts/tests -v
```

Use `--help` for count, seed, bounds, period and calibration controls. Every run uses a new output directory.
The source reference files live in `data/reference/penelco/`. The V1 generator is preserved byte-for-byte in `scripts/legacy/`.
Data folders are already git-ignored by this repository; keep the output folder and references when transferring the project.
''', encoding='utf-8')


def methodology(cfg, calibration, data):
    return f'''# Methodology: PENELCO-informed environmental outage simulation V2

## Purpose and scope

This simulation creates unplanned barangay-hour outage onsets for GIS exploration and later synthetic-label experiments. It does not reconstruct true barangay outage history or verify operational forecasting ability. Occurrence, barangay allocation, cause, duration and severity are simulated. All weights and interaction effects are heuristic assumptions. Neither raw event shares nor a synthetic score measures real outage probability without infrastructure/exposure denominators.

## Data and alignment

Authoritative administrative names and 10-digit PSGC identifiers come from the existing Bataan barangay and municipality GeoJSONs. Polygon validity, duplicate barangay geometry, key uniqueness, terrain/vegetation coverage, name agreement and municipality boundary keys are checked. Weather is an existing hourly Open-Meteo series for 12 municipalities. Its naive Asia/Singapore clocks (UTC+8) are localized as declared and converted to UTC. ERA5 CF-1.7 timestamps are decoded as UTC. CAPE is sampled from the nearest 0.25-degree cell to each polygon's interior representative point computed in UTM zone 51N. Joins use UTC; event exports use Asia/Manila. PENELCO local clocks are assumed Asia/Manila because the source does not encode timezone offsets.

Open-Meteo rainfall, gusts and deeper-layer soil moisture supply R, W and M. ERA5 CAPE supplies C, avoiding duplication of rainfall/wind from both products. Full schema and missingness inspection is saved. Missing environmental observations are never fabricated, extended or interpolated. The usable intersection is {data.inspection['period']['start_local']} to {data.inspection['period']['end_local']}. No event starts before coverage or ends beyond the final boundary.

Annual NDVI is retained as retrospective context, not event-time measurement or proof of tree/line contact. The inherited 2026 year-to-date composite includes imagery through 23 September, later than the January–June event period. Full-year composites also contain later imagery relative to early-year events. These fields are labeled and are unsuitable as as-of-time predictors without a separate availability assessment. No new historical NDVI is synthesized.

## Environmental score

Each relevant feature is normalized by pooled empirical 1st/99th-percentile clipped min-max scaling. Weather normalization uses municipality-hours, CAPE uses unique assigned cell-hours, vegetation uses barangay-years, and terrain uses barangays over the chosen simulation period. Extreme values are clipped in the score while raw source values remain context. These retrospective normalizers are not fitted predictive features.

`H = 0.30R + 0.35W + 0.20C + 0.15M`.

V is normalized annual mean NDVI. T is normalized mean slope. The preserved V1 inspection shows redundant slope/relief variables; p90 slope and relief remain context rather than receiving repeated weights. Terrain represents static access/exposure context, not an empirically established failure cause.

`WV = W × V`, `RT = R × T`.

`S = 0.50H + 0.20V + 0.10T + 0.12WV + 0.08RT`.

S is called the **synthetic outage propensity score**. Its coefficients sum to one and remain unchanged. Sensitivity alternatives do not replace the production formula.

## PENELCO eligibility and geographic uncertainty

Scheduled cause 004 is retained for historical analysis and excluded from unplanned calibration. Unknown 011 remains a separate eligible category. The codebook checks cause, weather, isolating-device and failed-equipment labels. All cause, weather and failed-equipment pairs agree; one unmapped isolating-device code is flagged and retained because it does not affect calibration. Raw unique record IDs and original labels are preserved. Identical-content rows with different IDs remain flagged in primary calibration, accompanied by deduplicated sensitivity. Neither encoded failed equipment nor circuit identity becomes a simulated event asset failure.

The original reporting template names a substation/load-center field. Area names alone cannot prove municipality coverage. A curated station/circuit crosswalk uses the existing supplied feeder location listing and authoritative administrative names. Circuit descriptions with explicit single-municipality support provide medium-confidence municipality proxies; cross-boundary, missing, corrupted or special-circuit evidence remains ambiguous or unresolved. Observed substation/feeder tables help interpret station aliases, but inferred topology and modeled infrastructure never supply occurrence exposure or service truth. The undated reference may not represent historical feeder changes.

Resolved share denominators contain only eligible resolved records. Province-wide use requires at least 90% mapping coverage and 20 resolved records in every municipality. Current coverage is {100 * calibration['resolved_fraction']:.2f}%; chosen mode is {calibration['geographic_mode']}. If evidence fails this screen, targets mix resolved observed mass with V1's municipality simulation shares for unresolved mass. No ambiguous real record is assigned to a municipality. This conservative allocation is explicitly a fallback, not a complete PENELCO municipality distribution. Coverage, causes and years of the selected subset are audited for selection bias.

## Constrained stochastic allocation

The default count is the original V1 realized event volume ({cfg.event_count:,}); it is configurable and is not inferred from observed annual reporting totals. Municipality counts are drawn from a multinomial, accepting draws within {100 * cfg.municipality_tolerance:g} percentage points per municipality (a minimum 2/N share tolerance ensures feasible small runs). This is a multinomial conditioned on a specified envelope; it controls extreme Monte Carlo deviations while retaining stochastic counts. Optional largest-remainder quota mode supports near-exact comparisons.

Within each municipality, every source-supported barangay-hour pair receives weight

`w = [epsilon + expm1(beta × S)] × exp(Z) × hour_factor`, with `Z ~ Normal(0, sigma²)`.

Defaults are epsilon={cfg.epsilon}, beta={cfg.response}, sigma={cfg.noise_sigma}. At epsilon=1, beta=4 the deterministic portion is exactly V1's exp(4S). Positive background ensures nonzero probability even for zero-score pairs. The score response is monotonic. Noise has zero mean in log weight; it is not an observed environmental value. Weights normalize across all eligible barangay-hours in the municipality. NumPy generators use recorded seeds. Low-propensity pairs remain selectable, and no argmax or static rank assignment occurs.

The hour factor is `(24 × smoothed_observed_hour_share)^strength`, using ten pseudocounts per clock hour and strength={cfg.hour_calibration_strength}. It supplies a weak reporting-clock pattern, without enforcing months, years or manually amplifying rainy seasons already represented by weather. Future work could evaluate different temporal constraints, but current reporting completeness does not justify annual frequency matching. 2019–2020 allocation applies later calibration retrospectively as an explicit simulation extrapolation.

## Cause assignment and durations

Observed causes map to Supply Interruption, Tree Contact, Weather / Storm, Equipment / Technical, Other and Unknown. A configurable {100 * cfg.animal_share:g}% synthetic Animal Interference share is transferred from Other. The real records are unchanged. Environment-dependent stochastic odds retain V1's weather and wind–vegetation relationships. Supply, Equipment, Other, Unknown and Animal retain background odds. Iterative proportional fitting adjusts category intercepts to match aggregate expected cause shares while preserving conditional environmental odds; independent categorical draws retain run variation. Municipality/season cause differences are descriptive and pooled with twenty pseudocounts, without fitting tiny location-specific models.

Durations are generated separately from S. Cause-specific lognormal parameters match observed median and p90 where at least thirty source records exist; sparse/synthetic categories pool to Other. The V1 factor `(1 + 0.8H) × (1 + 0.8T + 0.5RT)` retains environmental and access effects, centered by category on the log scale around fitted empirical durations. Lognormal draws supply restoration variability. Positive bounds {cfg.min_duration:g}–{cfg.max_duration:g} minutes are enforced by rejection, avoiding truncation pile-ups. Quantiles and extreme values are reported rather than asserting a perfect fit. Severity is a synthetic duration rule: Minor <60 minutes, Moderate 60–<240, Major ≥240; these are not official PENELCO classes.

Events must have nonoverlapping active intervals within a barangay. Conflicting candidates or durations extending past coverage are resampled within the same municipality, retaining exact count allocation. Resampling includes context, cause and duration; this conditions the independent initial proposal on valid windows. The exported conditional pair weight share is a pre-exclusion simulation diagnostic, not occurrence probability.

## Validation and future experiments

Integrity tests check unique IDs, geographic pairs, scores, clock alignment, positive durations, bounds, duplicate events, active-window collisions and CSV round-trip consistency. Comparisons preserve separate observed, V1 and V2 counts with explicit denominators. Raw annual/monthly patterns and aligned 2021–2025 / January–June 2026 periods are exported. Municipality-matched environmental candidate comparisons avoid confounding changed municipality allocation with weather influence. Seed runs assess barangay variation and municipality stability; alternative formula weights, epsilon, response and noise settings assess concentration and environmental relationships. All input hashes, code hashes, parameters, software and seed are saved.

A 2021–2024 fit versus 2025–August 2026 comparison assesses cause, municipality-share and duration distribution drift. It is not outage prediction evaluation, and the geographic reference lacks an as-of-time historical contract. Production descriptive calibration uses all eligible records.

No infrastructure density, transformer counts, poles, connectivity, customers or invented faulted assets enter occurrence. Stable PSGCs permit later impact visualization in a separate task. Future ML retains barangay-hour units, temporal train/validation/test separation and source availability checks. Duration, assigned cause, severity, propensity, component scores and other post-event/generator information must be excluded from occurrence predictors. Scheduled windows require a separate spatial/temporal assessment before masking or treating them as negatives; the current records do not establish verified barangay windows. No model is trained here, and high accuracy on imposed synthetic labels would not validate real PENELCO forecasting.
'''
