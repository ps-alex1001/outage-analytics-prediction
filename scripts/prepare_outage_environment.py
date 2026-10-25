"""One-time, reviewable port of the preserved V1 environmental implementation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'scripts/legacy/generate_synthetic_outages_v1.py').read_text(encoding='utf-8')
source = source[:source.index('\ndef cause_indices(')]
source = source[source.index('from __future__'):]
source = '"""Environmental loader and formula ported from the preserved V1 generator.\n\nOnly paths, GIS adapter and explicit engine are changed. No infrastructure inputs.\n"""\n\n' + source
source = source.replace('import geopandas as gpd', 'from shapely.geometry import shape\nfrom shapely.ops import transform as transform_geometry\nfrom rasterio.warp import transform as transform_coordinates')
source = source.replace('geography: gpd.GeoDataFrame', 'geography: pd.DataFrame')
for old, new in [('data/open_meteo/', 'data/weather/'), ('data/era5/', 'data/weather/'), ('data/geo/', 'data/boundaries/')]:
    source = source.replace(old, new)
start = source.index('    geo = gpd.read_file')
end = source.index('    municipalities = geo[', start)
source = source[:start] + '''    document = json.loads(INPUTS["geography"].read_text(encoding="utf-8"))
    features = document["features"]
    geometry = [shape(f["geometry"]) for f in features]
    require(all(g.is_valid and not g.is_empty and g.geom_type in {"Polygon", "MultiPolygon"}
                for g in geometry), "Invalid, empty or nonpolygon barangay geometry")
    require(len({g.wkb for g in geometry}) == len(geometry), "Duplicate barangay geometries")
    geo = pd.DataFrame([f["properties"] for f in features])
    require(geo.province.eq("Bataan").all(), "Non-Bataan geography")
    report["sources"]["geography"] = validate_table(geo, ["barangay_psgc"], "geography")
    check_psgc(geo, ["barangay_psgc", "municipality_psgc"], "geography")
    def project(src, dst):
        def coordinates(x, y, z=None):
            return transform_coordinates(src, dst, x, y)
        return coordinates
    projected = [transform_geometry(project("EPSG:4326", "EPSG:32651"), g) for g in geometry]
    points = [transform_geometry(project("EPSG:32651", "EPSG:4326"), g.representative_point())
              for g in projected]
    geo["point_latitude"] = [p.y for p in points]
    geo["point_longitude"] = [p.x for p in points]
    geo = geo.sort_values("barangay_psgc").reset_index(drop=True)
    report["source_crs"] = "EPSG:4326; representative points computed in EPSG:32651"
    report["duplicate_geometries"] = 0
''' + source[end:]
source = source.replace('with xr.open_dataset(INPUTS["era5"]) as ds:', 'with xr.open_dataset(INPUTS["era5"], engine="h5netcdf") as ds:')
source = source.replace('        points = geo.to_crs(32651).representative_point().to_crs(4326)', '        points = geo.rename(columns={"point_latitude": "y", "point_longitude": "x"})')
source = source.replace('User-approved 2026 NDVI includes imagery', 'Inherited V1 retrospective 2026 NDVI includes imagery')
(ROOT / 'scripts/outage_environment.py').write_text(source, encoding='utf-8')
