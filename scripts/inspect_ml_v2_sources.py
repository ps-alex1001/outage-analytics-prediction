"""Bounded, read-only source discovery for the V2 panel workstream."""
from pathlib import Path
import json
import pandas as pd
import pyarrow.parquet as pq
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]

def inspect():
    out = {}
    for name, rel in {
        'events': 'synthetic/v2_20261024T181237_896469Z/synthetic_outage_events_v2.csv',
        'terrain': 'terrain/bataan_barangay_terrain.csv',
        'ndvi': 'vegetation/bataan_barangay_ndvi_yearly.csv',
    }.items():
        f = pd.read_csv(ROOT/'data'/rel, dtype={'barangay_psgc': str, 'municipality_psgc': str})
        out[name] = {'rows':len(f), 'columns':list(f), 'missing':f.isna().sum().to_dict(),
                     'numeric':f.select_dtypes('number').describe(percentiles=[.01,.5,.95,.99]).to_dict()}
    with xr.open_dataset(ROOT/'data/weather/era5_bataan_2019_01_to_2026_06.nc', engine='h5netcdf') as ds:
        out['era5'] = {'sizes':dict(ds.sizes), 'coords':{c: str(ds[c].values.reshape(-1)[[0,-1]]) for c in ds.coords},
                       'variables': {n: {'attrs':v.attrs, 'missing':int(v.isnull().sum()),'size':v.size,
                                         'min':float(v.min()),'max':float(v.max())} for n,v in ds.data_vars.items()}}
    legacy = Path(r'D:\PyCharmProjects\Outage Prediction\data\processed\ml')
    out['legacy'] = {}
    for p in legacy.glob('bataan_barangay_hour_*.parquet'):
        pf=pq.ParquetFile(p)
        out['legacy'][p.name]={'rows':pf.metadata.num_rows,'row_groups':pf.num_row_groups,'schema':str(pf.schema_arrow),
                             'sample':pf.read_row_group(0, columns=['barangay_psgc','timestamp']).slice(0,2).to_pandas().astype(str).to_dict('records')}
    destination=ROOT/'notebooks/assessment/ml_v2_source_inspection.json'
    destination.write_text(json.dumps(out,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'events':out['events']['rows'],'era5':out['era5'],'legacy':out['legacy']},indent=2,default=str))

if __name__=='__main__':
    inspect()
