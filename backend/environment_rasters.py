"""Read-only lookup for precomputed environmental overlays; no router registration.

The catalog contains no GIS runtime dependency. A future FastAPI route can return
FileResponse(asset.path, media_type=asset.media_type, headers={"ETag": asset.etag}).
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path, PurePosixPath
import re

from .datasets import DATA_ROOT

LEVELS = frozenset({'municipalities', 'barangays'})
RASTER_TYPES = frozenset({'ndvi', 'slope', 'combined'})


@dataclass(frozen=True)
class EnvironmentRaster:
    path: Path
    metadata_path: Path
    geographic_level: str
    psgc: str
    name: str
    municipality_psgc: str
    raster_type: str
    year: int
    dimensions: tuple[int, int]
    bounds_wgs84: tuple[float, float, float, float]
    status: str
    data_available: bool
    size_bytes: int
    sha256: str
    media_type: str = 'image/webp'

    @property
    def etag(self) -> str:
        return f'"{self.sha256}"'


def _root(year: int) -> Path:
    if type(year) is not int or year != 2025:
        raise ValueError('Only the precomputed 2025 environmental catalog is supported')
    return (DATA_ROOT / 'environment_rasters' / str(year)).resolve()


def _validate_key(level: str, psgc: str) -> None:
    if level not in LEVELS:
        raise ValueError('Unknown environmental geographic level')
    if not isinstance(psgc, str) or not re.fullmatch(r'[0-9]{10}', psgc):
        raise ValueError('PSGC must be a 10-digit string, including leading zeroes')


@lru_cache(maxsize=256)
def _json_by_signature(path: str, mtime_ns: int, size: int) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _read_json(path: Path) -> dict:
    stat = path.stat()
    return _json_by_signature(str(path), stat.st_mtime_ns, stat.st_size)


def _asset_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or '\\' in relative:
        raise ValueError('Catalog paths must be relative POSIX paths')
    pure = PurePosixPath(relative)
    if pure.is_absolute() or '..' in pure.parts or ':' in relative:
        raise ValueError('Catalog path escapes its data directory')
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Catalog path escapes its data directory')
    return path


def _area(level: str, psgc: str, year: int) -> tuple[Path, dict]:
    _validate_key(level, psgc)
    root = _root(year)
    catalog = _read_json(root / 'manifest.json')
    if catalog.get('status') != 'complete' or catalog.get('ndvi_year') != year:
        raise ValueError('Environmental export is incomplete or has the wrong year')
    try:
        entry = catalog['areas'][level][psgc]
    except KeyError:
        raise KeyError(f'No environmental assets for {level}/{psgc}') from None
    return root, entry


def get_environment_raster(level: str, psgc: str, raster_type: str, year: int = 2025) -> EnvironmentRaster:
    """O(1) manifest lookup with cached JSON; never opens a GeoTIFF."""
    if raster_type not in RASTER_TYPES:
        raise ValueError('Unknown environmental raster type')
    root, entry = _area(level, psgc, year)
    image = entry['maps'][raster_type]
    if (image['psgc'], image['geographic_level'], image['raster_type'], image['ndvi_year']) != (psgc, level, raster_type, year):
        raise ValueError('Environmental manifest key mismatch')
    if not re.fullmatch(r'[a-f0-9]{64}', image['sha256']):
        raise ValueError('Invalid asset checksum')
    path = _asset_path(root, image['path'])
    if path.stat().st_size != image['bytes']:
        raise ValueError('Environmental asset size differs from the completed catalog')
    return EnvironmentRaster(path=path, metadata_path=_asset_path(root, entry['metadata_path']),
        geographic_level=level, psgc=psgc, name=entry['name'],
        municipality_psgc=entry['municipality_psgc'], raster_type=raster_type, year=year,
        dimensions=tuple(image['dimensions']), bounds_wgs84=tuple(image['bounds_wgs84']),
        status=image['status'], data_available=image['data_available'], size_bytes=image['bytes'], sha256=image['sha256'])


def get_environment_metadata(level: str, psgc: str, year: int = 2025) -> dict:
    """Return isolated metadata, including native/aligned statistics and map corners."""
    root, entry = _area(level, psgc, year)
    return deepcopy(_read_json(_asset_path(root, entry['metadata_path'])))


def get_environment_catalog() -> dict:
    """Cached internal catalog; callers must treat the returned object as read-only."""
    catalog = _read_json(_root(2025) / 'manifest.json')
    if catalog.get('status') != 'complete' or catalog.get('ndvi_year') != 2025:
        raise ValueError('Environmental export is incomplete or has the wrong year')
    return catalog


def environment_revision(level: str, codes: tuple[str, ...]) -> tuple:
    """Invalidate summary responses when catalog or selected metadata changes."""
    root = _root(2025)
    catalog = get_environment_catalog()
    paths = [root / 'manifest.json'] + [
        _asset_path(root, catalog['areas'][level][code]['metadata_path']) for code in codes]
    return tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in paths)
