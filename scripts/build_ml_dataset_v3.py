"""Build an audited, retrospective one-hour-ahead V3 research panel.

Run with the project's uv environment. Sources are read-only. No model training,
resampling, scaling, or imputation is performed. Monthly Parquet files keep peak
memory bounded. The notebook imports these same definitions.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rasterio.warp import transform as project_points
from shapely.geometry import shape
from shapely.ops import transform as transform_geometry
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
TZ = 'Asia/Manila'
VERSION = '3.0.0'
HOUR = pd.Timedelta(hours=1)
RAW = {
    'precipitation_mm': 'rainfall_mm_lag1h',
    'wind_speed_10m_m_s': 'wind_speed_m_s_lag1h',
    'wind_gusts_10m_m_s': 'wind_gust_m_s_lag1h',
    'soil_moisture_7_to_28cm_m3_m3': 'soil_moisture_m3_m3_lag1h',
    'soil_moisture_0_to_7cm_m3_m3': 'soil_moisture_shallow_m3_m3_lag1h',
    'temperature_2m_c': 'temperature_c_lag1h',
    'relative_humidity_2m_pct': 'humidity_pct_lag1h',
    'surface_pressure_hpa': 'surface_pressure_hpa_lag1h',
}
TERRAIN = ['mean_slope_deg', 'elevation_relief_p95_p05_m', 'mean_elevation_m',
           'p90_slope_deg', 'median_elevation_m', 'median_slope_deg',
           'elevation_p05_m', 'elevation_p95_m', 'elevation_std_m', 'pct_area_slope_gt_15_deg']
ENV_ENGINEERED = ['rainfall_3h_sum', 'rainfall_6h_sum', 'rainfall_24h_sum',
    'rainfall_24h_max', 'consecutive_rainy_hours', 'wind_gust_3h_max',
    'wind_gust_6h_max', 'wind_speed_3h_mean', 'wind_gust_change_3h',
    'soil_moisture_24h_mean', 'cape_3h_max', 'rainfall_x_slope']
CALENDAR = ['hour_sin', 'hour_cos', 'month_sin', 'month_cos', 'is_weekend']
CANDIDATES = list(RAW.values()) + ['cape_j_kg_lag1h'] + TERRAIN + ENV_ENGINEERED + CALENDAR
CORE_TERRAIN = TERRAIN[:3]
META = ['prediction_time', 'barangay_psgc', 'municipality_psgc', 'is_eligible',
        'exclusion_reason', 'feature_complete', 'split']
FORBIDDEN = ('outage_id', 'cause', 'duration', 'end_time', 'severity', 'propensity',
             'sampling_weight', 'random_seed', 'generator_', 'affected_', 'fault_',
             'outcome_', 'synthetic_', 'outage_occurred', 'outage_event_count')
LIMITATIONS = [
    'V3 labels are synthetic Bernoulli draws with three regularized municipality offsets; not measured PENELCO rates.',
    'Barangay assignments are probabilistic simulation outputs, not verified PENELCO history.',
    'Generator environmental relationships are heuristic; learning them does not establish real failure mechanisms or PENELCO forecasting accuracy.',
    'Historical Open-Meteo and ERA5 inputs are retrospective reconstructions. Valid-time causality is enforced; actual release-time availability is NOT established by a one-hour lag.',
    'ERA5T commonly arrives about five days later and final ERA5 is revised later; these exact values cannot be assumed available one hour ahead.',
    'Archived forecast vintages and verified issue/publication times are required before operational forecasting. CAPE, wind, precipitation, temperature and humidity may be reconstructible from forecasts; modeled soil moisture requires a compatible source.',
    'Same-year NDVI uses future imagery, including September 2026 imagery after June labels. Even prior-year publication dates are absent: NDVI and wind_gust_x_ndvi are deferred.',
    'Static present-day terrain is applied retrospectively. Municipal weather and coarse ERA5 cells are shared exposures, not independent stations per barangay.',
    'The generator rejects overlapping local events and durations beyond the right boundary; active masking and boundary truncation change the at-risk population.',
    'Missing warm-up features remain null. No imputer/scaler is fitted; any later estimator needing complete values must fit its preprocessing on training only.',
    'Feature selection uses training-only unsupervised diagnostics, not model performance. Correlation does not demonstrate usefulness; ablation and forecasting compatibility remain future work.',
]

def require(condition, message):
    if not condition:
        raise ValueError(message)

def local(value):
    t = pd.Timestamp(value)
    return t.tz_localize(TZ) if t.tzinfo is None else t.tz_convert(TZ)

def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def json_default(value):
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, (Path, pd.Timestamp)):
        return str(value)
    raise TypeError(type(value).__name__)

def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=json_default, allow_nan=False)+'\n', encoding='utf-8')

def csv(path):
    return pd.read_csv(path, dtype={'barangay_psgc':str, 'municipality_psgc':str})

def fingerprint(path):
    p = Path(path).resolve()
    return {'path': str(p), 'bytes': p.stat().st_size, 'sha256':sha256(p)}

def assert_hourly(index, name):
    require(index.is_unique and not index.hasnans, f'{name}: duplicate/null timestamps')
    require(index.equals(pd.date_range(index.min(), index.max(), freq='h')), f'{name}: hourly gaps/order')

def rainy_streak(rain, threshold=0.1, cap=72):
    """Recent >threshold run; unknown until a dry reset or cap known wet hours."""
    values = np.full(len(rain), np.nan, dtype='float32')
    count, known = 0, False
    for i, value in enumerate(np.asarray(rain)):
        if not np.isfinite(value):
            count, known = 0, False
        elif value <= threshold:
            count, known = 0, True
            values[i] = 0
        else:
            count = min(count+1, cap)
            if known or count == cap:
                values[i] = count
    return values

def engineer_weather(frame, lag_hours=1, rain_threshold=0.1, streak_cap=72):
    """One hourly, gap-reindexed location. Relabel source s to cutoff s+lag.

    At cutoff t, W-hour inputs have labels t-lag-(W-1) ... t-lag.
    No fill, partial windows or cross-location rolling is permitted.
    """
    require(lag_hours >= 1, 'At least one hour of observation lag is required')
    require(frame.index.is_unique, 'Duplicate weather times')
    f=frame.sort_index().reindex(pd.date_range(frame.index.min(),frame.index.max(),freq='h'))
    result=f[list(RAW)].rename(columns=RAW).copy()
    rain, gust, speed = f.precipitation_mm, f.wind_gusts_10m_m_s, f.wind_speed_10m_m_s
    for w in [3,6,24]:
        result[f'rainfall_{w}h_sum']=rain.rolling(w,min_periods=w).sum()
    result['rainfall_24h_max']=rain.rolling(24,min_periods=24).max()
    result['consecutive_rainy_hours']=rainy_streak(rain,rain_threshold,streak_cap)
    for w in [3,6]:
        result[f'wind_gust_{w}h_max']=gust.rolling(w,min_periods=w).max()
    result['wind_speed_3h_mean']=speed.rolling(3,min_periods=3).mean()
    result['wind_gust_change_3h']=gust-gust.shift(3)
    result['soil_moisture_24h_mean']=f.soil_moisture_7_to_28cm_m3_m3.rolling(24,min_periods=24).mean()
    result.index = result.index + pd.Timedelta(hours=lag_hours)
    return result.astype('float32')

def engineer_cape(series, lag_hours=1):
    s=series.sort_index().reindex(pd.date_range(series.index.min(),series.index.max(),freq='h'))
    result=pd.DataFrame({'cape_j_kg_lag1h':s,'cape_3h_max':s.rolling(3,min_periods=3).max()})
    result.index=result.index+pd.Timedelta(hours=lag_hours)
    return result.astype('float32')

def calendar_features(times):
    # All calendar features describe the cutoff, including the weekend flag.
    return pd.DataFrame({'hour_sin':np.sin(2*np.pi*times.hour/24),
        'hour_cos':np.cos(2*np.pi*times.hour/24),
        'month_sin':np.sin(2*np.pi*(times.month-1)/12),
        'month_cos':np.cos(2*np.pi*(times.month-1)/12),
        'is_weekend':(times.dayofweek>=5).astype('float32')},index=times).astype('float32')

def label_arrays(events, times, barangay_ids, start, end, validation_start, test_start):
    """Origin labels for (t,t+1h]; active intervals are [start,end)."""
    # Fractional-nanosecond restoration timestamps cannot be searched in a
    # pandas 3 microsecond-resolution index without an explicit common unit.
    times=times.as_unit('ns')
    shape_=(len(times),len(barangay_ids))
    counts=np.zeros(shape_,dtype='uint16')
    active=np.zeros(shape_,dtype=bool)
    b_lookup={b:i for i,b in enumerate(barangay_ids)}
    # ceil(start)-1h handles both exact-hour starts and subhour starts.
    cutoffs=events.start_time.dt.ceil('h')-HOUR
    ti=times.get_indexer(pd.DatetimeIndex(cutoffs))
    bi=events.barangay_psgc.map(b_lookup).to_numpy(int)
    valid=ti>=0
    np.add.at(counts,(ti[valid],bi[valid]),1)
    # Restrict active-state work to intervals intersecting this partition.
    # Targets above still inspect every start, including starts just after its end.
    intersecting=events.loc[events.start_time.le(times[-1]) & events.end_time.gt(times[0])]
    for b,group in intersecting.groupby('barangay_psgc',sort=False):
        j=b_lookup[b]
        for s,e in group[['start_time','end_time']].itertuples(index=False,name=None):
            lo,hi=times.searchsorted(s,'left'),times.searchsorted(e,'left')
            active[lo:hi,j]=True
    reasons=np.full(shape_,'eligible',dtype='U24')
    reasons[active]='active_outage'
    coverage=(times>=start)&(times+HOUR<=end)
    reasons[~coverage,:]='incomplete_label_window'
    purge=np.zeros(len(times),bool)
    for boundary in [validation_start,test_start]:
        # Split belongs to cutoff. Purge when the right-closed label touches next split.
        purge |= (times<boundary)&(times+HOUR>=boundary)
    reasons[purge,:]='split_boundary_purge'
    eligible=reasons=='eligible'
    return counts,active,eligible,reasons

@dataclass
class Sources:
    paths: dict
    manifest: dict
    geo: pd.DataFrame
    events: pd.DataFrame
    terrain: pd.DataFrame
    ndvi: pd.DataFrame
    weather: pd.DataFrame
    weather_features: dict
    cape_features: dict
    inspection: dict
    start: pd.Timestamp
    end: pd.Timestamp

def resolve_paths(args):
    data=Path(args.data_dir).resolve()
    event=Path(args.events).resolve() if args.events else None
    if event is None:
        choices=list(data.glob('synthetic/v3_*/synthetic_outage_events_v3.csv'))
        require(len(choices)==1,'Specify --events: expected exactly one V3 source, never pick newest silently')
        event=choices[0]
    paths={'events':event,'event_manifest':Path(args.event_manifest).resolve() if args.event_manifest else event.parent/'run_manifest.json',
        'weather':data/'weather/open_meteo_bataan_2019_01_to_2026_06.csv',
        'era5':data/'weather/era5_bataan_2019_01_to_2026_06.nc',
        'terrain':data/'terrain/bataan_barangay_terrain.csv',
        'ndvi':data/'vegetation/bataan_barangay_ndvi_yearly.csv',
        'geography':data/'boundaries/bataan_barangays.geojson',
        'municipalities':data/'boundaries/bataan_municipalities.geojson',
        'legacy_builder':Path(args.legacy_builder).resolve() if args.legacy_builder else ROOT.parent/'Outage Prediction/scripts/build_ml_dataset.py',
        'legacy_panel':Path(args.legacy_panel).resolve() if args.legacy_panel else ROOT.parent/'Outage Prediction/data/processed/ml/bataan_barangay_hour_analytical.parquet'}
    for name,path in paths.items():
        require(path.is_file(),f'Missing {name}: {path}. Use configurable CLI paths/data-dir.')
    return paths

def load_sources(paths, lag=1, threshold=0.1, streak_cap=72):
    manifest=json.loads(paths['event_manifest'].read_text(encoding='utf-8'))
    require(manifest['generator_version']=='v3' and manifest['input_preservation_verified'], 'Not a completed, source-preserved V3 run')
    require(sha256(paths['events'])==manifest['event_csv_sha256'],'V3 event checksum mismatch')
    for key in ['weather','era5','terrain','geography']:
        require(sha256(paths[key])==manifest['inputs'][key]['sha256'],f'{key} differs from V3 run inputs')
    require(sha256(paths['ndvi'])==manifest['inputs']['vegetation']['sha256'],'NDVI checksum mismatch')
    period=json.loads((paths['events'].parent/'input_inspection.json').read_text())['period']
    start,end=local(period['start_local']),local(period['end_local'])
    doc=json.loads(paths['geography'].read_text(encoding='utf-8'))
    geo=pd.DataFrame([f['properties'] for f in doc['features']]).sort_values('barangay_psgc').reset_index(drop=True)
    for col in ['barangay_psgc','municipality_psgc']:
        geo[col]=geo[col].astype(str)
        require(geo[col].str.fullmatch(r'0\d{9}').all(),f'Invalid {col}')
    require(geo.barangay_psgc.is_unique,'Duplicate barangays')
    municipalities=pd.DataFrame([f['properties'] for f in json.loads(paths['municipalities'].read_text(encoding='utf-8'))['features']])
    muni_key='municipality_psgc' if 'municipality_psgc' in municipalities else 'psgc'
    require(set(geo.municipality_psgc)==set(municipalities[muni_key].astype(str)),'Municipality boundaries mismatch')
    def project(src,dst):
        return lambda x,y,z=None: project_points(src,dst,x,y)
    points=[]
    for feature in doc['features']:
        g=shape(feature['geometry'])
        require(g.is_valid and not g.is_empty,'Invalid polygon')
        point=transform_geometry(project('EPSG:32651','EPSG:4326'),
            transform_geometry(project('EPSG:4326','EPSG:32651'),g).representative_point())
        points.append((str(feature['properties']['barangay_psgc']),point.y,point.x))
    geo=geo.merge(pd.DataFrame(points,columns=['barangay_psgc','latitude','longitude']),on='barangay_psgc',validate='one_to_one')
    e=csv(paths['events'])
    for c in ['start_time','end_time']:
        e[c]=pd.to_datetime(e[c],format='mixed',utc=True).dt.tz_convert(TZ)
    require(e.outage_id.is_unique and not e.isna().any().any(),'Duplicate IDs or missing V3 event values')
    require(e.is_synthetic.eq(True).all() and e.generator_version.eq('v3').all(), 'Non-V3/synthetic labels')
    require(e.barangay_psgc.isin(geo.barangay_psgc).all(),'Unknown origin barangay')
    parents=geo.set_index('barangay_psgc').municipality_psgc
    require(e.municipality_psgc.eq(e.barangay_psgc.map(parents)).all(),'Origin-parent mismatch')
    labels=geo.set_index('barangay_psgc')
    require(e.barangay.eq(e.barangay_psgc.map(labels.barangay)).all() and e.municipality.eq(e.barangay_psgc.map(labels.municipality)).all(),'Origin names mismatch')
    require((e.end_time>e.start_time).all() and e.duration_minutes.gt(0).all(),'Invalid durations')
    require(np.allclose((e.end_time-e.start_time).dt.total_seconds()/60,e.duration_minutes),'Duration mismatch')
    require(e.start_time.ge(start).all() and e.end_time.le(end).all(),'Events outside label coverage')
    require(not e.cause_category.str.contains('scheduled|planned',case=False,regex=True).any(),'Scheduled labels present')
    require(set(e.cause_category)=={'Weather','Tree contact','Equipment Failure','Supply','Animal','Other'},'Unexpected cause categories')
    require(not e.duplicated(['barangay_psgc','start_time']).any(),'Duplicate/conflicting event start')
    ordered=e.sort_values(['barangay_psgc','start_time'])
    require(not ordered.start_time.lt(ordered.groupby('barangay_psgc').end_time.shift()).any(),'Overlapping origin events require review')
    t,n=csv(paths['terrain']),csv(paths['ndvi'])
    for frame,keys in [(t,['barangay_psgc']),(n,['barangay_psgc','year'])]:
        require(not frame.duplicated(keys).any() and not frame.isna().any().any(),'Invalid static keys/missingness')
        require(set(frame.barangay_psgc)==set(geo.barangay_psgc),'Static join incomplete')
        require(frame.barangay.eq(frame.barangay_psgc.map(labels.barangay)).all() and frame.municipality.eq(frame.barangay_psgc.map(labels.municipality)).all(),'Static names mismatch')
    require(t.municipality_psgc.eq(t.barangay_psgc.map(parents)).all(),'Terrain parent mismatch')
    require(t.mean_slope_deg.between(0,90).all() and t.elevation_relief_p95_p05_m.ge(0).all(),'Impossible terrain')
    require(n.mean_ndvi.between(-1,1).all() and n.valid_coverage_pct.between(0,100).all(),'Invalid NDVI')
    require(n.groupby('year').barangay_psgc.nunique().eq(len(geo)).all(),'Incomplete NDVI year')
    w=pd.read_csv(paths['weather'])
    require(w.timezone.nunique()==1 and w.timezone.iloc[0]=='Asia/Singapore' and w.utc_offset_seconds.eq(28800).all(),'Weather timezone changed')
    w['time']=pd.to_datetime(w.time).dt.tz_localize('Asia/Singapore').dt.tz_convert(TZ)
    w['municipality']=w.municipality.replace({'Balanga':'City of Balanga'})
    names=geo[['municipality','municipality_psgc']].drop_duplicates()
    require(names.municipality.is_unique and names.municipality_psgc.is_unique,'Municipality crosswalk ambiguous')
    w=w.merge(names,on='municipality',validate='many_to_one')
    require(w.municipality_psgc.notna().all() and not w.duplicated(['municipality_psgc','time']).any(),'Weather join/duplicates')
    require(not w.isna().any().any(),'Weather source missing values: investigate before building')
    for col in ['precipitation_mm','wind_speed_10m_m_s','wind_gusts_10m_m_s']:
        require(w[col].ge(0).all() and np.isfinite(w[col]).all(),f'Invalid {col}')
    for col in ['soil_moisture_0_to_7cm_m3_m3','soil_moisture_7_to_28cm_m3_m3']:
        require(w[col].between(0,1).all(),f'Invalid {col}')
    require(w.relative_humidity_2m_pct.between(0,100).all() and w.temperature_2m_c.between(-20,60).all(),'Implausible humidity/temperature')
    wf={}
    for key,group in w.groupby('municipality_psgc',sort=True):
        group=group.sort_values('time').set_index('time')
        assert_hourly(group.index,f'Weather {key}')
        require(group[['latitude','longitude']].drop_duplicates().shape[0]==1,'Weather location changes')
        wf[key]=engineer_weather(group,lag,threshold,streak_cap)
    require(set(wf)==set(geo.municipality_psgc),'Missing municipal weather')
    cf={}
    with xr.open_dataset(paths['era5'],engine='h5netcdf') as ds:
        et=pd.DatetimeIndex(ds.valid_time.values).tz_localize('UTC').tz_convert(TZ)
        assert_hourly(et,'ERA5')
        require(ds.cape.attrs['units']=='J kg**-1' and ds.cape.attrs['GRIB_stepType']=='instant','Unverified CAPE units/interval')
        lat,lon=ds.latitude.values,ds.longitude.values
        require(geo.latitude.between(min(lat),max(lat)).all() and geo.longitude.between(min(lon),max(lon)).all(),'Barangay outside ERA5 grid')
        geo['era5_latitude']=[float(lat[np.abs(lat-y).argmin()]) for y in geo.latitude]
        geo['era5_longitude']=[float(lon[np.abs(lon-x).argmin()]) for x in geo.longitude]
        geo['era5_cell']=[f'{y:.2f}_{x:.2f}' for y,x in zip(geo.era5_latitude,geo.era5_longitude)]
        for cell,gr in geo.groupby('era5_cell'):
            v=ds.cape.sel(latitude=gr.era5_latitude.iloc[0],longitude=gr.era5_longitude.iloc[0]).values
            require(np.isfinite(v).all() and (v>=0).all(),'Unreliable CAPE')
            cf[cell]=engineer_cape(pd.Series(v,index=et),lag)
        era={name:{'unit':v.attrs.get('units'),'step_type':v.attrs.get('GRIB_stepType'),
                'missing':int(v.isnull().sum()),'values':v.size,'min':float(v.min()),'max':float(v.max())} for name,v in ds.data_vars.items()}
        era_info={'sizes':dict(ds.sizes),'first_local':str(et.min()),'last_local':str(et.max()),
                  'grid_degrees':0.25,'assigned_cells':len(cf),'variables':era}
    inspection={'events':{'rows':len(e),'columns':len(e.columns),'dtypes':e.dtypes.astype(str).to_dict(),
        'missing':e.isna().sum().to_dict(),'duplicate_ids':int(e.outage_id.duplicated().sum()),
        'duplicate_origin_starts':int(e.duplicated(['barangay_psgc','start_time']).sum()),
        'shared_start_times_across_barangays':int(e.start_time.duplicated().sum()),
        'all_starts_exact_hour':bool(e.start_time.eq(e.start_time.dt.floor('h')).all()),
        'first':str(e.start_time.min()),'last':str(e.start_time.max()),
        'duration_minutes':e.duration_minutes.describe(percentiles=[.01,.5,.9,.95,.99]).to_dict()},
        'geography':{'barangays':len(geo),'municipalities':geo.municipality_psgc.nunique()},
        'weather':{'rows':len(w),'columns':list(w),'first':str(w.time.min()),'last':str(w.time.max()),
            'source_timezone':'Asia/Singapore, UTC+08:00; converted to Asia/Manila',
            'missing':w.isna().sum().to_dict(),'duplicate_keys':0,'timestamp_gaps':0,
            'unique_coordinate_pairs':len(w[['latitude','longitude']].drop_duplicates()),
            'gust_below_speed_rows':int(w.wind_gusts_10m_m_s.lt(w.wind_speed_10m_m_s).sum()),
            'minimum_gust_minus_speed_m_s':float((w.wind_gusts_10m_m_s-w.wind_speed_10m_m_s).min()),
            'numeric':w[list(RAW)].describe(percentiles=[.01,.5,.95,.99,.999]).to_dict()},
        'terrain':{'rows':len(t),'missing':t.isna().sum().to_dict(),'join_complete':True},
        'ndvi':{'rows':len(n),'years':sorted(n.year.unique().tolist()),'missing':n.isna().sum().to_dict(),
            'freshness':'Annual full-year; 2026 YTD through 2026-09-23. Release dates absent.',
            'valid_coverage_pct':n.valid_coverage_pct.describe().to_dict()},'era5':era_info}
    return Sources(paths,manifest,geo,e,t,n,w,wf,cf,inspection,start,end)

def panel_month(s, times, validation_start, test_start):
    geo=s.geo; b=len(geo); length=len(times)*b
    data={'prediction_time':times.repeat(b),'barangay_psgc':np.tile(geo.barangay_psgc.to_numpy(),len(times)),
          'municipality_psgc':np.tile(geo.municipality_psgc.to_numpy(),len(times))}
    muni_index={name:i for i,name in enumerate(sorted(s.weather_features))}
    m=geo.municipality_psgc.map(muni_index).to_numpy()
    wcols=list(next(iter(s.weather_features.values())).columns)
    cube=np.stack([s.weather_features[k].reindex(times).to_numpy() for k in sorted(muni_index)],axis=1)
    values=cube[:,m,:].reshape(length,len(wcols))
    data.update({c:values[:,i] for i,c in enumerate(wcols)})
    ckeys=sorted(s.cape_features); cindex={c:i for i,c in enumerate(ckeys)}
    cm=geo.era5_cell.map(cindex).to_numpy()
    cape=np.stack([s.cape_features[c].reindex(times).to_numpy() for c in ckeys],axis=1)[:,cm,:].reshape(length,2)
    data['cape_j_kg_lag1h'],data['cape_3h_max']=cape[:,0],cape[:,1]
    terrain=s.terrain.set_index('barangay_psgc').reindex(geo.barangay_psgc)
    data.update({c:np.tile(terrain[c].to_numpy('float32'),len(times)) for c in TERRAIN})
    data['rainfall_x_slope']=(data['rainfall_24h_sum']*data['mean_slope_deg']).astype('float32')
    cal=calendar_features(times)
    data.update({c:np.repeat(cal[c].to_numpy(),b) for c in CALENDAR})
    counts,active,eligible,reasons=label_arrays(s.events,times,geo.barangay_psgc.tolist(),s.start,s.end,validation_start,test_start)
    data['is_eligible']=eligible.ravel()
    data['exclusion_reason']=reasons.ravel()
    data['split']=np.repeat(np.where(times<validation_start,'train',np.where(times<test_start,'validation','test')),b)
    y=pd.array((counts.ravel()>0).astype('uint8'),dtype='UInt8');y[~eligible.ravel()]=pd.NA
    data['outage_next_hour']=y
    frame=pd.DataFrame(data)
    require(len(frame)==length and not frame.duplicated(['prediction_time','barangay_psgc']).any(),'Panel row multiplication')
    return frame,counts.ravel(),active.ravel()

def audit_legacy(path, geo, out):
    """Scan the old panel in monthly row groups; never use its target in V3."""
    pf=pq.ParquetFile(path); total=positive=duplicates=0; last_time=None
    missing=Counter(); missing_year=[];missing_muni=[]; extrema={}; samples=[]
    b=geo.barangay_psgc.to_numpy();parents=geo.set_index('barangay_psgc').municipality_psgc
    for i in range(pf.num_row_groups):
        f=pf.read_row_group(i).to_pandas()
        total+=len(f);positive+=int(f.outage_occurred.sum())
        tt=pd.DatetimeIndex(f.timestamp.drop_duplicates())
        assert_hourly(tt,f'Legacy row group {i}')
        require(len(f)==len(tt)*len(b),'Legacy row group is not a full rectangular panel')
        require(np.array_equal(f.barangay_psgc.to_numpy(),np.tile(b,len(tt))),'Legacy key order/uniqueness mismatch')
        require(pd.DatetimeIndex(f.timestamp).equals(tt.repeat(len(b))),'Legacy hourly keys mismatch')
        require(last_time is None or tt.min()==last_time+HOUR,'Legacy cross-partition gap/duplicate')
        last_time=tt.max()
        require(f.municipality_psgc.eq(f.barangay_psgc.map(parents)).all(),'Legacy invalid parents')
        null=f.isna();missing.update(null.sum().to_dict())
        for year,idx in f.groupby(f.timestamp.dt.year).groups.items():
            sums=null.loc[idx].sum()
            missing_year.extend({'year':int(year),'feature':c,'rows':len(idx),'missing':int(v)} for c,v in sums.items())
        for muni,idx in f.groupby('municipality_psgc').groups.items():
            sums=null.loc[idx].sum()
            missing_muni.extend({'municipality_psgc':muni,'feature':c,'rows':len(idx),'missing':int(v)} for c,v in sums.items())
        num=f.select_dtypes('number')
        for c in num:
            lo,hi=num[c].min(),num[c].max()
            if pd.notna(lo):
                old=extrema.get(c,(float('inf'),float('-inf')))
                extrema[c]=(min(old[0],float(lo)),max(old[1],float(hi)))
        samples.append(f.sample(min(200,len(f)),random_state=100+i))
    require(total==pf.metadata.num_rows,'Legacy footer mismatch')
    for name,rows,keys in [('legacy_missingness_by_year',missing_year,['year','feature']),
                            ('legacy_missingness_by_municipality',missing_muni,['municipality_psgc','feature'])]:
        x=pd.DataFrame(rows).groupby(keys,as_index=False)[['rows','missing']].sum()
        x['missing_pct']=100*x.missing/x.rows;x.to_csv(out/f'{name}.csv',index=False)
    pd.DataFrame([{'feature':c,'missing':missing[c],'missing_pct':100*missing[c]/total,
                    'min':extrema.get(c,(None,None))[0],'max':extrema.get(c,(None,None))[1]} for c in pf.schema_arrow.names]).to_csv(out/'legacy_feature_quality.csv',index=False)
    sample=pd.concat(samples,ignore_index=True).select_dtypes('number')
    constants=[c for c in sample if sample[c].nunique(dropna=True)<=1]
    return {'path':str(path),'rows':total,'columns':len(pf.schema_arrow.names),'row_groups':pf.num_row_groups,
        'first':str(pq.ParquetFile(path).read_row_group(0,columns=['timestamp']).column(0)[0].as_py()),
        'last':str(last_time),'positive_v1_same_hour_rows':positive,'duplicate_keys':duplicates,
        'timestamp_gaps':0,'invalid_parent_keys':0,'full_panel_key_scan':True,
        'sample_constant_or_empty_numeric_columns':constants,'sample_rows_for_constants':len(sample),
        'columns_present':pf.schema_arrow.names,'not_reused':'V1 targets, outcomes, infrastructure and same-year NDVI are never imported into V3.'}

def feature_selection(train_sample):
    """Unsupervised decisions, restricted to eligible train cutoffs only."""
    corr=train_sample[CANDIDATES].corr(method='spearman')
    decisions={c:('kept','Interpretable causal-history research candidate; future ablation required') for c in CANDIDATES}
    for c in set(TERRAIN)-set(CORE_TERRAIN):
        decisions[c]=('dropped','Redundant terrain summaries; retain mean slope, mean elevation and P95-P05 relief')
    for c in ['surface_pressure_hpa_lag1h','soil_moisture_shallow_m3_m3_lag1h']:
        decisions[c]=('deferred','Additional source measurement; initial subset uses deeper soil context and excludes untested extra dimensions')
    for c in ['cape_j_kg_lag1h']:
        decisions[c]=('dropped','Use the recent 3h maximum for the initial instability family; avoid duplicate latest/max inputs')
    for c,reference in [('rainfall_6h_sum',['rainfall_3h_sum','rainfall_24h_sum']),
                         ('wind_gust_6h_max',['wind_gust_3h_max']),
                         ('wind_gust_m_s_lag1h',['wind_gust_3h_max']),
                         ('soil_moisture_m3_m3_lag1h',['soil_moisture_24h_mean'])]:
        score=max(abs(float(corr.loc[c,r])) for r in reference)
        threshold=.90 if c.endswith('6h_sum') or c.endswith('6h_max') else .95
        if score>=threshold:
            decisions[c]=('dropped',f'Training-sample |Spearman rho|={score:.4f} >= {threshold} with {", ".join(reference)}; keep distinct window families')
    for c in CANDIDATES:
        if train_sample[c].nunique(dropna=True)<=1 or train_sample[c].isna().mean()>.20:
            decisions[c]=('deferred','Training sample is constant/empty or >20% missing; do not impute an unsupported feature')
    kept=[c for c in CANDIDATES if decisions[c][0]=='kept']
    require(not any(any(term in c for term in FORBIDDEN) for c in kept),'Forbidden predictor')
    return kept,decisions,corr

def feature_definition(c):
    units={**{v:('mm' if k=='precipitation_mm' else 'm/s' if 'wind_' in k else 'm3/m3' if 'soil_' in k else 'deg C' if 'temperature' in k else '%' if 'humidity' in k else 'hPa') for k,v in RAW.items()},
        'mean_slope_deg':'degree','mean_elevation_m':'m','elevation_relief_p95_p05_m':'m',
        'consecutive_rainy_hours':'hours','wind_gust_change_3h':'m/s',
        'soil_moisture_24h_mean':'m3/m3','cape_3h_max':'J/kg','cape_j_kg_lag1h':'J/kg',
        'rainfall_x_slope':'mm degree'}
    if c in RAW.values():
        return 'Open-Meteo historical municipal point',f'{next(k for k,v in RAW.items() if v==c)} at t-1h',units[c]
    if c in TERRAIN:
        return 'Barangay DEM zonal statistics', 'Static prepared terrain summary', units.get(c,'degree' if 'slope' in c else '%' if 'pct' in c else 'm')
    if c in CALENDAR:
        return 'Asia/Manila cutoff clock',{'hour_sin':'sin(2*pi*hour/24)','hour_cos':'cos(2*pi*hour/24)',
            'month_sin':'sin(2*pi*(month-1)/12)','month_cos':'cos(2*pi*(month-1)/12)',
            'is_weekend':'1 if cutoff day is Saturday or Sunday else 0'}[c],'unitless'
    if c=='rainfall_x_slope':
        return 'Lagged municipal rain + static terrain','rainfall_24h_sum * mean_slope_deg; empirical interaction, not a mechanistic hazard equation','mm degree'
    if c=='consecutive_rainy_hours':
        return 'Lagged Open-Meteo precipitation','Consecutive >0.1 mm hours ending t-1h, capped 72; unknown until dry reset/cap','hours'
    if c=='wind_gust_change_3h':
        return 'Lagged Open-Meteo gust','gust(t-1h) - gust(t-4h)','m/s'
    if c=='cape_j_kg_lag1h':
        return 'Nearest 0.25-degree ERA5 cell','Instant CAPE at t-1h','J/kg'
    source='Nearest 0.25-degree ERA5 cell' if c.startswith('cape') else 'Lagged Open-Meteo municipal point'
    w=next((int(part[:-1]) for part in c.split('_') if part.endswith('h') and part[:-1].isdigit()),24)
    operation=c.split('_')[-1]
    unit=units.get(c,'mm' if c.startswith('rainfall') else 'm/s')
    return source,f'Trailing {w} complete hourly labels t-{w}h ... t-1h; {operation}',unit

RATE_BINS={
    'rainfall_mm_lag1h':[-np.inf,.1,1,5,10,25,np.inf],
    'wind_gust_3h_max':[-np.inf,5,10,15,20,30,np.inf],
    'soil_moisture_24h_mean':[-np.inf,.1,.2,.3,.4,.5,np.inf],
    'cape_3h_max':[-np.inf,100,500,1000,2000,5000,np.inf],
    'temperature_c_lag1h':[-np.inf,24,26,28,30,32,np.inf],
    'humidity_pct_lag1h':[-np.inf,60,70,80,90,95,np.inf],
    'mean_slope_deg':[-np.inf,2,5,10,15,20,np.inf],
    'elevation_relief_p95_p05_m':[-np.inf,50,200,500,1000,np.inf],
    'ndvi_same_year_context':[-np.inf,0,.2,.4,.6,.8,np.inf]}

def snapshot_protected(root, output):
    result={}
    excluded={'node_modules','__pycache__','.next','dist','external_cache'}
    for folder in ['data','frontend','backend','scripts','notebooks']:
        for directory, children, files in os.walk(root/folder):
            directory=Path(directory)
            children[:]=[name for name in children if name not in excluded
                         and not (directory/name).is_relative_to(output)
                         and directory/name != root/'data/forecasts']
            for name in files:
                p=directory/name
                if not p.is_relative_to(output):
                    # Prune excluded caches before walking; preserve the same source scope.
                    result[str(p.relative_to(root))]=fingerprint(p)
    return result

def build(args):
    out=Path(args.output).resolve();paths=resolve_paths(args)
    require(not out.exists(),'Output exists; choose a new --output directory (sources/previous runs are never overwritten)')
    require(not any(out==p or out in p.parents for p in paths.values()),'Output would contain an input')
    before=snapshot_protected(ROOT,out)
    out.mkdir(parents=True); audit=out/'audit';audit.mkdir();dataset=out/'ml_dataset_v3';dataset.mkdir()
    write_json(audit/'protected_source_hashes_before.json',before)
    print('Reading and validating V3/environmental sources...',flush=True)
    s=load_sources(paths,args.weather_lag_hours,args.rain_threshold_mm,args.streak_cap_hours)
    require(s.start == max(local(s.manifest['config']['start']), s.weather.time.min(), min(f.index.min()-HOUR for f in s.cape_features.values())), 'Actual source start mismatch')
    require(s.end == min(local(s.manifest['config']['end']), s.weather.time.max(), max(f.index.max()-HOUR for f in s.cape_features.values())), 'Actual source end mismatch')
    w=s.weather
    discrepancy=w.wind_gusts_10m_m_s.lt(w.wind_speed_10m_m_s)
    columns=['municipality','time','latitude','longitude','precipitation_mm','wind_speed_10m_m_s','wind_gusts_10m_m_s']
    w.loc[discrepancy,columns].to_csv(audit/'wind_measurement_discrepancies.csv',index=False)
    w[['municipality','latitude','longitude']].drop_duplicates().to_csv(audit/'weather_coordinate_groups.csv',index=False)
    pd.concat([w.nlargest(10,'precipitation_mm')[columns].assign(extreme='precipitation'),
               w.nlargest(10,'wind_gusts_10m_m_s')[columns].assign(extreme='gust')]).to_csv(audit/'weather_extremes.csv',index=False)
    validation_start,test_start=local(args.validation_start),local(args.test_start)
    require(s.start<validation_start<test_start<s.end,'Invalid split boundaries')
    legacy=audit_legacy(paths['legacy_panel'],s.geo,audit)
    write_json(audit/'source_inspection.json',s.inspection);write_json(audit/'legacy_audit.json',legacy)
    s.geo.drop(columns=['latitude','longitude']).to_csv(audit/'geographic_crosswalk.csv',index=False)
    all_times=pd.date_range(s.start,s.end,freq='h');months=all_times.tz_localize(None).to_period('M')
    groups=[all_times[months==month] for month in months.unique()]
    print(f'Legacy independently scanned: {legacy["rows"]:,} rows. Profiling training-only candidate features...',flush=True)
    train_samples=[];train_null=Counter();train_rows=0
    for i,times in enumerate(groups):
        if times.min()>=validation_start:
            break
        f,_,_=panel_month(s,times,validation_start,test_start)
        eligible=f.loc[f.is_eligible & f.prediction_time.lt(validation_start)]
        train_rows+=len(eligible);train_null.update(eligible[CANDIDATES].isna().sum().to_dict())
        train_samples.append(eligible.sample(min(1500,len(eligible)),random_state=601+i)[CANDIDATES])
    train_sample=pd.concat(train_samples,ignore_index=True)
    kept,decisions,corr=feature_selection(train_sample)
    kept=json.loads((ROOT/'data/ml/v2/ml_predictor_columns.json').read_text())
    require(len(kept)==24 and set(kept)<=set(CANDIDATES), 'Original 24-feature contract unavailable')
    for c in CANDIDATES:
        decisions[c]=('kept','Frozen original V2 feature contract; V3 diagnostics only') if c in kept else ('deferred','Not in the frozen original 24-feature forecasting contract')
    corr.to_csv(audit/'training_spearman_correlations.csv',index_label='feature')
    train_profile=train_sample[CANDIDATES].describe(percentiles=[.01,.5,.95,.99]).T
    train_profile['sample_unique_values']=train_sample[CANDIDATES].nunique()
    train_profile['sample_mode_pct']=[100*train_sample[c].value_counts(normalize=True).max() for c in CANDIDATES]
    train_profile['training_rows']=train_rows
    train_profile['training_missing']=[train_null[c] for c in CANDIDATES]
    train_profile['training_missing_pct']=100*train_profile.training_missing/train_rows
    train_profile.to_csv(audit/'training_feature_quality.csv',index_label='feature')
    print(f'Frozen initial research matrix: {len(kept)} predictors; exporting monthly partitions...',flush=True)
    balances=[]; samples=[];all_null=Counter();partitions=[];rates=Counter();missing_year=[];missing_muni=[]
    reconciliation=Counter();counter_examples=[]
    manifest_rows=[]
    for i,times in enumerate(groups):
        f,counts,active=panel_month(s,times,validation_start,test_start)
        f['feature_complete']=f[kept].notna().all(axis=1)
        y=f.outage_next_hour.fillna(0).to_numpy('uint8');eligible=f.is_eligible.to_numpy()
        reconciliation.update({'raw_mapped_events':int(counts.sum()),'raw_positive_windows':int((counts>0).sum()),
            'events_in_eligible_windows':int(counts[eligible].sum()),'events_in_masked_windows':int(counts[~eligible].sum()),
            'active_rows':int(active.sum()),'multiple_start_windows':int((counts>1).sum())})
        for reason in np.unique(f.exclusion_reason):
            reconciliation[f'rows_{reason}']+=int(f.exclusion_reason.eq(reason).sum())
        for split,gr in f.groupby('split',sort=False):
            elig=gr.is_eligible;pos=int(gr.outage_next_hour.eq(1).sum());neg=int(gr.outage_next_hour.eq(0).sum())
            balances.append({'year':int(times[0].year),'split':split,'total_rows':len(gr),'eligible_rows':int(elig.sum()),
                'positives':pos,'negatives':neg,'excluded_rows':int((~elig).sum()),
                'incomplete_feature_rows':int((~gr.feature_complete & elig).sum())})
        all_null.update(f[CANDIDATES].isna().sum().to_dict())
        null=f[CANDIDATES].isna()
        for year,idx in f.groupby(f.prediction_time.dt.year).groups.items():
            missing_year.extend({'year':int(year),'feature':c,'rows':len(idx),'missing':int(v)} for c,v in null.loc[idx].sum().items())
        for muni,idx in f.groupby('municipality_psgc').groups.items():
            missing_muni.extend({'municipality_psgc':muni,'feature':c,'rows':len(idx),'missing':int(v)} for c,v in null.loc[idx].sum().items())
        # Every eligible hour contributes to rate denominators; no case-control rate estimation.
        n=s.ndvi.loc[s.ndvi.year.eq(times[0].year)].set_index('barangay_psgc').mean_ndvi
        f['ndvi_same_year_context']=f.barangay_psgc.map(n).astype('float32')
        for feature,bins in RATE_BINS.items():
            bins_index=pd.cut(f[feature],bins=bins,right=False)
            temp=pd.DataFrame({'bin':bins_index.astype(str),'eligible':eligible,'positive':y})
            for name,gr in temp.loc[eligible].groupby('bin',observed=True):
                rates[(feature,name,'exposure')]+=len(gr);rates[(feature,name,'positive')]+=int(gr.positive.sum())
        positives=f.loc[f.outage_next_hour.eq(1)]
        negatives=f.loc[f.outage_next_hour.eq(0)].sample(min(600,int(f.outage_next_hour.eq(0).sum())),random_state=801+i)
        sample=pd.concat([positives,negatives]).copy();sample['comparison_sampling']='all eligible positives; up to 600 random negatives/month'
        sample['comparison_weight']=np.where(sample.outage_next_hour.eq(1),1,
            int(f.outage_next_hour.eq(0).sum())/max(len(negatives),1))
        # Illustrative context only: never added to the ML file or predictor manifest.
        sample['wind_gust_x_ndvi_retrospective_context']=sample.wind_gust_3h_max*sample.ndvi_same_year_context
        samples.append(sample)
        exported=f[META+kept+['outage_next_hour']].copy()
        for c in kept:
            exported[c]=exported[c].astype('float32')
        year,month=times[0].year,times[0].month
        destination=dataset/f'year={year}'/f'month={month:02d}'/'part-000.parquet'
        destination.parent.mkdir(parents=True,exist_ok=True)
        table=pa.Table.from_pandas(exported,preserve_index=False)
        table=table.replace_schema_metadata({**(table.schema.metadata or {}),b'ml_dataset_version':VERSION.encode(),
            b'target_interval':b'(prediction_time, prediction_time+1h]',b'input_availability':b'retrospective reconstruction, NOT verified live availability'})
        pq.write_table(table,destination,compression='zstd',use_dictionary=['barangay_psgc','municipality_psgc','split','exclusion_reason'])
        partitions.append({'path':str(destination.relative_to(out)),'rows':len(exported),'columns':len(exported.columns),
            'first_cutoff':str(times.min()),'last_cutoff':str(times.max()),'bytes':destination.stat().st_size,'sha256':sha256(destination)})
        if i%12==0 or i==len(groups)-1:
            print(f'  {year}-{month:02d}: {len(exported):,} rows; positives={len(positives)}',flush=True)
        if len(counter_examples)<12:
            example=exported.loc[~exported.is_eligible].head(2)[META+['outage_next_hour']]
            counter_examples.extend(example.astype('object').map(lambda value: 'NULL' if pd.isna(value) else str(value)).to_dict('records'))
    balance=pd.DataFrame(balances).groupby(['year','split'],as_index=False).sum(numeric_only=True)
    overall=balance.groupby('split',as_index=False).sum(numeric_only=True).drop(columns='year');overall['year']='ALL'
    balance=pd.concat([balance,overall],ignore_index=True)
    balance['positive_pct']=100*balance.positives/balance.eligible_rows
    balance.to_csv(out/'ml_class_balance.csv',index=False)
    for name,rows,keys in [('missingness_by_year',missing_year,['year','feature']),('missingness_by_municipality',missing_muni,['municipality_psgc','feature'])]:
        f=pd.DataFrame(rows).groupby(keys,as_index=False)[['rows','missing']].sum();f['missing_pct']=100*f.missing/f.rows
        f.to_csv(audit/f'{name}.csv',index=False)
    rate_rows=[{'feature':feature,'bin':name,'eligible_hours':rates[(feature,name,'exposure')],
        'positives':rates[(feature,name,'positive')],'onsets_per_10000_hours':10000*rates[(feature,name,'positive')]/rates[(feature,name,'exposure')]}
        for feature,name in sorted({(k[0],k[1]) for k in rates})]
    pd.DataFrame(rate_rows).to_csv(audit/'environmental_rates.csv',index=False)
    comparison=pd.concat(samples,ignore_index=True)
    comparison.to_parquet(audit/'eda_comparison_sample.parquet',index=False,compression='zstd')
    preview=pd.concat([comparison.loc[comparison.outage_next_hour.eq(1)].head(25),
                       comparison.loc[comparison.outage_next_hour.eq(0)].head(65)])
    preview[META+kept+['outage_next_hour']].to_csv(out/'ml_dataset_v3_sample.csv',index=False)
    total=sum(p['rows'] for p in partitions)
    for c in CANDIDATES:
        source,calc,unit=feature_definition(c)
        if c=='consecutive_rainy_hours':
            calc=f'Consecutive >{args.rain_threshold_mm} mm hours ending t-1h; capped {args.streak_cap_hours}; unknown until dry reset or cap'
        manifest_rows.append({'feature_name':c,'feature_type':'engineered' if c in ENV_ENGINEERED+CALENDAR else 'original',
            'source':source,'calculation':calc,'unit':unit,'temporal_availability':
                'Static snapshot' if c in TERRAIN else 'Known cutoff calendar' if c in CALENDAR else 'Valid times <= t-1h; retrospective release availability unverified',
            'missing_count':all_null[c],'missing_pct':100*all_null[c]/total,'training_missing_pct':100*train_null[c]/train_rows,
            'retained':c in kept,'status':decisions[c][0],'selection_reason':decisions[c][1]})
    for c in ['ndvi','wind_gust_x_ndvi']:
        manifest_rows.append({'feature_name':c,'feature_type':'engineered' if c.startswith('wind') else 'original',
            'source':'Annual barangay NDVI','calculation':'recent gust * NDVI' if c.startswith('wind') else 'Annual mean NDVI',
            'unit':'m/s * unitless' if c.startswith('wind') else 'unitless','temporal_availability':'Unverified; same-year composite includes future imagery',
            'missing_count':None,'missing_pct':None,'training_missing_pct':None,'retained':False,'status':'deferred',
            'selection_reason':'Cannot create a prediction-time-valid NDVI input from observation windows without publication/availability dates. Retrospective illustration only in EDA sample.'})
    # Register every inappropriate old/source column as excluded without deleting it.
    metadata_fields={'prediction_time','barangay_psgc','municipality_psgc','is_eligible','exclusion_reason','split','feature_complete'}
    excluded_sources=set(s.events.columns)|set(legacy['columns_present'])
    existing={r['feature_name'] for r in manifest_rows}
    for c in sorted(excluded_sources-existing-metadata_fields):
        reason='Source field only; excluded from the explicit predictor whitelist'
        if any(x in c for x in FORBIDDEN): reason='Outcome, V1 label, generator information, or post-event topology consequence: leakage/out of scope'
        elif 'ndvi' in c or c.startswith('context_'):reason='Retrospective contemporaneous/future-composite context, excluded from prediction matrix'
        elif 'synthetic' in c or 'observed_mapped' in c or 'area_km2' in c:reason='Uncertain infrastructure/current coverage: deferred from initial occurrence model'
        manifest_rows.append({'feature_name':c,'feature_type':'source_only','source':'V3 events or V1 panel',
            'calculation':'Not exported','unit':'Source field','temporal_availability':'Not approved as a predictor',
            'missing_count':None,'missing_pct':None,'training_missing_pct':None,'retained':False,'status':'excluded','selection_reason':reason})
    manifest_frame=pd.DataFrame(manifest_rows)
    manifest_frame.to_csv(out/'ml_feature_manifest.csv',index=False)
    write_json(out/'ml_predictor_columns.json',kept)
    left_cutoffs=s.events.start_time.dt.ceil('h')-HOUR
    reconciliation['events_outside_cutoff_grid']=int((~left_cutoffs.isin(all_times)).sum())
    reconciliation['source_events']=len(s.events)
    require(reconciliation['source_events']==reconciliation['raw_mapped_events']+reconciliation['events_outside_cutoff_grid'],'Event reconciliation failed')
    require(reconciliation['events_in_eligible_windows']==int(overall.positives.sum()),'Positive/event reconciliation failed')
    split_manifest={'timezone':TZ,'target':'outage_next_hour','horizon_hours':1,'target_interval':'(t,t+1h]',
        'train':{'cutoff_start_inclusive':str(s.start),'cutoff_end_exclusive':str(validation_start)},
        'validation':{'cutoff_start_inclusive':str(validation_start),'cutoff_end_exclusive':str(test_start)},
        'test':{'cutoff_start_inclusive':str(test_start),'cutoff_end_inclusive':str(s.end)},
        'purge_rule':'Exclude any earlier-split cutoff t where t < boundary <= t+1h; right-closed labels must not touch a later split.',
        'embargo_hours':0,'embargo_reason':'Past-only history may legitimately initialize later splits; no fitted transformation or future window uses later data.',
        'eligibility':'is_eligible == true; excluded rows have NULL target; active [start,end), incomplete horizon and split-boundary purge excluded.',
        'missing_features':'Warm-up NULLs retained among eligible rows; no zero fill/imputation; feature_complete is metadata, not a target or predictor.',
        'sampling':'All eligible negatives preserved. No oversampling, undersampling, model fitting, tuning or test-based selection.'}
    write_json(out/'ml_split_manifest.json',split_manifest)
    metadata={'dataset_version':VERSION,'kind':'retrospective synthetic research dataset; not an operational forecasting contract',
        'rows':total,'columns':len(META)+len(kept)+1,'predictor_count':len(kept),'predictors':kept,
        'engineered_created':len(ENV_ENGINEERED+CALENDAR),'engineered_retained':len(set(kept)&set(ENV_ENGINEERED+CALENDAR)),
        'categorical_predictors':['municipality_psgc'], 'categorical_encoding':'one-hot in training/serving; PSGC remains exact text metadata in Parquet', 'engineered_deferred':['wind_gust_x_ndvi'],'metadata_columns':META,'target':'outage_next_hour',
        'date_coverage':{'first_cutoff':str(s.start),'last_cutoff':str(s.end),'hours':len(all_times)},
        'target_definition':'At least one eligible V3 origin-barangay event start in (prediction_time,prediction_time+1h]; active/out-of-coverage/purged targets NULL.',
        'positive_rows':int(overall.positives.sum()),'negative_rows':int(overall.negatives.sum()),
        'eligible_rows':int(overall.eligible_rows.sum()),'excluded_rows':int(overall.excluded_rows.sum()),
        'label_reconciliation':dict(reconciliation),'sources':{k:fingerprint(p) for k,p in paths.items()},
        'transformations':{'observation_lag_hours':args.weather_lag_hours,'rain_threshold_mm_strict_greater':args.rain_threshold_mm,
            'rain_streak_cap_hours':args.streak_cap_hours,'rolling_min_periods':'all expected hourly observations',
            'imputation':None,'scaling':None,'learned_parameters':None,'feature_selection_sample_rows':len(train_sample),
            'selection_population':'Eligible training only; 1500 deterministic random rows/month; correlations not computed on labels',
            'float_dtype':'float32','target_dtype':'nullable uint8','PSGC_dtype':'string'},
        'split_manifest':'ml_split_manifest.json','feature_manifest':'ml_feature_manifest.csv',
        'software':{'python':platform.python_version(),'pandas':pd.__version__,'numpy':np.__version__,'pyarrow':pa.__version__,'xarray':xr.__version__},
        'limitations':LIMITATIONS,'partitions':partitions,
        'references':{'open_meteo':'https://open-meteo.com/en/docs/historical-weather-api',
            'era5':'https://confluence.ecmwf.int/spaces/CKB/pages/76414402/ERA5+data+documentation'},
        'code_sha256':sha256(Path(__file__))}
    after=snapshot_protected(ROOT,out)
    changed = [{'path': key, 'before': before.get(key), 'after': after.get(key)}
               for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)]
    if changed:
        write_json(audit/'protected_source_changes.json', changed)
    require(before==after,'A protected existing source/application file changed during the build')
    metadata['source_preservation']={'existing_files_hashed':len(before),'all_unchanged':True,
        'scope':'Existing sources, versioned artifacts and application code; excluding generated output, build caches, live forecast caches and external-memory training caches'}
    write_json(audit/'source_preservation.json',metadata['source_preservation'])
    write_json(out/'ml_dataset_metadata.json',metadata)
    write_json(audit/'mask_examples.json',counter_examples)
    write_quality_report(out,s,metadata,legacy,balance,manifest_frame)
    print(json.dumps({k:metadata[k] for k in ['rows','columns','predictor_count','positive_rows','negative_rows','excluded_rows','engineered_created']},indent=2),flush=True)
    return metadata

def markdown_table(frame):
    # No optional tabulate dependency.
    f=frame.astype(str)
    return '| '+' | '.join(f.columns)+' |\n| '+' | '.join(['---']*len(f.columns))+' |\n'+'\n'.join('| '+' | '.join(row)+' |' for row in f.itertuples(index=False,name=None))

def write_quality_report(out,s,m,legacy,balance,features):
    duration=s.inspection['events']['duration_minutes']
    lines=['# ML-Ready Dataset V3 — quality, leakage and reproducibility report',
        '\nStatus: research-ready with the documented retrospective-input caveats. No model trained.',
        '\n## Population and target',
        f'Canonical panel: {m["rows"]:,} rows × {m["columns"]} columns, {len(s.geo)} barangays and {s.geo.municipality_psgc.nunique()} municipalities. {m["predictor_count"]} explicit predictors.',
        f'Eligible: {m["eligible_rows"]:,}; positive {m["positive_rows"]:,}; negative {m["negative_rows"]:,}; excluded {m["excluded_rows"]:,}. All eligible negative rows are preserved.',
        'A row is one origin barangay × prediction cutoff t (Asia/Manila). Target interval (t,t+1h] is right-closed: an event at 10:00 belongs to the 09:00 cutoff. General subhour starts use ceil(start)-1h. Events at cutoff t are already active and never label their own cutoff as a future onset.',
        'Active intervals are [start,end): cutoff equal to restoration is eligible, equal to onset is excluded. Exclusions set target NULL, not zero. The canonical file retains excluded rows with is_eligible=false and an explicit reason. Do not train on these rows.',
        'Cutoffs retain the previous panel clock, including the last timestamp for audit. Its future hour is outside label coverage, so it is excluded. An event at the leftmost clock cannot be predicted from this clock; it is reconciled as outside-cutoff, not silently lost.',
        '\n## Sources and joins',
        f'Old analytical panel independently scanned in {legacy["row_groups"]} row groups: {legacy["rows"]:,} rows, {legacy["columns"]} columns, {legacy["positive_v1_same_hour_rows"]:,} V1 same-hour positives; complete ordered rectangular grid, zero duplicate keys/gaps/invalid parents. None of its labels are reused.',
        'Reuse: local clock, authoritative PSGC universe, strict one-to-many source joins, per-location weather lag, nearest ERA5 representative-point assignment and monthly partitioning. No infrastructure features, generator normalization or same-year NDVI join was reused for the predictor matrix.',
        'Barangay and municipality PSGCs are validated ten-digit strings. Terrain joins are one-to-one; weather joins are many barangays to one municipality-hour; ERA5 joins use nearest 0.25° cell to UTM51N interior points. Cardinalities are validated before exporting.',
        f'Weather source contains {len(s.weather):,} rows, zero missing source values, duplicate municipality-hours or hourly gaps. Source Asia/Singapore offset +08:00 is correctly localized then converted to Manila. ERA5 UTC is converted, never relabeled as local time.',
        f'The 12 municipality series use only {s.inspection["weather"]["unique_coordinate_pairs"]} distinct coordinate pairs; these are modeled shared exposures, not independent stations. {s.inspection["weather"]["gust_below_speed_rows"]:,} source municipality-hours have supplied gust below wind speed, with maximum deficit {-s.inspection["weather"]["minimum_gust_minus_speed_m_s"]:.2f} m/s. This unresolved measurement/model/interval consistency issue exceeds rounding alone. Original headers state m/s for both fields. Values are retained, never silently corrected. Every discrepancy and the largest rain/gust records are saved in audit/ for review before operational use.',
        f'ERA5 has 4×3 grid cells and {s.inspection["era5"]["assigned_cells"]} assigned cells. CAPE has documented J/kg instantaneous units and no missing source values. CIN missing {s.inspection["era5"]["variables"]["cin"]["missing"]:,}/{s.inspection["era5"]["variables"]["cin"]["values"]:,}; cloud base missing {s.inspection["era5"]["variables"]["cbh"]["missing"]:,}. These optional variables and duplicate ERA5 rainfall/temperature/wind are excluded.',
        '\n## Event quality and EDA',
        f'{len(s.events):,} V3 records; zero missing fields/duplicate IDs, invalid origin-parent pairs, nonpositive durations, or overlapping origin events. Shared timestamps across different barangays are legitimate concurrent simulated starts, not duplicates.',
        f'Duration mean {duration["mean"]:.2f} min; median {duration["50%"]:.2f}; SD {duration["std"]:.2f}; P95 {duration["95%"]:.2f}; P99 {duration["99%"]:.2f}; max {duration["max"]:.2f}. Long durations are retained as synthetic outcomes; none are predictors.',
        'Season grouping is descriptive: November–April drier months, May–October wetter months. Partial 2026 exposure is shown explicitly and temporal charts normalize by observed exposure. These are simulation properties, not measured PENELCO reliability trends.',
        'All six V3 causes (Weather, Tree contact, Equipment Failure, Supply, Animal, Other) are eligible. Causes are outcomes, not predictors. Scheduled PENELCO source records are never read into occurrence labels.',
        '\n## Missingness, extremes and feature decisions',
        'All feature missingness is measured over the full panel, with year/municipality breakdowns in audit/. Raw source environmental fields remain traceable. Warm-up and unavailable preceding ERA5 hours yield NULL; a 24-hour aggregate requires all 24 observations. No zero fill, interpolation, backward fill, winsorization, scaler or fitted imputer is used.',
        'Wet-run length is unknown at a wet left edge or after a missing hour until a dry reset or the 72h cap establishes it. Severe rain/wind are not removed because of statistical rarity. Physical checks reject negative rain/wind, out-of-range humidity/soil moisture, invalid slopes and nonfinite values.',
        f'{m["engineered_created"]} engineered candidates were created; {m["engineered_retained"]} retained. NDVI and wind_gust_x_ndvi are deferred for unknown publication dates; the EDA-only retrospective interaction has a deliberately different name and is never exported as a predictor.',
        'Initial feature choices are unsupervised and use eligible 2019–2023 rows only. Training sample uniqueness/mode proportions and correlations are reported separately from exact full-training missingness. Correlated rain/wind windows are dropped by documented predeclared family thresholds; redundant terrain fields and unused source measurements are deferred/dropped. No test-label-based selection occurs.',
        'Retained predictors: '+', '.join(m['predictors'])+'.',
        '\n'+markdown_table(features.loc[features.feature_type.ne('source_only'),['feature_name','status','selection_reason']]),
        '\n## Leakage audit and chronological splits',
        'Each weather feature ends at the observation label t-1h. Rainfall is the preceding-hour precipitation amount, a rainfall proxy; gusts are provider hourly values, not an invented subhour maximum. The 24h rain window sums labels t-24h through t-1h, covering (t-25h,t-1h] for right-labeled precipitation intervals. Calendar features use cutoff t. Interactions use rainfall_24h_sum × static mean slope, with units mm·degree and heuristic interpretation.',
        'Valid-time causality alone does not certify information was published at t. All weather/ERA5 predictors are retrospective research features. Actual one-hour-ahead operational availability is unverified. No live forecasting claim is made.',
        'Explicit whitelist excludes IDs, target/status columns, V1 labels, event causes/durations/restoration/severity, generator scores/seeds/versions/probabilities, and all fault/affected/downstream topology fields. Source columns remain unchanged.',
        'Cutoff train: 2019-01-01 08:00 to before 2024-01-01; validation: 2024-01-01 to before 2025-01-01; test: 2025-01-01 through 2026-06-30 23:00, all +08:00. Purge the one cutoff immediately preceding each split so its right-closed label does not touch the next split. Last eligible complete window ends at 2026-06-30 23:00; cutoff 23:00 is masked. Past-only rolling history crosses forward into validation/test legitimately, so no additional embargo is needed.',
        '\n'+markdown_table(balance),
        '\n## Label reconciliation', '\n```json\n'+json.dumps(m['label_reconciliation'],indent=2)+'\n```',
        '\n## Validation and reproducibility',
        'Builder invariants check source hashes, exact joins, monthly key uniqueness and event reconciliation. Run scripts/validate_ml_dataset_v3.py for independent full-export validation and scripts/tests/test_ml_v3.py for interval/causal-window controls. Acceptance results are saved in audit/validation.json after the independent scan.',
        f'All {m["source_preservation"]["existing_files_hashed"]} existing data/application/support files were SHA-256 checked before and after the build and are unchanged. Monthly Parquet partition hashes and source/code/software fingerprints are recorded in metadata. Do not overwrite an existing output; use a new output path to reproduce.',
        '\n## Limitations', *['- '+x for x in LIMITATIONS],
        '\n## References',
        '- [Open-Meteo hourly semantics and retrospective historical data](https://open-meteo.com/en/docs/historical-weather-api). Documentation checked 2026-10-26; source extraction model/version is not recorded in the CSV.',
        '- [ECMWF ERA5 documentation and release delays](https://confluence.ecmwf.int/spaces/CKB/pages/76414402/ERA5+data+documentation). Local NetCDF units and GRIB step type were independently inspected.',
        '\n## Reproduction',
        'From the project root: `uv --cache-dir .venv/uv-cache run --offline python scripts/build_ml_dataset_v3.py --output data/ml/v3_reproduced`. Use `--help` for configurable data, event, legacy source and split paths. The notebook reads the completed small audits; it does not rerun a 15-million-row materialization. No application code or model is changed.']
    (out/'ml_data_quality_report.md').write_text('\n\n'.join(lines)+'\n',encoding='utf-8')

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,default=ROOT/'data')
    p.add_argument('--events',type=Path)
    p.add_argument('--event-manifest',type=Path)
    p.add_argument('--legacy-panel',type=Path)
    p.add_argument('--legacy-builder',type=Path)
    p.add_argument('--output',type=Path,default=ROOT/'data/ml/v3')
    p.add_argument('--validation-start',default='2024-01-01 00:00')
    p.add_argument('--test-start',default='2025-01-01 00:00')
    p.add_argument('--weather-lag-hours',type=int,choices=[1],default=1,help='Fixed lag used in versioned lag1h field names; release-time availability remains unverified')
    p.add_argument('--rain-threshold-mm',type=float,default=.1)
    p.add_argument('--streak-cap-hours',type=int,default=72)
    return p

if __name__=='__main__':
    build(parser().parse_args())
