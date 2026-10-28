"""Windowed municipal and ERA5 weather for the Spatial Analysis event selection.

Verified export: hourly local clocks, Asia/Singapore / UTC+8, 2019-01–2026-06.
Column suffixes specify units; both requested soil layers are volumetric m³/m³.
The CSV is grouped by municipality, then time. A lazy sparse byte index retains
only source metadata and one seek position per day, never the weather history.
"""
from bisect import bisect_right
import csv
from functools import lru_cache
import math
from threading import Lock

from fastapi import APIRouter, HTTPException
import pandas as pd
import numpy as np
import xarray as xr

from backend.datasets import DATA_ROOT
from backend.impacts import dataset

router = APIRouter(prefix='/api/impacts', tags=['historical weather'])
WEATHER_PATH = DATA_ROOT / 'weather/open_meteo_bataan_2019_01_to_2026_06.csv'
ERA5_PATH = DATA_ROOT / 'weather/era5_bataan_2019_01_to_2026_06.nc'
MANILA = 'Asia/Manila'
FIELDS = {
    'precipitation_mm': ('precipitation_mm', 'mm', 1),
    'wind_speed_kmh': ('wind_speed_10m_m_s', 'km/h', 3.6),
    'wind_gusts_kmh': ('wind_gusts_10m_m_s', 'km/h', 3.6),
    'temperature_c': ('temperature_2m_c', '°C', 1),
    'relative_humidity_pct': ('relative_humidity_2m_pct', '%', 1),
    'soil_moisture_0_7': ('soil_moisture_0_to_7cm_m3_m3', 'm³/m³', 1),
    'soil_moisture_7_28': ('soil_moisture_7_to_28cm_m3_m3', 'm³/m³', 1),
    'surface_pressure_hpa': ('surface_pressure_hpa', 'hPa', 1),
}
# Inspected NetCDF units and instantaneous step semantics. Total Totals is an
# atmospheric stability index in K, not a temperature to convert to Celsius.
ERA5_FIELDS = {
    'cape_j_kg': ('cape', 'J kg**-1', 'J/kg'),
    'column_water_vapour_kg_m2': ('tcwv', 'kg m**-2', 'kg/m²'),
    'total_totals_index_k': ('totalx', 'K', 'K'),
    'cloud_base_height_m': ('cbh', 'm', 'm'),
}
ERA5_PRIORITIES = {
    'sp': 'Surface atmospheric pressure', 'tcc': 'Total cloud cover',
    'd2m': 'Dew-point temperature', 'ssrd': 'Surface solar radiation',
}
# Exact verified source-name alias; no geographic nearest-neighbour substitution.
MUNICIPALITY_ALIASES = {'City of Balanga': 'Balanga'}
_index_lock = Lock()
_era5_lock = Lock()


@lru_cache(maxsize=1)
def _build_index(path, modified_ns, size):
    del modified_ns, size  # Cache invalidation keys for the source file.
    locations = {}
    with path.open('rb') as source:
        header = source.readline()
        columns = next(csv.reader([header.decode('utf-8-sig')]))
        required = ['municipality', 'latitude', 'longitude', 'utc_offset_seconds', 'timezone', 'time']
        if not set(required).issubset(columns):
            raise ValueError('Historical weather location/time columns are missing')
        positions = {name: columns.index(name) for name in required}
        previous_municipality = None
        offset = len(header)
        for raw in source:
            row = next(csv.reader([raw.decode('utf-8')]))
            municipality, lat, lon, utc_offset, zone, time = [row[positions[k]] for k in required]
            if utc_offset not in {'28800', '28800.0'} or zone not in {'Asia/Singapore', MANILA}:
                raise ValueError('Unexpected historical weather timezone')
            # The inspected export uses sortable YYYY-MM-DD HH:00:00 clocks.
            if len(time) != 19 or time[13:] != ':00:00':
                raise ValueError('Historical weather timestamps must be hourly aligned')
            coordinates = (float(lat), float(lon))
            if municipality not in locations:
                locations[municipality] = {'coordinates': coordinates, 'zone': zone,
                                           'first': time, 'last': None, 'count': 0,
                                           'times': [], 'offsets': []}
            location = locations[municipality]
            if (location['coordinates'] != coordinates or location['zone'] != zone
                    or (location['last'] is not None and (time <= location['last']
                        or previous_municipality != municipality))):
                raise ValueError('Historical weather location/time keys are ambiguous or unordered')
            if location['count'] % 24 == 0:
                location['times'].append(time)
                location['offsets'].append(offset)
            location['last'] = time
            location['count'] += 1
            previous_municipality = municipality
            offset += len(raw)
    return columns, locations


def read_window(municipality, start, end):
    stat = WEATHER_PATH.stat()
    with _index_lock:
        columns, locations = _build_index(WEATHER_PATH, stat.st_mtime_ns, stat.st_size)
    location = locations.get(municipality)
    if location is None:
        return {}, None, columns
    first, last = start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')
    if last < location['first'] or first > location['last']:
        return {}, location, columns
    checkpoint = max(0, bisect_right(location['times'], first) - 1)
    records = {}
    with WEATHER_PATH.open('rb') as source:
        source.seek(location['offsets'][checkpoint])
        for raw in source:
            row = dict(zip(columns, next(csv.reader([raw.decode('utf-8')]))))
            if row['municipality'] != municipality or row['time'] > last:
                break
            if row['time'] >= first:
                records[row['time']] = row
    return records, location, columns


def measurement(row, column, multiplier):
    try:
        value = float(row.get(column, ''))
        return value * multiplier if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def read_era5_window(location, start, end):
    """Select one nearest grid cell and the UTC window before loading values.

    The on-disk HDF5 chunks span the time dimension; decompression is managed by
    HDF5. Only the selected variables/cell/window are materialized by xarray and
    returned to the client, never the full NetCDF or a full time-series cache.
    """
    metadata = {'file': ERA5_PATH.name, 'status': 'unavailable', 'timezone': 'UTC',
                'resolution': 'hourly', 'grid_degrees': 0.25, 'latitude': None, 'longitude': None,
                'match': 'nearest_grid_cell_to_municipal_weather_point',
                'unavailable_variables': [], 'reason': None}
    if location is None:
        metadata.update(status='unmatched', reason='No municipal weather point to match to ERA5')
        return {}, metadata, {}
    rows, measurements = {}, {}
    try:
        with _era5_lock, xr.open_dataset(ERA5_PATH, engine='h5netcdf') as source:
            metadata['unavailable_variables'] = [name for column, name in ERA5_PRIORITIES.items()
                                                  if column not in source.data_vars]
            if not {'latitude', 'longitude', 'valid_time'}.issubset(source.coords):
                raise ValueError('ERA5 spatial/time coordinates are missing')
            latitudes, longitudes = source.latitude.values, source.longitude.values
            if (latitudes.ndim != 1 or longitudes.ndim != 1 or len(latitudes) < 2 or len(longitudes) < 2
                    or not np.allclose(np.abs(np.diff(latitudes)), 0.25)
                    or not np.allclose(np.abs(np.diff(longitudes)), 0.25)):
                raise ValueError('ERA5 grid differs from the inspected 0.25-degree export')
            lat, lon = location['coordinates']
            if not (latitudes.min() <= lat <= latitudes.max() and longitudes.min() <= lon <= longitudes.max()):
                metadata.update(status='unmatched', reason='Municipal weather point lies outside the ERA5 grid')
                return rows, metadata, measurements
            grid_lat = float(latitudes[np.abs(latitudes - lat).argmin()])
            grid_lon = float(longitudes[np.abs(longitudes - lon).argmin()])
            metadata.update(latitude=grid_lat, longitude=grid_lon,
                            reference_latitude=lat, reference_longitude=lon)
            for key, (column, source_unit, unit) in ERA5_FIELDS.items():
                variable = source.get(column)
                available = (variable is not None and variable.dims == ('valid_time', 'latitude', 'longitude')
                             and variable.attrs.get('units') == source_unit
                             and variable.attrs.get('GRIB_stepType') == 'instant')
                measurements[key] = {'column': column, 'unit': unit, 'source': 'era5',
                                     'source_unit': variable.attrs.get('units') if variable is not None else None,
                                     'available': available,
                                     'reason': None if available else 'Variable absent or incompatible units/interval metadata'}
            available_columns = [details['column'] for details in measurements.values() if details['available']]
            if not available_columns:
                metadata['reason'] = 'No supported ERA5 variables are available'
                return rows, metadata, measurements
            # CF epoch timestamps in this export are UTC; municipal CSV clocks
            # are UTC+8. Convert the query, never reinterpret UTC as Manila.
            first, last = [t.tz_convert('UTC').tz_localize(None).to_datetime64() for t in (start, end)]
            selected = source[available_columns].sel(
                valid_time=slice(first, last), latitude=grid_lat, longitude=grid_lon).load()
            times = pd.DatetimeIndex(selected.valid_time.values).tz_localize('UTC').tz_convert(MANILA)
            if (not times.is_unique or not times.is_monotonic_increasing
                    or not times.equals(times.floor('h'))):
                raise ValueError('ERA5 timestamps are not unique ordered hourly labels')
            for i, time in enumerate(times):
                row = {}
                for key, details in measurements.items():
                    if details['available']:
                        value = float(selected[details['column']].values[i])
                        sentinel = source[details['column']].attrs.get('GRIB_missingValue')
                        row[key] = value if math.isfinite(value) and value != sentinel else None
                rows[time.isoformat()] = row
            metadata['status'] = 'available' if all(d['available'] for d in measurements.values()) else 'partial'
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        metadata.update(status='unavailable', reason='ERA5 source is unavailable or has incompatible metadata')
        # Preserve the original municipal charts when this independent source fails.
        return {}, metadata, {}
    return rows, metadata, measurements


@router.get('/events/{outage_id}/weather')
def event_weather(outage_id: str):
    events, _, _ = dataset()
    if outage_id not in events.index:
        raise HTTPException(404, 'Unknown synthetic outage')
    event = events.loc[outage_id]
    try:
        start, end = pd.Timestamp(event['start_time']), pd.Timestamp(event['end_time'])
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError('Invalid outage period')
        start, end = start.tz_convert(MANILA), end.tz_convert(MANILA)
        window_start, window_end = start - pd.Timedelta(hours=6), end + pd.Timedelta(hours=3)
        municipality = MUNICIPALITY_ALIASES.get(event['municipality'], event['municipality'])
        rows, location, columns = read_window(municipality, window_start, window_end)
    except (OSError, ValueError, KeyError, IndexError, UnicodeError, csv.Error) as exc:
        raise HTTPException(503, 'Historical weather source is unavailable or has an incompatible schema') from exc

    # Keep hourly labels inside the exact requested bounds; no interpolation,
    # zero filling, or rounding the outage boundaries to the nearest hour.
    era5_rows, era5, era5_measurements = read_era5_window(location, window_start, window_end)
    points = []
    for time in pd.date_range(window_start.ceil('h'), window_end.floor('h'), freq='h'):
        row = rows.get(time.strftime('%Y-%m-%d %H:%M:%S'), {})
        points.append({'time': time.isoformat(), **{
            key: measurement(row, column, multiplier)
            for key, (column, _, multiplier) in FIELDS.items()},
            **{key: era5_rows.get(time.isoformat(), {}).get(key) for key in ERA5_FIELDS}})
    missing = {key: sum(point[key] is None for point in points) for key in (*FIELDS, *ERA5_FIELDS)}
    all_missing = not points or all(count == len(points) for count in missing.values())
    status = 'missing' if all_missing else 'partial' if any(missing.values()) else 'complete'
    return {
        'outage_id': outage_id, 'status': status, 'timezone': MANILA,
        'outage': {'start': start.isoformat(), 'end': end.isoformat()},
        'window': {'start': window_start.isoformat(), 'end': window_end.isoformat()},
        'source': {'file': WEATHER_PATH.name, 'resolution': 'hourly',
                   'timezone': location['zone'] if location else None,
                   'municipality': municipality, 'match': 'municipality' if location else 'unmatched',
                   'latitude': location['coordinates'][0] if location else None,
                   'longitude': location['coordinates'][1] if location else None},
        'era5': era5,
        'measurements': {
            **{key: {'column': column, 'unit': unit, 'source': 'municipal', 'available': column in columns,
                     'missing_hours': missing[key]} for key, (column, unit, _) in FIELDS.items()},
            **{key: {**era5_measurements.get(key, {'column': column, 'unit': unit, 'source': 'era5',
                                                  'available': False, 'reason': era5['reason']}),
                     'missing_hours': missing[key]} for key, (column, _, unit) in ERA5_FIELDS.items()},
        },
        'points': points,
    }
