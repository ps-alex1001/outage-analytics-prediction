"""Catalog-backed environmental profiles and precomputed images; no request-time GIS."""
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from backend.environment_rasters import (
    environment_revision, get_environment_catalog, get_environment_metadata, get_environment_raster,
)

router = APIRouter(prefix='/api/spatial', tags=['environment'])
PSGC = Annotated[str | None, Query(pattern=r'^[0-9]{10}$')]


def _ratio(numerator, denominator):
    return numerator / denominator * 100 if denominator else None


def _weighted(metas, key):
    rows = [m['statistics'][key] for m in metas if m['statistics'][key]['valid_pixels'] > 0]
    count = sum(r['valid_pixels'] for r in rows)
    return sum(r['mean'] * r['valid_pixels'] for r in rows) / count if count else None


@lru_cache(maxsize=320)
def _profile(level: str, codes: tuple[str, ...], revision: tuple):
    catalog = get_environment_catalog()
    metas = [get_environment_metadata(level, code) for code in codes]
    # Province mode uses municipalities only, never a mix of parents and children.
    joint_count = sum(m['coverage']['joint_valid_30m_cells'] for m in metas)
    high_steep = sum(m['bivariate_counts_rows_slope_columns_ndvi'][2][2] for m in metas)
    elevation = [m['statistics']['elevation_native_30m'] for m in metas
                 if m['statistics']['elevation_native_30m']['valid_pixels']]
    native_count = sum(m['coverage']['inside_native_ndvi_cells'] for m in metas)
    cells = sum(m['coverage']['inside_30m_cells'] for m in metas)
    coverage = {
        'native_ndvi_valid_pct': _ratio(sum(m['coverage']['native_ndvi_valid_cells'] for m in metas), native_count),
        'joint_valid_30m_pct': _ratio(joint_count, cells),
        'joint_valid_30m_cells': joint_count, 'inside_30m_cells': cells,
    }
    images = []
    for code, meta in zip(codes, metas):
        images.append({
            'psgc': code, 'name': meta['name'],
            'bounds_wgs84': meta['rendering']['bounds_wgs84'],
            'coordinates': meta['rendering']['maplibre_image_coordinates_TL_TR_BR_BL'],
            'maps': {kind: {
                'url': f'/api/spatial/environment/rasters/2025/{level}/{code}/{kind}',
                'sha256': item['sha256'], 'dimensions': item['dimensions'],
                'status': item['status'], 'data_available': item['data_available'],
                'valid_30m_pct': item['valid_30m_pct'], 'render_valid_pct': item['render_valid_pct'],
            } for kind, item in meta['maps'].items()},
        })
    bounds = [min(i['bounds_wgs84'][0] for i in images), min(i['bounds_wgs84'][1] for i in images),
              max(i['bounds_wgs84'][2] for i in images), max(i['bounds_wgs84'][3] for i in images)]
    style = catalog['style']
    return {
        'selection_key': level + ':' + ','.join(codes),
        'area': metas[0]['name'] if len(codes) == 1 else 'Bataan',
        'geographic_level': level if len(codes) == 1 else 'province', 'ndvi_year': 2025,
        'terrain': 'Static DSM-derived surface terrain; acquisition year unspecified',
        'statistics': {
            'mean_ndvi_2025': _weighted(metas, 'ndvi_native_10m'),
            'mean_slope_deg': _weighted(metas, 'slope_native_30m'),
            'elevation_min_m': min(r['min'] for r in elevation) if elevation else None,
            'elevation_max_m': max(r['max'] for r in elevation) if elevation else None,
            'high_ndvi_steep_pct': _ratio(high_steep, joint_count),
        },
        'basis': 'Valid-pixel-weighted native-grid NDVI (10 m) and Horn slope (30 m). Joint percentages use aligned 30 m cells.',
        'missing_note': 'Missing contributors and polygon edge gaps remain transparent; coverage describes measured analysis cells.',
        'coverage': coverage, 'bounds_wgs84': bounds, 'images': images,
        'legend': {'ndvi': style['ndvi'], 'slope': style['slope'], 'combined': style['bivariate']},
    }


@router.get('/environment')
def environment(municipality_psgc: PSGC = None, barangay_psgc: PSGC = None):
    try:
        catalog = get_environment_catalog()
        if barangay_psgc:
            entry = catalog['areas']['barangays'].get(barangay_psgc)
            if entry is None or municipality_psgc and entry['municipality_psgc'] != municipality_psgc:
                raise HTTPException(404, 'Unknown or inconsistent geographic selection')
            level, codes = 'barangays', (barangay_psgc,)
        elif municipality_psgc:
            if municipality_psgc not in catalog['areas']['municipalities']:
                raise HTTPException(404, 'Unknown municipality')
            level, codes = 'municipalities', (municipality_psgc,)
        else:
            level, codes = 'municipalities', tuple(sorted(catalog['areas']['municipalities']))
        return _profile(level, codes, environment_revision(level, codes))
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, 'Environmental catalog unavailable') from exc


@router.get('/environment/rasters/{year}/{level}/{psgc}/{kind}')
def environmental_image(year: int, level: Literal['municipalities', 'barangays'],
                        psgc: str,
                        kind: Literal['ndvi', 'slope', 'combined'],
                        if_none_match: Annotated[str | None, Header()] = None):
    if year != 2025:
        raise HTTPException(404, 'Only NDVI 2025 environmental maps are available')
    try:
        asset = get_environment_raster(level, psgc, kind, year)
    except KeyError as exc:
        raise HTTPException(404, 'Environmental map not found') from exc
    except ValueError as exc:
        raise HTTPException(422, 'Invalid environmental map request') from exc
    except OSError as exc:
        raise HTTPException(503, 'Environmental map unavailable') from exc
    headers = {'ETag': asset.etag, 'Cache-Control': 'public, max-age=3600, must-revalidate'}
    if if_none_match and any(tag.strip().removeprefix('W/') in (asset.etag, '*') for tag in if_none_match.split(',')):
        return Response(status_code=304, headers=headers)
    return FileResponse(asset.path, media_type=asset.media_type, headers=headers)
