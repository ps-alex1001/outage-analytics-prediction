"""Cached environmental summaries; no raster processing on requests."""
from functools import lru_cache
import json
import math
import pandas as pd
from fastapi import APIRouter, HTTPException
from backend.datasets import DATA_ROOT
router = APIRouter(prefix='/api/spatial', tags=['environment'])

@lru_cache(maxsize=1)
def sources():
    terrain = pd.read_csv(DATA_ROOT / 'terrain/bataan_barangay_terrain.csv', dtype={'barangay_psgc': str, 'municipality_psgc': str})
    ndvi = pd.read_csv(DATA_ROOT / 'vegetation/bataan_barangay_ndvi_yearly.csv', dtype={'barangay_psgc': str})
    preview = json.loads((DATA_ROOT / 'spatial_previews/morong_2025/metadata.json').read_text(encoding='utf-8'))
    return terrain, ndvi.loc[ndvi.year.eq(2025)], preview

def weighted(frame, value, weight):
    valid = frame[value].notna() & frame[weight].gt(0)
    denominator = frame.loc[valid, weight].sum()
    result = (frame.loc[valid, value] * frame.loc[valid, weight]).sum() / denominator if denominator else None
    return float(result) if result is not None and math.isfinite(result) else None

@router.get('/environment')
@lru_cache(maxsize=320)
def environment(municipality_psgc: str | None = None, barangay_psgc: str | None = None):
    try:
        terrain, ndvi, preview = sources()
    except (OSError, ValueError) as exc:
        raise HTTPException(503, 'Environmental summaries unavailable') from exc
    selected = terrain
    if municipality_psgc:
        selected = selected.loc[selected.municipality_psgc.eq(municipality_psgc)]
    if barangay_psgc:
        selected = selected.loc[selected.barangay_psgc.eq(barangay_psgc)]
    if selected.empty:
        raise HTTPException(404, 'Unknown or inconsistent geographic selection')
    vegetation = ndvi.loc[ndvi.barangay_psgc.isin(selected.barangay_psgc)]
    stats = {'mean_ndvi_2025': weighted(vegetation, 'mean_ndvi', 'valid_pixel_count'),
             'mean_slope_deg': weighted(selected, 'mean_slope_deg', 'slope_valid_pixels'),
             'elevation_min_m': None, 'elevation_max_m': None, 'high_ndvi_steep_pct': None}
    basis = 'Valid-pixel-weighted barangay summaries (NDVI 10 m; terrain 30 m). Missing values excluded independently.'
    if municipality_psgc == preview['scope']['municipality_psgc'] and not barangay_psgc:
        native = preview['statistics']
        stats.update(mean_ndvi_2025=native['ndvi_native_10m']['mean'], mean_slope_deg=native['slope_native_30m']['mean'],
                     elevation_min_m=native['elevation_native_30m']['min'], elevation_max_m=native['elevation_native_30m']['max'],
                     high_ndvi_steep_pct=preview['bivariate']['percent_joint_valid_area_rows_slope_columns_ndvi'][2][2])
        basis = 'Validated Morong native-grid statistics; overlap percentage uses jointly valid aligned 30 m cells.'
    return {'area': selected.iloc[0].barangay if barangay_psgc else selected.iloc[0].municipality if municipality_psgc else 'Bataan',
            'ndvi_year': 2025, 'terrain': 'Static DSM-derived surface terrain; acquisition year unspecified',
            'statistics': stats, 'basis': basis,
            'missing_note': 'Exact elevation extrema and joint overlap are validated only for Morong municipality. Percentiles are not extrema.',
            'legend': {'ndvi': preview['rendering']['ndvi'], 'slope': preview['rendering']['slope'], 'combined': preview['bivariate']}}
