"""Independent full-export validation; does not call the builder's transforms."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import xarray as xr

ROOT=Path(__file__).resolve().parents[1]
TZ='Asia/Manila'
H=pd.Timedelta(hours=1)

def check(value,message):
    if not value: raise AssertionError(message)

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def independent_weather(source,threshold,cap):
    """NumPy window views provide a separate implementation of trailing windows."""
    f=source.sort_values('time').set_index('time'); result={}
    raw={'precipitation_mm':'rainfall_mm_lag1h','wind_speed_10m_m_s':'wind_speed_m_s_lag1h',
        'wind_gusts_10m_m_s':'wind_gust_m_s_lag1h','soil_moisture_7_to_28cm_m3_m3':'soil_moisture_m3_m3_lag1h',
        'soil_moisture_0_to_7cm_m3_m3':'soil_moisture_shallow_m3_m3_lag1h',
        'temperature_2m_c':'temperature_c_lag1h','relative_humidity_2m_pct':'humidity_pct_lag1h',
        'surface_pressure_hpa':'surface_pressure_hpa_lag1h'}
    for src,dst in raw.items():result[dst]=f[src].to_numpy(float)
    def roll(c,w,op):
        a=f[c].to_numpy(float);v=np.lib.stride_tricks.sliding_window_view(a,w)
        return np.r_[np.full(w-1,np.nan),getattr(np,op)(v,axis=1)]
    for w in [3,6,24]:result[f'rainfall_{w}h_sum']=roll('precipitation_mm',w,'sum')
    result['rainfall_24h_max']=roll('precipitation_mm',24,'max')
    for w in [3,6]:result[f'wind_gust_{w}h_max']=roll('wind_gusts_10m_m_s',w,'max')
    result['wind_speed_3h_mean']=roll('wind_speed_10m_m_s',3,'mean')
    gust=f.wind_gusts_10m_m_s.to_numpy(float)
    result['wind_gust_change_3h']=np.r_[np.full(3,np.nan),gust[3:]-gust[:-3]]
    result['soil_moisture_24h_mean']=roll('soil_moisture_7_to_28cm_m3_m3',24,'mean')
    rain=f.precipitation_mm.to_numpy(float);streak=np.full(len(f),np.nan)
    last_dry=-1
    for i,value in enumerate(rain):
        if value<=threshold:last_dry=i
        distance=i-last_dry
        if last_dry>=0 or distance>=cap:streak[i]=min(distance,cap)
    result['consecutive_rainy_hours']=streak
    return pd.DataFrame(result,index=f.index+H).astype('float32')

def validate(out):
    out=Path(out);m=json.loads((out/'ml_dataset_metadata.json').read_text())
    split=json.loads((out/'ml_split_manifest.json').read_text())
    features=json.loads((out/'ml_predictor_columns.json').read_text())
    manifest=pd.read_csv(out/'ml_feature_manifest.csv')
    check(sha(ROOT/'scripts/build_ml_dataset_v2.py')==m['code_sha256'],'Builder definitions changed after export')
    check(manifest.loc[manifest.retained,'feature_name'].tolist()==features,'Predictor manifest mismatch')
    e=pd.read_csv(m['sources']['events']['path'],dtype={'barangay_psgc':str,'municipality_psgc':str})
    for c in ['start_time','end_time']:e[c]=pd.to_datetime(e[c],utc=True,format='mixed').dt.tz_convert(TZ)
    geo=pd.read_csv(out/'audit/geographic_crosswalk.csv',dtype={'barangay_psgc':str,'municipality_psgc':str}).sort_values('barangay_psgc')
    boundary=pd.DataFrame([f['properties'] for f in json.loads(Path(m['sources']['geography']['path']).read_text(encoding='utf-8'))['features']])
    check(geo.barangay_psgc.tolist()==boundary.barangay_psgc.sort_values().tolist(),'Export geography differs from authoritative boundaries')
    authoritative_parents=boundary.set_index('barangay_psgc').municipality_psgc
    check(geo.municipality_psgc.eq(geo.barangay_psgc.map(authoritative_parents)).all(),'Crosswalk parent differs from authoritative boundaries')
    check(e.is_synthetic.eq(True).all() and not e.cause_category.str.contains('scheduled|planned',case=False).any(),'Ineligible event source')
    check(e.end_time.gt(e.start_time).all(),'Nonpositive source duration')
    observed_cells=e[['barangay_psgc','era5_latitude','era5_longitude']].drop_duplicates()
    check(observed_cells.barangay_psgc.is_unique,'Event spatial assignment varies within barangay')
    comparison=geo.merge(observed_cells,on='barangay_psgc',suffixes=('_panel','_event'),validate='one_to_one')
    for c in ['era5_latitude','era5_longitude']:
        check(np.allclose(comparison[c+'_panel'],comparison[c+'_event']),'ERA5 assignment differs from source V2 static spatial assignment')
    bcodes=geo.barangay_psgc.to_numpy();b=len(geo)
    parents=geo.set_index('barangay_psgc').municipality_psgc
    terrain=pd.read_csv(m['sources']['terrain']['path'],dtype={'barangay_psgc':str}).set_index('barangay_psgc')
    weather=pd.read_csv(m['sources']['weather']['path'])
    weather['time']=pd.to_datetime(weather.time).dt.tz_localize('Asia/Singapore').dt.tz_convert(TZ)
    weather['municipality']=weather.municipality.replace({'Balanga':'City of Balanga'})
    muni_names=geo[['municipality_psgc','municipality']].drop_duplicates().set_index('municipality').municipality_psgc
    weather['municipality_psgc']=weather.municipality.map(muni_names)
    settings=m['transformations']
    wf={key:independent_weather(f,settings['rain_threshold_mm_strict_greater'],settings['rain_streak_cap_hours']) for key,f in weather.groupby('municipality_psgc')}
    cf={}
    with xr.open_dataset(m['sources']['era5']['path'],engine='h5netcdf') as ds:
        era_times=pd.DatetimeIndex(ds.valid_time.values).tz_localize('UTC').tz_convert(TZ)+H
        for cell,g in geo.groupby('era5_cell'):
            raw=ds.cape.sel(latitude=g.era5_latitude.iloc[0],longitude=g.era5_longitude.iloc[0]).values.astype(float)
            maximum=np.r_[np.full(2,np.nan),np.max(np.lib.stride_tricks.sliding_window_view(raw,3),axis=1)]
            cf[cell]=pd.DataFrame({'cape_j_kg_lag1h':raw,'cape_3h_max':maximum},index=era_times).astype('float32')
    start=pd.Timestamp(m['date_coverage']['first_cutoff']);end=pd.Timestamp(m['date_coverage']['last_cutoff'])
    boundaries=[pd.Timestamp(split[x]['cutoff_start_inclusive']) for x in ['validation','test']]
    events={key:f.sort_values('start_time') for key,f in e.groupby('barangay_psgc')}
    totals={'rows':0,'positives':0,'negatives':0,'excluded':0,'eligible':0,'feature_values_checked':0}
    first=last=None;class_by_split={};schema=None
    for part in m['partitions']:
        path=out/part['path'];check(sha(path)==part['sha256'],'Partition hash mismatch')
        pf=pq.ParquetFile(path);table=pf.read();f=table.to_pandas()
        check(f.columns.tolist()==m['metadata_columns']+features+['outage_next_hour'],'Unlisted/exported predictor')
        if schema is None:schema=table.schema
        else:check(schema.equals(table.schema,check_metadata=False),'Schema differs across partitions')
        check(pa.types.is_string(table.schema.field('barangay_psgc').type) or pa.types.is_large_string(table.schema.field('barangay_psgc').type),'PSGC must be text')
        check(pa.types.is_uint8(table.schema.field('outage_next_hour').type),'Target must be nullable uint8')
        for c in features:check(pa.types.is_float32(table.schema.field(c).type),f'Feature dtype {c}')
        tt=pd.DatetimeIndex(f.prediction_time.drop_duplicates())
        check(str(tt.tz)==TZ,'Timestamp timezone')
        check(tt.equals(pd.date_range(tt.min(),tt.max(),freq='h')),'Within-partition timestamp gap')
        check(np.array_equal(f.barangay_psgc.to_numpy(),np.tile(bcodes,len(tt))),'Duplicate/missing/order barangay keys')
        check(pd.DatetimeIndex(f.prediction_time).equals(tt.repeat(b)),'Duplicate/missing hourly keys')
        check(len(f)==len(tt)*b and len(f)==part['rows'],'Row multiplication or missing rows')
        check(last is None or tt.min()==last+H,'Cross-partition time overlap/gap')
        if first is None:first=tt.min()
        last=tt.max()
        check(f.municipality_psgc.eq(f.barangay_psgc.map(parents)).all(),'Invalid parent')
        check(not f[['prediction_time','barangay_psgc','municipality_psgc','is_eligible','exclusion_reason','split','feature_complete']].isna().any().any(),'Missing required metadata')
        # Independent sorted-search interval labels and active state, full population.
        expected_count=np.zeros((len(tt),b),dtype='uint16')
        expected_active=np.zeros((len(tt),b),bool)
        for j,barangay in enumerate(bcodes):
            ev=events.get(barangay)
            active=np.zeros(len(tt),bool);count=np.zeros(len(tt),int)
            if ev is not None:
                ss=pd.DatetimeIndex(ev.start_time).as_unit('ns').asi8;ee=pd.DatetimeIndex(ev.end_time).as_unit('ns').asi8;t=tt.as_unit('ns').asi8
                count=np.searchsorted(ss,(tt+H).as_unit('ns').asi8,side='right')-np.searchsorted(ss,t,side='right')
                index=np.searchsorted(ss,t,side='right')-1
                ok=index>=0;active[ok]=ee[index[ok]]>t[ok]
            expected_count[:,j]=count;expected_active[:,j]=active
        coverage=((tt>=start)&(tt+H<=end))[:,None]
        purge=np.zeros(len(tt),bool)
        for boundary in boundaries:purge|=(tt<boundary)&(tt+H>=boundary)
        eligible=(coverage & ~expected_active & ~purge[:,None]).ravel()
        reason=np.where(~coverage,'incomplete_label_window',np.where(purge[:,None],'split_boundary_purge',
            np.where(expected_active,'active_outage','eligible'))).ravel()
        check(np.array_equal(f.is_eligible.to_numpy(),eligible),'Eligibility')
        check(np.array_equal(f.exclusion_reason.to_numpy(),reason),'Exclusion reason')
        check(np.array_equal(f.outage_next_hour.isna().to_numpy(),~eligible),'Masked targets must be NULL')
        check(np.array_equal(f.loc[eligible,'outage_next_hour'].to_numpy('uint8'),
            (expected_count.ravel()[eligible]>0).astype('uint8')),'Incorrect target window')
        expected_split=np.repeat(np.where(tt<boundaries[0],'train',np.where(tt<boundaries[1],'validation','test')),b)
        check(np.array_equal(f.split.to_numpy(),expected_split),'Split membership')
        # Compare each whole column after independent source-location computation.
        mw={key:df.reindex(tt) for key,df in wf.items()}
        ca={key:df.reindex(tt) for key,df in cf.items()}
        for c in features:
            if c in next(iter(mw.values())):
                expected=np.column_stack([mw[key][c].to_numpy() for key in geo.municipality_psgc]).ravel()
            elif c in next(iter(ca.values())):
                expected=np.column_stack([ca[key][c].to_numpy() for key in geo.era5_cell]).ravel()
            elif c in terrain:
                expected=np.tile(terrain.loc[bcodes,c].to_numpy('float32'),len(tt))
            elif c=='rainfall_x_slope':
                rain=np.column_stack([mw[key].rainfall_24h_sum.to_numpy() for key in geo.municipality_psgc])
                expected=(rain*terrain.loc[bcodes,'mean_slope_deg'].to_numpy('float32')[None,:]).ravel()
            elif c=='hour_sin':expected=np.repeat(np.sin(2*np.pi*tt.hour/24),b)
            elif c=='hour_cos':expected=np.repeat(np.cos(2*np.pi*tt.hour/24),b)
            elif c=='month_sin':expected=np.repeat(np.sin(2*np.pi*(tt.month-1)/12),b)
            elif c=='month_cos':expected=np.repeat(np.cos(2*np.pi*(tt.month-1)/12),b)
            elif c=='is_weekend':expected=np.repeat((tt.dayofweek>=5).astype(float),b)
            else:raise AssertionError(f'No independent formula: {c}')
            check(np.allclose(f[c].to_numpy(),expected,rtol=2e-5,atol=2e-4,equal_nan=True),f'Feature differs from source: {c}')
            totals['feature_values_checked']+=len(f)
        check(np.array_equal(f.feature_complete.to_numpy(),f[features].notna().all(axis=1).to_numpy()),'Completeness flag')
        check(not np.isinf(f[features].to_numpy()).any(),'Infinite feature values')
        for key,gr in f.groupby('split'):
            d=class_by_split.setdefault(key,{'positive':0,'negative':0,'excluded':0})
            d['positive']+=int(gr.outage_next_hour.eq(1).sum());d['negative']+=int(gr.outage_next_hour.eq(0).sum());d['excluded']+=int((~gr.is_eligible).sum())
        totals['rows']+=len(f);totals['positives']+=int(f.outage_next_hour.eq(1).sum());totals['negatives']+=int(f.outage_next_hour.eq(0).sum())
        totals['excluded']+=int((~f.is_eligible).sum());totals['eligible']+=int(f.is_eligible.sum())
        if path.parent.name=='month=01':print('Validated',path.parent.parent.name,flush=True)
    check(first==start and last==end,'Coverage mismatch')
    check(totals['rows']==m['rows'] and totals['positives']==m['positive_rows'] and totals['negatives']==m['negative_rows'],'Metadata totals')
    balance=pd.read_csv(out/'ml_class_balance.csv',dtype={'year':str})
    for key,counts in class_by_split.items():
        record=balance.loc[balance.year.eq('ALL')&balance.split.eq(key)].iloc[0]
        check(record.positives==counts['positive'] and record.negatives==counts['negative'] and record.excluded_rows==counts['excluded'],'Class balance mismatch')
    before=json.loads((out/'audit/protected_source_hashes_before.json').read_text())
    # These files are authored deliverables, not source datasets/application inputs;
    # they can be refined while executing and visually reviewing the notebook.
    authored={'scripts/build_ml_dataset_v2.py','scripts/validate_ml_dataset_v2.py',
        'scripts/inspect_ml_v2_sources.py','scripts/tests/test_ml_v2.py','scripts/README_ml_dataset_v2.md',
        'notebooks/support/build_eda_feature_engineering_v2.py','notebooks/support/execute_eda_feature_engineering_v2.py',
        'notebooks/assessment/ml_v2_source_inspection.json','notebooks/01_eda_feature_engineering_v2.ipynb'}
    protected={name:info for name,info in before.items() if name.replace('\\','/') not in authored}
    changed=[]
    for name,info in protected.items():
        if not (ROOT/name).is_file() or sha(ROOT/name)!=info['sha256']:changed.append(name)
    check(not changed,f'Protected sources changed: {changed}')
    for name,info in m['sources'].items():check(sha(info['path'])==info['sha256'],f'External input changed: {name}')
    result={'status':'passed','independent_full_export_scan':True,**totals,'partitions':len(m['partitions']),
        'columns':len(schema),'split_counts':class_by_split,'source_files_rehashed':len(protected),
        'authored_analysis_files_not_source_inputs':sorted(authored),
        'validator_code_sha256':sha(Path(__file__)),
        'checks':['Every key and parent','Every target via independent sorted interval search',
            'Every active mask, purge and incomplete-horizon exclusion','Every retained feature independently recomputed from sources',
            'All schemas/units through source contract','Predictor manifest and class balance','All source/app hashes'],
        'availability_limit':'Release-time availability of retrospective weather remains unverified; no operational readiness claim.'}
    (out/'audit/validation.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'data/ml/v2')
    validate(p.parse_args().output)
