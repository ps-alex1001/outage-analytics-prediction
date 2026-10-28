import csv
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
import pandas as pd
import numpy as np
import xarray as xr

from backend import weather_context as weather


class WeatherContextTests(unittest.TestCase):
    def setUp(self):
        self.path = Mock()
        self.path.name = 'weather.csv'
        self.revision = 0
        self.event = {'outage_id': 'event', 'municipality': 'City of Balanga',
                      'start_time': '2026-06-30T22:15:00+08:00',
                      'end_time': '2026-07-01T00:45:30+08:00'}
        self.rows = []
        for t in pd.date_range('2026-06-30T16:00:00', '2026-07-01T04:00:00', freq='h'):
            self.rows.append({'municipality': 'Balanga', 'latitude': 14.657293, 'longitude': 120.52974,
                              'utc_offset_seconds': 28800, 'timezone': 'Asia/Singapore',
                              'time': str(t), 'precipitation_mm': 0, 'wind_speed_10m_m_s': 2,
                              'wind_gusts_10m_m_s': 5, 'temperature_2m_c': 28,
                              'surface_pressure_hpa': 1012.6,
                              'relative_humidity_2m_pct': 75, 'soil_moisture_0_to_7cm_m3_m3': 0.42,
                              'soil_moisture_7_to_28cm_m3_m3': 0.43})
        self.write_rows()
        times = pd.date_range('2026-06-30T16:00:00+08:00', '2026-07-01T04:00:00+08:00', freq='h').tz_convert('UTC').tz_localize(None)
        self.era5 = xr.Dataset(coords={'valid_time': times, 'latitude': [14.75, 14.5], 'longitude': [120.5, 120.75]})
        for column, unit, base in [('cape', 'J kg**-1', 900), ('tcwv', 'kg m**-2', 60),
                                   ('totalx', 'K', 42), ('cbh', 'm', 350)]:
            values = base + np.arange(len(times))[:, None, None] + np.array([[0, 100], [200, 300]])[None, :, :]
            self.era5[column] = xr.DataArray(values.astype(float), dims=('valid_time', 'latitude', 'longitude'),
                                            attrs={'units': unit, 'GRIB_stepType': 'instant'})
        self.addCleanup(weather._build_index.cache_clear)
        for patcher in [patch.object(weather, 'WEATHER_PATH', self.path),
                        patch.object(weather.xr, 'open_dataset', side_effect=lambda *a, **kw: self.era5.copy()),
                        patch.object(weather, 'dataset', side_effect=lambda: (
                            pd.DataFrame([self.event]).set_index('outage_id', drop=False), None, None))]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_rows(self):
        source = io.StringIO(newline='')
        writer = csv.DictWriter(source, fieldnames=list(self.rows[0]))
        writer.writeheader()
        writer.writerows(self.rows)
        content = source.getvalue().encode('utf-8')
        self.revision += 1
        self.path.stat.return_value = SimpleNamespace(st_mtime_ns=self.revision, st_size=len(content))
        self.path.open.side_effect = lambda *args, **kwargs: io.BytesIO(content)

    def test_exact_cross_midnight_window_and_hourly_labels(self):
        result = weather.event_weather('event')
        self.assertEqual(result['window'], {'start': '2026-06-30T16:15:00+08:00',
                                            'end': '2026-07-01T03:45:30+08:00'})
        self.assertEqual(result['outage']['end'], '2026-07-01T00:45:30+08:00')
        self.assertEqual(result['points'][0]['time'], '2026-06-30T17:00:00+08:00')
        self.assertEqual(result['points'][-1]['time'], '2026-07-01T03:00:00+08:00')
        self.assertEqual(len(result['points']), 11)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['source']['municipality'], 'Balanga')
        self.assertEqual(result['source']['latitude'], 14.657293)

    def test_units_conversion_and_soil_depth_identity(self):
        result = weather.event_weather('event')
        point = result['points'][0]
        self.assertEqual(point['wind_speed_kmh'], 7.2)
        self.assertEqual(point['wind_gusts_kmh'], 18)
        self.assertEqual(point['soil_moisture_0_7'], 0.42)
        self.assertEqual(point['soil_moisture_7_28'], 0.43)
        self.assertEqual(result['measurements']['soil_moisture_7_28']['unit'], 'm³/m³')
        self.assertEqual(point['precipitation_mm'], 0)
        self.assertEqual(point['surface_pressure_hpa'], 1012.6)

    def test_era5_utc_alignment_grid_match_and_metadata_units(self):
        result = weather.event_weather('event')
        self.assertEqual(result['era5']['latitude'], 14.75)
        self.assertEqual(result['era5']['longitude'], 120.5)
        self.assertEqual(result['era5']['grid_degrees'], 0.25)
        self.assertEqual(result['points'][0]['cape_j_kg'], 901)
        self.assertEqual(result['points'][0]['column_water_vapour_kg_m2'], 61)
        # K-valued stability index must not receive a Celsius offset.
        self.assertEqual(result['points'][0]['total_totals_index_k'], 43)
        self.assertEqual(result['points'][0]['cloud_base_height_m'], 351)
        self.assertEqual(result['measurements']['surface_pressure_hpa']['source'], 'municipal')
        self.assertEqual(result['measurements']['cape_j_kg']['source'], 'era5')
        self.assertIn('Total cloud cover', result['era5']['unavailable_variables'])

    def test_era5_missing_value_and_incompatible_units_are_not_substituted(self):
        self.era5['cbh'].values[2, 0, 0] = np.nan
        self.era5['tcwv'].attrs['units'] = 'unsupported'
        result = weather.event_weather('event')
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['points'][1]['cloud_base_height_m'])
        self.assertEqual(result['measurements']['cloud_base_height_m']['missing_hours'], 1)
        self.assertFalse(result['measurements']['column_water_vapour_kg_m2']['available'])
        self.assertTrue(all(p['column_water_vapour_kg_m2'] is None for p in result['points']))
        self.assertEqual(result['points'][0]['temperature_c'], 28)

    def test_era5_failure_preserves_municipal_weather(self):
        with patch.object(weather.xr, 'open_dataset', side_effect=OSError('offline')):
            result = weather.event_weather('event')
        self.assertEqual(result['era5']['status'], 'unavailable')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['points'][0]['surface_pressure_hpa'], 1012.6)
        self.assertTrue(all(p['cape_j_kg'] is None for p in result['points']))

    def test_only_selected_era5_variables_cell_and_window_are_materialized(self):
        loaded = []
        original_load = xr.Dataset.load

        def record_load(selected, *args, **kwargs):
            loaded.append((dict(selected.sizes), set(selected.data_vars), float(selected.latitude), float(selected.longitude)))
            return original_load(selected, *args, **kwargs)

        with patch.object(xr.Dataset, 'load', record_load):
            weather.event_weather('event')
        self.assertEqual(loaded, [({'valid_time': 11}, {'cape', 'tcwv', 'totalx', 'cbh'}, 14.75, 120.5)])

    def test_missing_era5_hour_remains_a_gap(self):
        self.era5 = self.era5.drop_sel(valid_time=self.era5.valid_time.values[3])
        result = weather.event_weather('event')
        self.assertIsNone(result['points'][2]['cape_j_kg'])
        self.assertEqual(result['points'][2]['temperature_c'], 28)
        self.assertEqual(result['measurements']['cape_j_kg']['missing_hours'], 1)

    def test_missing_hour_and_measurement_remain_null(self):
        self.rows.pop(2)  # 18:00, inside the window.
        self.rows[2]['temperature_2m_c'] = 'NaN'
        for row in self.rows:
            del row['soil_moisture_7_to_28cm_m3_m3']
            row['soil_moisture_28_to_100cm_m3_m3'] = 0.9
        self.write_rows()
        result = weather.event_weather('event')
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['points'][1]['precipitation_mm'])
        self.assertIsNone(result['points'][2]['temperature_c'])
        self.assertTrue(all(p['soil_moisture_7_28'] is None for p in result['points']))
        self.assertFalse(result['measurements']['soil_moisture_7_28']['available'])

    def test_no_geographic_or_temporal_fallback(self):
        self.event['municipality'] = 'Unknown'
        result = weather.event_weather('event')
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(result['source']['match'], 'unmatched')
        self.event['municipality'] = 'City of Balanga'
        self.event['start_time'] = '2027-01-01T00:00:00+08:00'
        self.event['end_time'] = '2027-01-01T01:00:00+08:00'
        result = weather.event_weather('event')
        self.assertEqual(result['status'], 'missing')
        self.assertTrue(all(p['temperature_c'] is None for p in result['points']))

    def test_timezone_conversion_uses_recorded_utc_plus_eight(self):
        self.event['start_time'] = '2026-06-30T14:15:00Z'
        self.event['end_time'] = '2026-06-30T16:45:30Z'
        result = weather.event_weather('event')
        self.assertEqual(result['outage']['start'], '2026-06-30T22:15:00+08:00')
        self.assertEqual(result['points'][0]['time'], '2026-06-30T17:00:00+08:00')

    def test_missing_event_and_source_errors(self):
        with self.assertRaises(HTTPException) as error:
            weather.event_weather('unknown')
        self.assertEqual(error.exception.status_code, 404)
        self.rows[0]['utc_offset_seconds'] = 0
        self.write_rows()
        with self.assertRaises(HTTPException) as error:
            weather.event_weather('event')
        self.assertEqual(error.exception.status_code, 503)


if __name__ == '__main__':
    unittest.main()
