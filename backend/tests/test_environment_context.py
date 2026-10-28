"""Focused endpoint, aggregation, and raster delivery tests using the generated catalog."""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from fastapi import FastAPI
import asyncio
from urllib.parse import urlsplit
from types import SimpleNamespace
from backend import environment_context as context
from backend.environment_rasters import get_environment_catalog, get_environment_metadata, get_environment_raster
from backend.datasets import DATA_ROOT


class ASGIClient:
    """Exercise the real ASGI router without adding the optional httpx2 dependency."""
    def __init__(self, app): self.app = app

    def get(self, url, headers=None):
        async def request():
            parsed = urlsplit(url); messages = []
            scope = {'type':'http', 'asgi':{'version':'3.0','spec_version':'2.4'},
                'http_version':'1.1', 'method':'GET', 'scheme':'http', 'path':parsed.path,
                'raw_path':parsed.path.encode(), 'query_string':parsed.query.encode(),
                'root_path':'', 'headers':[(k.lower().encode(),v.encode()) for k,v in (headers or {}).items()],
                'client':('127.0.0.1',1234), 'server':('testserver',80)}
            async def receive(): return {'type':'http.request','body':b'','more_body':False}
            async def send(message): messages.append(message)
            await self.app(scope,receive,send)
            start = next(m for m in messages if m['type']=='http.response.start')
            body = b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body')
            return SimpleNamespace(status_code=start['status'],content=body,
                headers={k.decode():v.decode() for k,v in start['headers']},json=lambda:json.loads(body))
        return asyncio.run(request())


class EnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI(); app.include_router(context.router)
        cls.client = ASGIClient(app)

    def setUp(self):
        context._profile.cache_clear()
        self.addCleanup(context._profile.cache_clear)

    def test_morong_profile_matches_approved_statistics_and_corners(self):
        response = self.client.get('/api/spatial/environment?municipality_psgc=0300808000')
        self.assertEqual(response.status_code, 200)
        actual = response.json()
        approved = json.loads((DATA_ROOT/'spatial_previews/morong_2025/metadata.json').read_text())
        self.assertEqual(actual['statistics']['mean_slope_deg'], approved['statistics']['slope_native_30m']['mean'])
        self.assertEqual(actual['statistics']['elevation_min_m'], 0)
        self.assertAlmostEqual(actual['statistics']['high_ndvi_steep_pct'], approved['bivariate']['percent_joint_valid_area_rows_slope_columns_ndvi'][2][2])
        self.assertEqual(actual['images'][0]['coordinates'], approved['rendering']['maplibre_image_coordinates_TL_TR_BR_BL'])
        self.assertEqual(set(actual['images'][0]['maps']), {'ndvi','slope','combined'})

    def test_barangay_extrema_and_joint_stats_are_available_beyond_morong(self):
        data = self.client.get('/api/spatial/environment?municipality_psgc=0300806000&barangay_psgc=0300806012').json()
        self.assertEqual(data['area'], 'Wawa')
        self.assertIsNotNone(data['statistics']['elevation_max_m'])
        self.assertIsNotNone(data['statistics']['high_ndvi_steep_pct'])
        self.assertAlmostEqual(data['coverage']['joint_valid_30m_pct'], 80.85106382978722)
        self.assertEqual(data['coverage']['native_ndvi_valid_pct'], 100)
        self.assertEqual(len(data['images']), 1)
        self.assertEqual(data['images'][0]['maps']['ndvi']['status'], 'partial')

    def test_province_uses_only_twelve_parents_and_weighted_counts(self):
        data = self.client.get('/api/spatial/environment').json()
        codes = sorted(get_environment_catalog()['areas']['municipalities'])
        self.assertEqual([i['psgc'] for i in data['images']], codes)
        self.assertEqual(len(codes), 12)
        metas = [get_environment_metadata('municipalities', c) for c in codes]
        n = sum(m['statistics']['ndvi_native_10m']['valid_pixels'] for m in metas)
        mean = sum(m['statistics']['ndvi_native_10m']['mean']*m['statistics']['ndvi_native_10m']['valid_pixels'] for m in metas)/n
        self.assertAlmostEqual(data['statistics']['mean_ndvi_2025'], mean)
        joint = sum(m['coverage']['joint_valid_30m_cells'] for m in metas)
        high = sum(m['bivariate_counts_rows_slope_columns_ndvi'][2][2] for m in metas)
        self.assertAlmostEqual(data['statistics']['high_ndvi_steep_pct'], 100*high/joint)
        self.assertEqual(data['statistics']['elevation_max_m'], max(m['statistics']['elevation_native_30m']['max'] for m in metas))

    def test_parent_polygon_counts_partition_province_without_double_counting(self):
        import rasterio
        from rasterio.features import geometry_mask, geometry_window
        from rasterio.warp import transform_geom
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union
        features = json.loads((DATA_ROOT/'boundaries/bataan_municipalities.geojson').read_text())['features']
        union = mapping(unary_union([shape(f['geometry']) for f in features]))
        metas = [get_environment_metadata('municipalities', f['properties']['municipality_psgc']) for f in features]
        for name, field in [('terrain/bataan_dem_utm51n.tif','inside_30m_cells'), ('vegetation/bataan_ndvi_2025.tif','inside_native_ndvi_cells')]:
            with rasterio.open(DATA_ROOT/name) as src:
                geom = transform_geom('EPSG:4326', src.crs, union)
                window = geometry_window(src,[geom])
                inside = geometry_mask([geom], out_shape=(int(window.height),int(window.width)), transform=src.window_transform(window), invert=True)
                self.assertEqual(int(inside.sum()), sum(m['coverage'][field] for m in metas))

    def test_invalid_and_inconsistent_selections(self):
        for query,code in [('municipality_psgc=wrong',422),('barangay_psgc=0000000000',404),('municipality_psgc=0300808000&barangay_psgc=0300806012',404)]:
            self.assertEqual(self.client.get('/api/spatial/environment?'+query).status_code,code)
        base='/api/spatial/environment/rasters/'
        for path,code in [('2024/municipalities/0300808000/ndvi',404),('2025/bad/0300808000/ndvi',422),('2025/municipalities/0300808000/invalid',422),('2025/municipalities/invalid/ndvi',422),('2025/municipalities/0000000000/ndvi',404)]:
            self.assertEqual(self.client.get(base+path).status_code,code)

    def test_real_webp_and_conditional_requests(self):
        url='/api/spatial/environment/rasters/2025/municipalities/0300808000/combined'
        response=self.client.get(url)
        asset=get_environment_raster('municipalities','0300808000','combined')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['content-type'],'image/webp')
        self.assertEqual(response.content,asset.path.read_bytes())
        self.assertEqual(response.headers['etag'],asset.etag)
        for etag in [asset.etag,'W/'+asset.etag,'"other", '+asset.etag,'*']:
            cached=self.client.get(url,headers={'If-None-Match':etag})
            self.assertEqual(cached.status_code,304); self.assertEqual(cached.content,b'')
        self.assertEqual(self.client.get(url,headers={'If-None-Match':'"old"'}).status_code,200)

    def test_outage_year_does_not_change_environment(self):
        url='/api/spatial/environment?barangay_psgc=0300808001'
        self.assertEqual(self.client.get(url+'&year=2019').json(),self.client.get(url+'&year=2026').json())

    def test_catalog_failure_and_revision_cache_invalidation(self):
        with patch.object(context,'get_environment_catalog',side_effect=FileNotFoundError):
            self.assertEqual(self.client.get('/api/spatial/environment').status_code,503)
        original=get_environment_metadata('barangays','0300808001')
        changed=deepcopy(original); changed['statistics']['elevation_native_30m']['max']=9999
        with patch.object(context,'get_environment_metadata',side_effect=[original,changed]) as read:
            context._profile('barangays',('0300808001',),('revision-1',))
            context._profile('barangays',('0300808001',),('revision-1',))
            new=context._profile('barangays',('0300808001',),('revision-2',))
            self.assertEqual(read.call_count,2);self.assertEqual(new['statistics']['elevation_max_m'],9999)


if __name__=='__main__': unittest.main()
