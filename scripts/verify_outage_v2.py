"""Independent output-to-source audit; does not import generator helpers.

Usage: uv run python scripts/verify_outage_v2.py data/synthetic/v2_<run>
"""
from pathlib import Path
import argparse
import hashlib
import json

import numpy as np
import pandas as pd
import xarray as xr


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def verify(output):
    output = Path(output).resolve()
    meta = json.loads((output / 'run_manifest.json').read_text(encoding='utf-8'))
    e = pd.read_csv(output / 'synthetic_outage_events_v2.csv', dtype={'barangay_psgc': str, 'municipality_psgc': str})
    for column in ['start_time', 'end_time']:
        e[column] = pd.to_datetime(e[column], format='ISO8601', utc=True)
    assert digest(output / 'synthetic_outage_events_v2.csv') == meta['event_csv']['sha256']
    assert len(e) == meta['config']['event_count'] and e.outage_id.is_unique
    assert not e.isna().any().any()
    assert not e.duplicated(['barangay_psgc', 'start_time']).any()
    assert np.allclose((e.end_time - e.start_time).dt.total_seconds() / 60, e.duration_minutes)
    ordered = e.sort_values(['barangay_psgc', 'start_time'])
    assert not (ordered.start_time < ordered.groupby('barangay_psgc').end_time.shift()).any()
    for key, item in {**meta['inputs'], **meta['code']}.items():
        assert digest(item['path']) == item['sha256'], f'Hash mismatch: {key}'
    # Full event-time weather join, independent of the generation context writer.
    weather = pd.read_csv(meta['inputs']['weather']['path'])
    weather['municipality'] = weather.municipality.replace({'Balanga': 'City of Balanga'})
    weather['start_time'] = pd.to_datetime(weather.time).dt.tz_localize('Asia/Singapore').dt.tz_convert('UTC')
    fields = ['precipitation_mm', 'wind_speed_10m_m_s', 'wind_gusts_10m_m_s',
              'soil_moisture_0_to_7cm_m3_m3', 'soil_moisture_7_to_28cm_m3_m3',
              'temperature_2m_c', 'relative_humidity_2m_pct', 'surface_pressure_hpa']
    joined = e.merge(weather[['municipality', 'start_time', *fields]], on=['municipality', 'start_time'],
                     how='left', validate='many_to_one', suffixes=('', '_source'))
    for name in fields:
        np.testing.assert_allclose(joined[name], joined[name + '_source'], atol=1e-7)
    terrain = pd.read_csv(meta['inputs']['terrain']['path'], dtype={'barangay_psgc': str, 'municipality_psgc': str})
    columns = ['mean_elevation_m', 'mean_slope_deg', 'p90_slope_deg', 'elevation_relief_p95_p05_m']
    joined = e.merge(terrain[['barangay_psgc', 'municipality_psgc', 'municipality', 'barangay', *columns]],
                     on=['barangay_psgc', 'municipality_psgc', 'municipality', 'barangay'],
                     how='left', validate='many_to_one', suffixes=('', '_source'))
    for name in columns:
        np.testing.assert_allclose(joined[name], joined[name + '_source'], atol=1e-7)
    ndvi = pd.read_csv(meta['inputs']['vegetation']['path'], dtype={'barangay_psgc': str})
    joined = e.merge(ndvi[['barangay_psgc', 'year', 'mean_ndvi']], on=['barangay_psgc', 'year'],
                     how='left', validate='many_to_one')
    np.testing.assert_allclose(joined.ndvi_annual_mean, joined.mean_ndvi, atol=1e-7)
    with xr.open_dataset(meta['inputs']['era5']['path'], engine='h5netcdf') as ds:
        selected = ds.cape.sel(valid_time=xr.DataArray(e.start_time.dt.tz_localize(None).to_numpy(), dims='event'),
                               latitude=xr.DataArray(e.era5_latitude, dims='event'),
                               longitude=xr.DataArray(e.era5_longitude, dims='event')).values
        np.testing.assert_allclose(selected, e.cape_j_kg, atol=1e-7)
    norm = meta['environment_normalization']
    def scale(column, symbol):
        a = norm[symbol]
        return np.clip((np.asarray(column) - a['p01']) / (a['p99'] - a['p01']), 0, 1)
    r = scale(e.precipitation_mm, 'R')
    w = scale(e.wind_gusts_10m_m_s, 'W')
    c = scale(e.cape_j_kg, 'C')
    m = scale(e.soil_moisture_7_to_28cm_m3_m3, 'M')
    v = scale(joined.mean_ndvi, 'V')
    t = scale(e.mean_slope_deg, 'T')
    h = .30 * r + .35 * w + .20 * c + .15 * m
    s = .50 * h + .20 * v + .10 * t + .12 * w * v + .08 * r * t
    for actual, expected in [('weather_hazard_score', h), ('synthetic_event_propensity', s),
                             ('vegetation_exposure_score', v), ('terrain_exposure_score', t),
                             ('wind_vegetation_interaction', w * v), ('rain_terrain_interaction', r * t)]:
        np.testing.assert_allclose(e[actual], expected, atol=1e-7)
    local = e.start_time.dt.tz_convert('Asia/Manila')
    for key in ['year', 'month', 'hour']:
        np.testing.assert_array_equal(e[key], getattr(local.dt, key))
    np.testing.assert_array_equal(e.day_of_week, local.dt.day_name())
    raw = pd.read_csv(meta['inputs']['penelco']['path'], dtype=str)
    scheduled = pd.read_csv(output / 'penelco_scheduled_preserved.csv', dtype=str)
    assert set(scheduled.record_id) == set(raw.loc[raw.cause.eq('scheduled'), 'record_id'])
    audit = pd.read_csv(output / 'penelco_record_audit.csv', dtype=str)
    assert set(audit.record_id) == set(raw.record_id)
    for column in raw.columns:
        if column == 'record_id':
            continue
        np.testing.assert_array_equal(raw.set_index('record_id')[column].sort_index(),
                                      audit.set_index('record_id')[column].sort_index())
    assert e.is_synthetic.all() and e.cause_is_synthetic.all()
    assert not e.cause_category.str.contains('Scheduled', case=False).any()
    result = {'passed': True, 'event_count': len(e), 'all_event_weather_context_matches_source': True,
              'all_event_terrain_context_matches_source': True, 'all_event_ndvi_context_matches_source': True,
              'all_event_cape_context_matches_source': True, 'formula_recomputed_from_raw_inputs': True,
              'source_record_values_preserved': True, 'scheduled_ids_preserved': True,
              'input_and_generator_hashes_unchanged': True, 'audit_script_sha256': digest(__file__),
              'duration_draws_above_observed_max': int(e.duration_minutes.gt(pd.to_numeric(raw.loc[raw.cause.ne('scheduled'), 'duration_minutes']).max()).sum())}
    target = output / 'independent_source_validation.json'
    target.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    verify(parser.parse_args().output)
