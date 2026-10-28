r"""Batch approved Morong environmental maps for Bataan; never modifies sources.
Run: .\.venv\Scripts\python.exe scripts\generate_environment_rasters.py
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import math
import platform
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
import shapely
from PIL import Image, features, __version__ as pillow_version
from rasterio.features import geometry_window
from rasterio.transform import from_bounds, array_bounds
from rasterio.warp import transform_geom, transform, Resampling
from rasterio.windows import Window
from shapely.geometry import shape, box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/environment_rasters/2025'
SAMPLE = ROOT / 'data/spatial_previews/morong_2025'
TYPES = ('ndvi', 'slope', 'combined')
LEVELS = ('municipalities', 'barangays')
MORONG = '0300808000'
spec = importlib.util.spec_from_file_location('approved_morong', SAMPLE / 'generate.py')
approved = importlib.util.module_from_spec(spec)
spec.loader.exec_module(approved)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, allow_nan=False, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.replace(path)


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def relative(path):
    return path.relative_to(OUT).as_posix()


def summary(a, unit):
    if not np.isfinite(a).any():
        return {'unit': unit, 'valid_pixels': 0, **{k: None for k in
                ('min', 'max', 'mean', 'std_population', 'p05', 'p25', 'median', 'p75', 'p90', 'p95')}}
    return approved.stats(a, unit)


def fraction(n, d):
    return float(n / d * 100) if d else None


def status(valid, total):
    return 'no_data' if not valid else ('complete' if valid == total else 'partial')


def render_grid(geometry):
    bounds = shape(geometry).bounds
    sx, sy = bounds[2] - bounds[0], bounds[3] - bounds[1]
    pixel = max(sx, sy) / approved.LONGEST
    width, height = math.ceil(sx / pixel), math.ceil(sy / pixel)
    edges = (bounds[0], bounds[3] - height * pixel, bounds[0] + width * pixel, bounds[3])
    affine = from_bounds(*edges, width, height)
    lon, lat = transform('EPSG:3857', 'EPSG:4326', [edges[0], edges[2]], [edges[1], edges[3]])
    wgs = [lon[0], lat[0], lon[1], lat[1]]
    return (height, width), affine, {
        'crs': 'EPSG:3857', 'dimensions': [width, height],
        'affine': list(affine)[:6], 'resolution_projected_m': [pixel, pixel],
        'bounds_web_mercator': list(edges), 'bounds_wgs84': wgs,
        'maplibre_image_coordinates_TL_TR_BR_BL': [
            [wgs[0], wgs[3]], [wgs[2], wgs[3]], [wgs[2], wgs[1]], [wgs[0], wgs[1]]],
        'north_up': True, 'resampling': 'nearest from polygon-masked common 30m cells',
        'pixel_edges_not_centres': True,
    }


def source_checks():
    yy, xx = np.mgrid[:7, :7]
    plane = 2 * xx * 30. + 3 * yy * 30.
    expected = math.degrees(math.atan(math.hypot(2, 3)))
    require(np.max(np.abs(approved.horn(plane, 30, 30) - expected)) < 1e-10, 'Slope plane check failed')
    require(np.all(approved.horn(np.zeros((5, 5)), 30, 30) == 0), 'Flat slope check failed')
    plane[3, 3] = np.nan
    require(np.isnan(approved.horn(plane, 30, 30)[1:4, 1:4]).all(), 'Slope nodata check failed')
    require(np.array_equal(np.digitize([.299999, .3, .599999, .6], approved.NDVI_BREAKS), [0, 1, 1, 2]), 'NDVI intervals')
    require(np.array_equal(np.digitize([4.9999, 5, 14.9999, 15], approved.SLOPE_BREAKS), [0, 1, 1, 2]), 'Slope intervals')


def main():
    started = time.monotonic()
    require(features.check('webp'), 'Existing Pillow must support WebP')
    require(not (OUT / 'manifest.json').exists(), 'Completed export exists; refusing silent overwrite')
    sample_meta = json.loads((SAMPLE / 'metadata.json').read_text())
    require(digest(SAMPLE / 'generate.py') == sample_meta['reproduction']['script_sha256'], 'Approved sample generator changed')
    protected_paths = {**{k: ROOT / p for k, p in approved.FILES.items()},
                       **{'sample/' + p.name: p for p in SAMPLE.iterdir() if p.is_file()}}
    before = {k: {'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
              for k, p in protected_paths.items()}
    docs = {level: json.loads((ROOT / approved.FILES[key]).read_text(encoding='utf-8'))['features']
            for level, key in [('municipalities', 'municipalities'), ('barangays', 'barangays')]}
    require(len(docs['municipalities']) == 12 and len(docs['barangays']) == 237, 'Unexpected area counts')
    keys = {'municipalities': 'municipality_psgc', 'barangays': 'barangay_psgc'}
    parents = {f['properties']['municipality_psgc']: f for f in docs['municipalities']}
    groups = {code: [] for code in parents}
    for level in LEVELS:
        codes = [f['properties'][keys[level]] for f in docs[level]]
        require(len(codes) == len(set(codes)) and all(isinstance(c, str) and len(c) == 10 and c.isdigit() for c in codes), 'Invalid/duplicate PSGC')
        for f in docs[level]:
            require(shape(f['geometry']).is_valid and not shape(f['geometry']).is_empty, 'Invalid boundary')
            require(f['properties']['municipality_psgc'] in parents, 'Unmatched municipality')
            if level == 'barangays':
                groups[f['properties']['municipality_psgc']].append(shape(f['geometry']))
    for code, f in parents.items():
        require(shape(f['geometry']).equals(unary_union(groups[code])), 'Municipality differs from barangay union')
    terrain_ref = pd.read_csv(ROOT / approved.FILES['terrain_summary'], dtype={'municipality_psgc': str, 'barangay_psgc': str})
    ndvi_ref = pd.read_csv(ROOT / approved.FILES['ndvi_summary'], dtype={'barangay_psgc': str})
    ndvi_ref = ndvi_ref[ndvi_ref.year == 2025]
    require(len(terrain_ref) == len(ndvi_ref) == 237, 'Summary geography/year mismatch')
    source_checks()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'legends').mkdir(exist_ok=True)
    legend_paths = {}
    for kind, name in [('ndvi', 'ndvi_legend.png'), ('slope', 'slope_legend.png'), ('combined', 'bivariate_legend.png')]:
        target = OUT / 'legends' / name
        shutil.copyfile(SAMPLE / name, target)
        legend_paths[kind] = {'path': relative(target), 'bytes': target.stat().st_size, 'sha256': digest(target)}
    style = {
        'schema_version': '1.0', 'approved_sample': 'data/spatial_previews/morong_2025',
        'ndvi': sample_meta['rendering']['ndvi'],
        'slope': {k: v for k, v in sample_meta['rendering']['slope'].items() if k != 'saturation_above_60_degrees_pixels'},
        'bivariate': {k: v for k, v in sample_meta['bivariate'].items()
                      if k not in ('counts_30m_rows_slope_columns_ndvi', 'percent_joint_valid_area_rows_slope_columns_ndvi')},
        'legends': legend_paths,
        'encoding': {'format': 'WebP', 'lossless': True, 'quality': 100, 'method': 4, 'exact': True,
                     'mime_type': 'image/webp', 'alpha': '0 outside boundary or missing; 255 valid'},
        'target_longest_image_dimension': approved.LONGEST,
        'dimension_rounding': 'ceil as approved; floating arithmetic may produce 961 pixels',
        'note': 'Fixed scales/colors; slope color cap 60 degrees, statistics uncapped. Combined is environmental context, not outage risk.',
    }
    write_json(OUT / 'style.json', style)
    print('Deriving slope once and aligning only the 2025 NDVI once for all areas', flush=True)
    lookup = {level: {} for level in LEVELS}
    validations, coverage_issues = [], []
    morong_comparison = None
    lut = np.array([[round(v * 255) for v in approved.to_rgba(c)] for row in approved.PALETTE for c in row], dtype='uint8')
    require(len(np.unique(lut, axis=0)) == 9, 'Legend colors not distinct')
    with rasterio.open(ROOT / approved.FILES['dem']) as dem, rasterio.open(ROOT / approved.FILES['ndvi']) as ndvi:
        require(dem.crs == ndvi.crs == rasterio.CRS.from_epsg(32651), 'Source CRS changed')
        require(dem.crs.is_projected and dem.crs.linear_units == 'metre', 'DEM must be metric')
        require(dem.res == (30., 30.) and ndvi.res == (10., 10.), 'Source resolutions changed')
        sources = {'dem': approved.header(dem), 'ndvi': approved.header(ndvi)}
        for k in ('dem', 'ndvi'):
            require(before[k]['sha256'] == sample_meta['sources'][k]['sha256'], 'Source differs from approved sample')
        metric = {level: {f['properties'][keys[level]]: transform_geom('EPSG:4326', dem.crs, f['geometry'], precision=-1)
                          for f in docs[level]} for level in LEVELS}
        province_window = geometry_window(dem, list(metric['municipalities'].values()))
        province_window = Window(*map(int, [province_window.col_off, province_window.row_off, province_window.width, province_window.height]))
        halo = Window(province_window.col_off - 1, province_window.row_off - 1, province_window.width + 2, province_window.height + 2)
        z = approved.read_physical(dem, halo)
        elev = z[1:-1, 1:-1].copy()
        slopes = approved.horn(z, *dem.res)
        finite_slope = np.argwhere(np.isfinite(slopes))
        sr, sc = finite_slope[len(finite_slope) // 2]
        patch = z[sr:sr+3, sc:sc+3]
        kx = np.array([[-1,0,1],[-2,0,2],[-1,0,1]]) / 240.
        direct = math.degrees(math.atan(math.hypot((patch * kx).sum(), (patch * kx.T).sum())))
        require(abs(direct - slopes[sr, sc]) < 1e-10, 'Source slope kernel check failed')
        del z, finite_slope
        affine = dem.window_transform(province_window)
        native_ndvi = approved.read_physical(ndvi, Window(0, 0, ndvi.width, ndvi.height))
        invalid_range = np.isfinite(native_ndvi) & ((native_ndvi < -1) | (native_ndvi > 1))
        native_ndvi[invalid_range] = np.nan
        aligned = approved.warp(native_ndvi, ndvi.transform, affine, elev.shape, dem.crs, Resampling.average)
        invalid_contributor = (~np.isfinite(native_ndvi)).astype('float32')
        invalid_aligned = approved.warp(invalid_contributor, ndvi.transform, affine, elev.shape, dem.crs, Resampling.max)
        aligned[(invalid_aligned > 0) | ~np.isfinite(invalid_aligned)] = np.nan
        del invalid_contributor, invalid_aligned
        joint = np.isfinite(aligned) & np.isfinite(slopes)
        classified = np.full(elev.shape, np.nan)
        classified[joint] = (1 + np.digitize(aligned, approved.NDVI_BREAKS)
                            + 3 * np.digitize(slopes, approved.SLOPE_BREAKS))[joint]
        require(np.all((slopes[np.isfinite(slopes)] >= 0) & (slopes[np.isfinite(slopes)] < 90)), 'Slope outside degrees range')
        require(np.all((aligned[np.isfinite(aligned)] >= -1) & (aligned[np.isfinite(aligned)] <= 1)), 'NDVI outside physical range')
        processing = {
            'analysis_crs': str(dem.crs), 'analysis_resolution_m': list(dem.res),
            'province_analysis_dimensions': [elev.shape[1], elev.shape[0]], 'province_analysis_affine': list(affine)[:6],
            'slope_derivations': 1, 'ndvi_alignment_operations': 1,
            'slope_method': sample_meta['slope_method'], 'ndvi_alignment': sample_meta['alignment']['ndvi_resampling'],
            'missing_policy': sample_meta['alignment']['missing_policy'],
            'mask_policy': 'Unsimplified polygon; pixel centres, all_touched=False, holes/multipolygons retained. Mask common cells before rendering; mask rendered pixels again.',
            'render_crs': 'EPSG:3857', 'render_resampling': 'nearest', 'year': 2025, 'terrain_year': None,
            'terrain_caveat': sample_meta['time']['terrain_year_note'],
            'statistics_scope': 'Pixel centres within boundary; native 10m NDVI and 30m elevation/slope, plus aligned/joint 30m summaries.',
        }
        order = [('municipalities', parents[MORONG])] + [
            (level, f) for level in LEVELS for f in sorted(docs[level], key=lambda f: f['properties'][keys[level]])
            if not (level == 'municipalities' and f['properties']['municipality_psgc'] == MORONG)]
        for number, (level, feature) in enumerate(order, 1):
            props = feature['properties']; code = props[keys[level]]
            name = props['municipality'] if level == 'municipalities' else props['barangay']
            g = metric[level][code]
            gw = geometry_window(dem, [g])
            r0, c0 = int(gw.row_off - province_window.row_off), int(gw.col_off - province_window.col_off)
            h, w = int(gw.height), int(gw.width)
            region = (slice(r0, r0+h), slice(c0, c0+w))
            local_aff = dem.window_transform(gw)
            inside = approved.mask(g, (h, w), local_aff)
            require(inside.any(), f'No 30m cell centres inside {code}')
            arrays = {k: np.where(inside, a[region], np.nan) for k, a in
                      [('ndvi', aligned), ('slope', slopes), ('combined', classified), ('elevation', elev)]}
            local_joint = np.isfinite(arrays['combined'])
            counts = np.bincount(arrays['combined'][local_joint].astype(int), minlength=10)[1:].reshape(3, 3)
            require(int(counts.sum()) == int(local_joint.sum()), 'Class totals mismatch')
            nw = geometry_window(ndvi, [g], pad_x=4, pad_y=4)
            nr, nc = int(nw.row_off), int(nw.col_off)
            nh, nwidth = int(nw.height), int(nw.width)
            n_aff = ndvi.window_transform(nw)
            n_inside = approved.mask(g, (nh, nwidth), n_aff)
            n_values = np.where(n_inside, native_ndvi[nr:nr+nh, nc:nc+nwidth], np.nan)
            statistics = {
                'ndvi_native_10m': summary(n_values, 'dimensionless NDVI'),
                'elevation_native_30m': summary(arrays['elevation'], 'metres'),
                'slope_native_30m': summary(arrays['slope'], 'degrees'),
                'ndvi_aligned_30m': summary(arrays['ndvi'], 'dimensionless NDVI'),
                'joint_valid_ndvi_30m': summary(np.where(local_joint, arrays['ndvi'], np.nan), 'dimensionless NDVI'),
                'joint_valid_slope_30m': summary(np.where(local_joint, arrays['slope'], np.nan), 'degrees'),
            }
            nd_count = int(n_inside.sum()); cell_count = int(inside.sum())
            cover = {
                'inside_native_ndvi_cells': nd_count,
                'native_ndvi_valid_cells': statistics['ndvi_native_10m']['valid_pixels'],
                'native_ndvi_valid_pct': fraction(statistics['ndvi_native_10m']['valid_pixels'], nd_count),
                'inside_30m_cells': cell_count,
                'ndvi_valid_30m_cells': statistics['ndvi_aligned_30m']['valid_pixels'],
                'ndvi_valid_30m_pct': fraction(statistics['ndvi_aligned_30m']['valid_pixels'], cell_count),
                'slope_valid_30m_cells': statistics['slope_native_30m']['valid_pixels'],
                'slope_valid_30m_pct': fraction(statistics['slope_native_30m']['valid_pixels'], cell_count),
                'elevation_valid_30m_pct': fraction(statistics['elevation_native_30m']['valid_pixels'], cell_count),
                'joint_valid_30m_cells': int(local_joint.sum()), 'joint_valid_30m_pct': fraction(int(local_joint.sum()), cell_count),
                'invalid_native_ndvi_range_cells': int((invalid_range[nr:nr+nh, nc:nc+nwidth] & n_inside).sum()),
                'source_footprints_contain_boundary': all(box(*src.bounds).covers(shape(g)) for src in (dem, ndvi)),
            }
            missing_r, missing_c = np.nonzero(inside & ~np.isfinite(arrays['ndvi']))
            distances = shapely.distance(shapely.points(local_aff.c + (missing_c+.5)*local_aff.a,
                                                        local_aff.f + (missing_r+.5)*local_aff.e), shape(g).boundary)
            cover['missing_ndvi_centres_max_distance_to_boundary_m'] = float(distances.max()) if len(distances) else 0.
            cover['missing_ndvi_centres_over_60m_from_boundary'] = int((distances > 60).sum())
            merc = transform_geom('EPSG:4326', 'EPSG:3857', feature['geometry'], precision=-1)
            image_shape, image_aff, rendering = render_grid(merc)
            render_inside = approved.mask(merc, image_shape, image_aff)
            values = {k: approved.warp(arrays[k], local_aff, image_aff, image_shape, 'EPSG:3857') for k in TYPES}
            for a in values.values():
                a[~render_inside] = np.nan
            values['combined'][~(np.isfinite(values['ndvi']) & np.isfinite(values['slope']))] = np.nan
            rgba = {'ndvi': approved.color_rgba(values['ndvi'], approved.NDVI_CMAP, -1, 1),
                    'slope': approved.color_rgba(values['slope'], approved.SLOPE_CMAP, 0, 60),
                    'combined': np.zeros((*image_shape, 4), dtype='uint8')}
            valid = np.isfinite(values['combined'])
            ids = values['combined'][valid].astype(int)
            rgba['combined'][valid] = lut[ids-1]
            require(np.array_equal(valid, np.isfinite(values['ndvi']) & np.isfinite(values['slope'])), 'Joint alpha mismatch')
            expected_ids = 1 + np.digitize(values['ndvi'][valid], approved.NDVI_BREAKS) + 3*np.digitize(values['slope'][valid], approved.SLOPE_BREAKS)
            require(np.array_equal(expected_ids, ids), 'Rendered class not consistent with variables')
            visible = (rgba['ndvi'][:, :, 3] > 0) | (rgba['slope'][:, :, 3] > 0)
            rr, cc = np.nonzero(visible)
            geom_check = shape(merc); shapely.prepare(geom_check)
            require(shapely.covers(geom_check, shapely.points(image_aff.c+(cc+.5)*image_aff.a,
                                                           image_aff.f+(rr+.5)*image_aff.e)).all(), 'Visible pixel outside geometry')
            area_path = OUT / level / code; area_path.mkdir(parents=True, exist_ok=True)
            maps = {}
            for kind in TYPES:
                image = rgba[kind]
                require(not image[:, :, 3][~render_inside].any(), 'Boundary alpha leak')
                require(set(np.unique(image[:, :, 3])).issubset({0, 255}), 'Unexpected alpha')
                require(not image[image[:, :, 3] == 0, :3].any(), 'Transparent RGB not zero')
                asset = area_path / (kind + '.webp')
                Image.fromarray(image).save(asset, lossless=True, quality=100, method=4, exact=True)
                decoded = np.asarray(Image.open(asset).convert('RGBA'))
                require(np.array_equal(image, decoded), f'Lossless image mismatch: {asset}')
                valid_30m = int(np.isfinite(arrays[kind]).sum())
                opaque = int(np.count_nonzero(image[:, :, 3]))
                maps[kind] = {
                    'geographic_level': level, 'psgc': code, 'name': name,
                    'municipality_psgc': props['municipality_psgc'], 'municipality': props['municipality'],
                    'raster_type': kind, 'ndvi_year': 2025,
                    'path': relative(asset), 'bytes': asset.stat().st_size, 'sha256': digest(asset),
                    'media_type': 'image/webp', 'dimensions': rendering['dimensions'],
                    'bounds_wgs84': rendering['bounds_wgs84'],
                    'status': status(valid_30m, cell_count), 'data_available': bool(valid_30m),
                    'valid_30m_cells': valid_30m, 'valid_30m_pct': fraction(valid_30m, cell_count),
                    'render_valid_pixels': opaque, 'render_inside_boundary_pixels': int(render_inside.sum()),
                    'render_valid_pct': fraction(opaque, int(render_inside.sum())), 'legend': legend_paths[kind]['path'],
                }
            native_ref = ndvi_ref[ndvi_ref.barangay_psgc == code] if level == 'barangays' else ndvi_ref[ndvi_ref.municipality == props['municipality']]
            tr = terrain_ref[terrain_ref.barangay_psgc == code] if level == 'barangays' else terrain_ref[terrain_ref.municipality_psgc == code]
            require(len(native_ref) > 0 and len(tr) > 0, 'Summary reference missing')
            crosscheck = {
                'reference_barangay_rows': len(tr),
                'existing_ndvi_weighted_mean': float(np.average(native_ref.mean_ndvi, weights=native_ref.valid_pixel_count)),
                'existing_elevation_weighted_mean_m': float(np.average(tr.mean_elevation_m, weights=tr.elevation_valid_pixels)),
                'existing_slope_weighted_mean_deg': float(np.average(tr.mean_slope_deg, weights=tr.slope_valid_pixels)),
                'note': 'Raster calculations control this export; existing rounded terrain summaries may differ. Approved Horn slope used consistently.',
            }
            metadata = {
                'schema_version': '1.0', 'ndvi_year': 2025, 'terrain_year': None,
                'geographic_level': level, 'psgc': code, 'name': name,
                'municipality_psgc': props['municipality_psgc'], 'municipality': props['municipality'],
                'boundary': {'bounds_wgs84': list(shape(feature['geometry']).bounds), 'crs': 'OGC:CRS84',
                             'valid': True, 'geometry_type': shape(g).geom_type, 'area_utm_m2': shape(g).area,
                             'source_feature_geometry_sha256': hashlib.sha256(json.dumps(feature['geometry'], sort_keys=True).encode()).hexdigest()},
                'analysis': {'crs': str(dem.crs), 'resolution_m': [30.,30.], 'dimensions': [w,h],
                             'affine': list(local_aff)[:6], 'bounds_utm': list(array_bounds(h,w,local_aff))},
                'rendering': rendering, 'coverage': cover, 'statistics': statistics,
                'bivariate_counts_rows_slope_columns_ndvi': counts.tolist(),
                'bivariate_pct_joint_valid_rows_slope_columns_ndvi': (counts / local_joint.sum() * 100).tolist() if local_joint.any() else None,
                'slope_pixels_above_color_cap_60_deg': int((arrays['slope'] > 60).sum()),
                'existing_summary_crosscheck': crosscheck,
                'maps': maps, 'shared_style_path': '../../style.json', 'shared_manifest_path': '../../manifest.json',
            }
            write_json(area_path / 'metadata.json', metadata)
            lookup[level][code] = {'name': name, 'municipality_psgc': props['municipality_psgc'],
                                   'municipality': props['municipality'], 'ndvi_year': 2025,
                                   'metadata_path': relative(area_path / 'metadata.json'), 'maps': maps}
            if level == 'municipalities' and code == MORONG:
                results = {}
                for kind, old in [('ndvi', 'ndvi.png'), ('slope', 'slope.png'), ('combined', 'ndvi_x_slope.png')]:
                    old_rgba = np.asarray(Image.open(SAMPLE / old).convert('RGBA'))
                    require(old_rgba.shape == rgba[kind].shape, 'Morong dimensions changed')
                    differing = int(np.any(old_rgba != rgba[kind], axis=2).sum())
                    results[kind] = {'different_rgba_pixels': differing, 'identical_decoded_pixels': differing == 0,
                                     'sample_bytes': (SAMPLE / old).stat().st_size, 'batch_webp_bytes': maps[kind]['bytes']}
                    require(differing == 0, f'Morong {kind} differs in {differing} pixels')
                require(rendering['bounds_wgs84'] == sample_meta['rendering']['bounds_wgs84'], 'Morong bounds changed')
                morong_comparison = {'psgc': code, 'bounds_exact_match': True, 'dimensions_exact_match': True, 'maps': results,
                                     'alignment_change': 'Single province-wide alignment reused; decoded pixels equal approved window-based sample.'}
                print('Morong gate passed: all three decoded WebPs exactly match approved PNG pixels', flush=True)
            issues = {k: v for k,v in maps.items() if v['status'] != 'complete' or v['render_valid_pixels'] < v['render_inside_boundary_pixels']}
            if issues:
                coverage_issues.append({'level': level, 'psgc': code, 'name': name, 'municipality': props['municipality'],
                                        'coverage': cover, 'maps': {k: {'status': v['status'], 'valid_30m_pct': v['valid_30m_pct'],
                                                                     'render_valid_pct': v['render_valid_pct']} for k,v in issues.items()}})
            validations.append({'level': level, 'psgc': code, 'all_checks_passed': True,
                                'outside_boundary_visible_pixels': 0, 'lossless_roundtrip_maps': 3,
                                'classification_and_joint_alpha_verified': True,
                                'visible_pixel_centres_independently_checked': int(visible.sum())})
            if number == 1 or number % 10 == 0 or number == 249:
                print(f'{number}/249 areas complete; {level}/{code} {name}; joint coverage {cover["joint_valid_30m_pct"]:.3f}%; elapsed {time.monotonic()-started:.1f}s', flush=True)
    after = {k: {'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
             for k, p in protected_paths.items()}
    require(before == after, 'A protected input or approved sample changed')
    require(len(lookup['municipalities']) == 12 and len(lookup['barangays']) == 237, 'Incomplete geography')
    require(len(list(OUT.rglob('*.webp'))) == 747, 'Unexpected output map count')
    coverage = {
        'meaning': 'partial means missing aligned measurement cells; render_valid_pct also reports edge masking differences between grids. No measurement is filled.',
        'areas_with_any_incomplete_analysis_or_render_coverage': len(coverage_issues),
        'areas_with_no_data': [dict(level=v['level'], psgc=v['psgc'], name=v['name']) for v in coverage_issues
                               if any(m['status'] == 'no_data' for m in v['maps'].values())], 'areas': coverage_issues,
    }
    write_json(OUT / 'coverage.json', coverage)
    validation = {
        'all_checks_passed': True, 'expected_counts': {'municipalities': 12, 'barangays': 237, 'maps_per_area': 3, 'maps_total': 747},
        'actual_counts': {'municipalities': len(lookup['municipalities']), 'barangays': len(lookup['barangays']), 'maps_total': 747},
        'source_and_sample_hashes_unchanged': True, 'protected_inputs_before': before, 'protected_inputs_after': after,
        'crs_metric_slope_and_common_alignment_verified': True,
        'horn_flat_plane_actual_kernel_and_nodata_checks': True,
        'threshold_inclusivity_checks': True, 'nine_unique_legend_colors': True,
        'morong_comparison': morong_comparison, 'area_checks': validations, 'browser_testing_performed': False,
        'software': {'python': platform.python_version(), 'numpy': np.__version__, 'rasterio': rasterio.__version__,
                     'gdal': rasterio.__gdal_version__, 'pillow': pillow_version, 'shapely': shapely.__version__},
        'generator_path': Path(__file__).relative_to(ROOT).as_posix(), 'generator_sha256': digest(Path(__file__)),
        'generation_elapsed_seconds': round(time.monotonic()-started, 3),
    }
    write_json(OUT / 'validation.json', validation)
    manifest = {
        'schema_version': '1.0', 'status': 'complete', 'ndvi_year': 2025,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'path_base': 'data/environment_rasters/2025', 'asset_paths_relative_to': 'this manifest directory',
        'geographic_levels': {'municipalities': 12, 'barangays': 237}, 'raster_types': list(TYPES), 'image_count': 747,
        'sources': {k:v for k,v in before.items() if not k.startswith('sample/')}, 'source_rasters': sources,
        'processing': processing, 'style_path': 'style.json', 'style': style,
        'coverage_report_path': 'coverage.json', 'validation_path': 'validation.json',
        'backend_retrieval': {'lookup': 'areas[level][10-digit PSGC].maps[raster_type]',
                              'media_type': 'image/webp', 'etag': 'quoted sha256',
                              'statistics': 'per-area metadata.json; no request-time GIS processing'}, 'areas': lookup,
    }
    write_json(OUT / 'manifest.json', manifest)
    image_bytes = sum(m['bytes'] for areas in lookup.values() for entry in areas.values() for m in entry['maps'].values())
    municipality_rows = []
    for code, entry in sorted(lookup['municipalities'].items()):
        m = json.loads((OUT / entry['metadata_path']).read_text(encoding='utf-8'))
        municipality_rows.append(f"| {code} | {entry['name']} | {m['coverage']['native_ndvi_valid_pct']:.3f}% | {m['coverage']['joint_valid_30m_pct']:.3f}% |")
    lines = ['# Bataan 2025 environmental raster export', '',
        '747 lossless WebP overlays: 12 municipalities/city and 237 barangays, three maps per area. Original rasters, boundaries, summaries and the approved Morong sample are preserved.', '',
        '## Retrieval', '',
        'IDs are 10-digit PSGC strings. Look up `manifest.json` using `areas[level][psgc].maps[kind]`. Levels: `municipalities`, `barangays`. Kinds: `ndvi`, `slope`, `combined`. All paths are relative to this directory. Entries include bounds, dimensions, status, availability, bytes, SHA-256 and parent municipality. Per-area metadata adds statistics and MapLibre TL/TR/BR/BL corners.', '',
        'The isolated `backend/environment_rasters.py` helper provides cached, allowlisted lookup. No router is registered and no existing API or frontend code is changed. A future FastAPI route may use FileResponse with descriptor.media_type and descriptor.etag. Request-time raster processing is unnecessary.', '',
        '## Approved processing', '',
        'The unchanged Morong generator supplies Horn slope, masking, resampling, colormaps and RGBA rendering. Slope is derived once on native metric EPSG:32651 30 m DEM with a one-cell halo and all nine samples required. Only 2025 10 m NDVI is read; it is area-averaged once onto exact DEM cells. Any invalid source contributor invalidates its aggregate NDVI cell.', '',
        'Each exact polygon is masked on the common grid, sampled with nearest-neighbor onto north-up square-pixel EPSG:3857, then masked again against the transformed polygon. Transparent RGB is zero. All maps retain the approved 960-pixel target longest dimension (a floating-point ceiling can produce 961 pixels); upsampling creates no new measured detail.', '',
        'NDVI fixed scale: [-1,1]. Slope fixed color scale: 0–60 degrees; higher measurements share the final color and statistics remain uncapped. NDVI breaks: 0.3 and 0.6. Slope breaks: 5 and 15 degrees. The nine colors and legend orientation are unchanged. Combined is environmental context, not outage risk or an arithmetic product.', '',
        'Shared legends are byte-for-byte copies of approved legends; `style.json` documents colors and thresholds. Terrain is Copernicus GLO-30 DSM. Metres are inferred from provenance and existing summaries because band units are unspecified. Terrain acquisition year/vertical datum are unrecorded; surface slope can include canopy/buildings.', '',
        '## Coverage', '',
        'Native, aligned and rendered coverage are separate. Conservative invalid-contributor rejection and masking at two resolutions can leave transparent edge pixels even when native polygon-centre measurements are complete. `coverage.json` lists all incomplete areas and missing-cell distances; no measurements are filled.', '',
        '| Municipality PSGC | Municipality/city | Native NDVI | Joint 30 m |', '|---|---|---:|---:|', *municipality_rows, '',
        '## Validation', '',
        'All 747 files decode exactly to intended RGBA. All visible pixel centres were independently checked against true polygons, including holes/multipolygons. Units, physical ranges, common-grid class membership, joint alpha, source preservation and complete PSGC sets were checked. Slope formula and threshold boundary checks passed. No browser testing was performed.', '',
        'All three new Morong WebPs have exactly the same decoded RGBA pixels, dimensions and geographic bounds as the approved sample PNGs. Existing rounded terrain summaries can differ; every output uses the same approved Horn method.', '',
        f'Total overlay payload: {image_bytes:,} bytes ({image_bytes/1024**2:.2f} MiB). Per-image sizes are in the manifest.', '',
        '## Reproduction', '',
        'Run `.\\.venv\\Scripts\\python.exe scripts\\generate_environment_rasters.py` from the project root. It refuses to overwrite an existing completed export. Inputs and approved generator are fingerprinted. No packages were installed.', '']
    (OUT / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'complete': True, 'areas': 249, 'images': 747, 'image_bytes': image_bytes,
        'directory_file_count': sum(p.is_file() for p in OUT.rglob('*')),
        'directory_total_bytes': sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file()),
        'areas_with_coverage_notes': len(coverage_issues), 'morong_comparison': morong_comparison,
        'elapsed_seconds': round(time.monotonic()-started, 1)}, indent=2), flush=True)


if __name__ == '__main__':
    main()
