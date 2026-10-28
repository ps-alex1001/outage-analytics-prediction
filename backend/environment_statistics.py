"""Boundary-window statistics using the validated Morong numeric method; no images."""
from functools import lru_cache
import json
from threading import Lock
import numpy as np
import rasterio
from rasterio.features import geometry_mask, geometry_window
from rasterio.windows import Window
from rasterio.warp import transform_geom, reproject, Resampling
from shapely.geometry import shape, mapping
from shapely.ops import unary_union
from backend.datasets import DATA_ROOT
_lock = Lock()

@lru_cache(maxsize=2)
def boundaries(kind):
    return json.loads((DATA_ROOT / f'boundaries/bataan_{kind}.geojson').read_text(encoding='utf-8'))['features']

def horn(z, dx, dy):
    a,b,c=z[:-2,:-2],z[:-2,1:-1],z[:-2,2:]
    d,e,f=z[1:-1,:-2],z[1:-1,1:-1],z[1:-1,2:]
    g,h,i=z[2:,:-2],z[2:,1:-1],z[2:,2:]
    valid=np.logical_and.reduce([np.isfinite(v) for v in [a,b,c,d,e,f,g,h,i]])
    result=np.degrees(np.arctan(np.hypot(((c+2*f+i)-(a+2*d+g))/(8*dx),((g+2*h+i)-(a+2*b+c))/(8*dy))))
    return np.where(valid,result,np.nan)

def physical(source, window, boundless=False):
    return source.read(1,window=window,masked=True,boundless=boundless).astype('float64').filled(np.nan)*source.scales[0]+source.offsets[0]

def mean(values):
    values=values[np.isfinite(values)]
    return float(values.mean()) if values.size else None

@lru_cache(maxsize=320)
def _calculate(municipality, barangay):
    kind='barangays' if barangay else 'municipalities'
    key='barangay_psgc' if barangay else 'municipality_psgc'
    code=barangay or municipality
    features=boundaries(kind)
    selected=[f['geometry'] for f in features if not code or f['properties'][key]==code]
    if not selected: raise ValueError('No matching boundary')
    geometry=selected[0] if len(selected)==1 else mapping(unary_union([shape(g) for g in selected]))
    with rasterio.open(DATA_ROOT/'terrain/bataan_dem_utm51n.tif') as dem, rasterio.open(DATA_ROOT/'vegetation/bataan_ndvi_2025.tif') as ndvi:
        if dem.crs.to_epsg()!=32651 or ndvi.crs!=dem.crs or dem.transform.b or dem.transform.d:
            raise ValueError('Unsupported environmental raster grid')
        geom=transform_geom('EPSG:4326',dem.crs,geometry)
        win=geometry_window(dem,[geom])
        win=Window(int(win.col_off),int(win.row_off),int(win.width),int(win.height))
        affine=dem.window_transform(win)
        size=(int(win.height),int(win.width))
        inside=geometry_mask([geom],out_shape=size,transform=affine,invert=True,all_touched=False)
        z=physical(dem,Window(win.col_off-1,win.row_off-1,win.width+2,win.height+2),True)
        elevation=np.where(inside,z[1:-1,1:-1],np.nan)
        slope=np.where(inside,horn(z,*dem.res),np.nan)
        nw=geometry_window(ndvi,[geom],pad_x=4,pad_y=4)
        raw=physical(ndvi,nw)
        raw[(raw < -1)|(raw > 1)]=np.nan
        native_inside=geometry_mask([geom],out_shape=raw.shape,transform=ndvi.window_transform(nw),invert=True,all_touched=False)
        def warp(values,method):
            out=np.full(size,np.nan)
            reproject(values,out,src_transform=ndvi.window_transform(nw),src_crs=ndvi.crs,src_nodata=np.nan,dst_transform=affine,dst_crs=dem.crs,dst_nodata=np.nan,resampling=method,num_threads=1)
            return out
        aligned=warp(raw,Resampling.average)
        missing=warp(np.where(np.isfinite(raw),0.,1.),Resampling.max)
        aligned[(missing>0)|~np.isfinite(missing)|~inside]=np.nan
        joint=inside&np.isfinite(aligned)&np.isfinite(slope)
        valid_elevation=elevation[np.isfinite(elevation)]
        count=int(joint.sum())
        return {'statistics':{'mean_ndvi_2025':mean(np.where(native_inside,raw,np.nan)), 'mean_slope_deg':mean(slope),
                'elevation_min_m':float(valid_elevation.min()) if valid_elevation.size else None,
                'elevation_max_m':float(valid_elevation.max()) if valid_elevation.size else None,
                'high_ndvi_steep_pct':float(100*np.count_nonzero(joint&(aligned>=.6)&(slope>=15))/count) if count else None},
                'coverage':{'inside_dem_grid_pixels':int(inside.sum()),'valid_elevation_pixels':int(valid_elevation.size),
                'valid_slope_pixels':int(np.isfinite(slope).sum()),'joint_valid_pixels':count,
                'joint_valid_pct':float(100*count/inside.sum()) if inside.any() else None}}

def calculate(municipality=None,barangay=None):
    # Serialize cache misses so concurrent requests do not repeat GIS work.
    with _lock:
        return _calculate(municipality,barangay)
