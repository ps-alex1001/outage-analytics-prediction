"""Focused, independent read-back verification of the generated environmental export."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/environment_rasters/2025'


def check(ok, text):
    if not ok:
        raise ValueError(text)


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    from backend.environment_rasters import get_environment_raster, get_environment_metadata, _asset_path, _json_by_signature
    manifest = json.loads((OUT / 'manifest.json').read_text(encoding='utf-8'))
    style = json.loads((OUT / 'style.json').read_text(encoding='utf-8'))
    generated = json.loads((OUT / 'validation.json').read_text(encoding='utf-8'))
    check(manifest['status'] == 'complete' and manifest['ndvi_year'] == 2025, 'Catalog incomplete')
    check(style['bivariate']['ndvi']['breaks'] == [.3, .6], 'NDVI class breaks')
    check(style['bivariate']['slope_degrees']['breaks'] == [5,15], 'Slope class breaks')
    check(style['ndvi']['domain'] == [-1,1] and style['slope']['domain_degrees'] == [0,60], 'Continuous scales')
    from matplotlib.colors import to_rgba
    spec = importlib.util.spec_from_file_location('sample_verify', ROOT/'data/spatial_previews/morong_2025/generate.py')
    sample = importlib.util.module_from_spec(spec); spec.loader.exec_module(sample)
    check(style['bivariate']['palette_rows_low_to_high_slope_columns_low_to_high_ndvi'] == sample.PALETTE, 'Palette changed')
    check(style['ndvi']['color_stops'] == [list(v) for v in sample.NDVI_STOPS], 'NDVI ramp changed')
    check(style['slope']['color_stops'] == [list(v) for v in sample.SLOPE_STOPS], 'Slope ramp changed')
    lut = np.array([[round(v*255) for v in to_rgba(c)] for row in sample.PALETTE for c in row], dtype='uint8')
    rgb_keys = lambda a: (a[...,0].astype('uint32')<<16)+(a[...,1].astype('uint32')<<8)+a[...,2]
    allowed = {
        'combined': set(rgb_keys(lut).tolist()),
        'ndvi': set(rgb_keys(sample.NDVI_CMAP(np.linspace(0,1,256),bytes=True)).tolist()),
        'slope': set(rgb_keys(sample.SLOPE_CMAP(np.linspace(0,1,256),bytes=True)).tolist()),
    }
    count, image_bytes, native_missing, aligned_missing, render_partial = 0, 0, [], [], []
    min_coverage = {}
    for level,key in [('municipalities','municipality_psgc'),('barangays','barangay_psgc')]:
        path = ROOT / ('data/boundaries/bataan_municipalities.geojson' if level=='municipalities' else 'data/boundaries/bataan_barangays.geojson')
        features = json.loads(path.read_text(encoding='utf-8'))['features']
        by_code = {f['properties'][key]:f for f in features}
        check(set(by_code) == set(manifest['areas'][level]), f'PSGC completeness {level}')
        for code,entry in manifest['areas'][level].items():
            check(set(entry['maps']) == {'ndvi','slope','combined'}, 'Raster types missing')
            metadata = get_environment_metadata(level,code)
            check(metadata['psgc'] == code and metadata['ndvi_year']==2025, 'Metadata identifiers')
            width,height = metadata['rendering']['dimensions']
            aff = Affine(*metadata['rendering']['affine'])
            check(abs(abs(aff.a)-abs(aff.e)) < 1e-7 and aff.b==aff.d==0 and aff.a>0 and aff.e<0, 'Square, north-up grid')
            check(metadata['analysis']['crs']=='EPSG:32651' and metadata['analysis']['resolution_m']==[30,30], 'Analysis alignment')
            geom = transform_geom('EPSG:4326','EPSG:3857',by_code[code]['geometry'],precision=-1)
            inside = geometry_mask([geom],out_shape=(height,width),transform=aff,invert=True,all_touched=False)
            arrays = {}
            for kind,descriptor in entry['maps'].items():
                asset = get_environment_raster(level,code,kind)
                check(asset.path == (OUT/descriptor['path']).resolve(), 'Lookup path mismatch')
                check(asset.etag=='"'+descriptor['sha256']+'"','ETag mismatch')
                check(sha(asset.path)==descriptor['sha256'], 'Image checksum mismatch')
                with Image.open(asset.path) as image:
                    check(image.format=='WEBP' and image.size==(width,height), 'File type or dimensions mismatch')
                    rgba = np.asarray(image.convert('RGBA'))
                check(not rgba[:,:,3][~inside].any(), 'Outside-boundary pixels visible')
                check(set(np.unique(rgba[:,:,3])).issubset({0,255}), 'Alpha must be transparent/opaque')
                check(not rgba[rgba[:,:,3]==0,:3].any(), 'Transparent RGB not zero')
                observed = set(np.unique(rgb_keys(rgba[rgba[:,:,3]>0])).tolist())
                check(observed.issubset(allowed[kind]), 'Unapproved map colors')
                check(np.count_nonzero(rgba[:,:,3])==descriptor['render_valid_pixels'], 'Valid pixel count mismatch')
                check(asset.dimensions==(width,height) and list(asset.bounds_wgs84)==metadata['rendering']['bounds_wgs84'], 'Image georeferencing')
                check(descriptor==metadata['maps'][kind], 'Manifest/metadata disagreement')
                arrays[kind]=rgba
                count+=1; image_bytes+=asset.size_bytes
            check(np.array_equal(arrays['combined'][:,:,3]>0,(arrays['ndvi'][:,:,3]>0)&(arrays['slope'][:,:,3]>0)), 'Combined alpha intersection')
            check(sum(sum(row) for row in metadata['bivariate_counts_rows_slope_columns_ndvi'])==metadata['coverage']['joint_valid_30m_cells'], 'Joint statistics counts')
            cover = metadata['coverage']
            if cover['native_ndvi_valid_pct'] < 100 or cover['slope_valid_30m_pct'] < 100:
                native_missing.append({'level':level,'psgc':code,'name':entry['name'],'coverage':cover})
            if cover['joint_valid_30m_pct'] < 100:
                aligned_missing.append({'level':level,'psgc':code,'name':entry['name'],'coverage':cover})
            if any(d['render_valid_pct']<100 for d in entry['maps'].values()):
                render_partial.append(code)
            current=min_coverage.get(level)
            if current is None or cover['joint_valid_30m_pct']<current['coverage_pct']:
                min_coverage[level]={'psgc':code,'name':entry['name'],'coverage_pct':cover['joint_valid_30m_pct']}
            if code=='0300808000':
                for kind,name in [('ndvi','ndvi.png'),('slope','slope.png'),('combined','ndvi_x_slope.png')]:
                    check(np.array_equal(arrays[kind],np.asarray(Image.open(ROOT/'data/spatial_previews/morong_2025'/name).convert('RGBA'))), 'Morong changed')
    check(count==747 and len(list(OUT.rglob('*.webp')))==747, 'Image count')
    for name,descriptor in style['legends'].items():
        check(sha(OUT/descriptor['path'])==descriptor['sha256'], 'Legend checksum')
        check(sha(OUT/descriptor['path'])==sha(ROOT/'data/spatial_previews/morong_2025'/Path(descriptor['path']).name), 'Approved legend changed')
    for key,expected in generated['protected_inputs_before'].items():
        path=ROOT/expected['path']
        check(path.stat().st_size==expected['bytes'] and sha(path)==expected['sha256'], 'Protected source/sample changed')
    rejected=0
    for args in [('bad','0300808000','ndvi'),('municipalities',300808000,'ndvi'),('municipalities','../x','ndvi'),
                 ('municipalities','0300808000','../../x'),('municipalities','0300808000','ndvi',2026),
                 ('municipalities','0000000000','ndvi')]:
        try:get_environment_raster(*args)
        except (ValueError,KeyError):rejected+=1
        else:raise ValueError('Unsafe/unknown key accepted')
    for path in ['../outside.webp','/outside.webp','C:/outside.webp','..\\outside.webp']:
        try:_asset_path(OUT.resolve(),path)
        except ValueError:rejected+=1
        else:raise ValueError('Unsafe catalog path accepted')
    report={'all_checks_passed':True,'images_verified':count,'image_bytes':image_bytes,
        'geographic_codes_exactly_match_boundaries':True,'all_output_checksums_match':True,
        'outside_boundary_visible_pixels':0,'shared_fixed_scales_and_approved_colors':True,
        'combined_alpha_intersection':True,'morong_exact_decoded_match':True,
        'source_and_sample_hashes_still_unchanged':True,'legend_bytes_match_approved_sample':True,
        'lookup_rejected_invalid_inputs':rejected,'lookup_json_cache':str(_json_by_signature.cache_info()),
        'areas_with_native_missing_measurements':native_missing,
        'areas_with_incomplete_aligned_measurements':len(aligned_missing),
        'areas_with_render_edge_or_measurement_gaps':len(render_partial),
        'minimum_joint_analysis_coverage':min_coverage,
        'files_before_this_report':sum(p.is_file() for p in OUT.rglob('*')),
        'bytes_before_this_report':sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file())}
    (OUT/'readback_validation.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2,allow_nan=False),flush=True)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    main()
