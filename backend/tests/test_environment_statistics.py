"""Targeted numerical checks against the independently validated Morong sample."""
import json
import unittest
import numpy as np
from backend.datasets import DATA_ROOT
from backend.environment_statistics import calculate, horn

class EnvironmentalStatisticsTests(unittest.TestCase):
    def test_horn_plane_and_missing_neighborhood(self):
        y,x=np.mgrid[:7,:7]
        self.assertTrue(np.allclose(horn(x*30.,30,30),45))
        z=x.astype(float); z[3,3]=np.nan
        self.assertTrue(np.isnan(horn(z,30,30)[1:4,1:4]).all())

    def test_morong_matches_validated_numeric_outputs(self):
        expected=json.loads((DATA_ROOT/'spatial_previews/morong_2025/metadata.json').read_text(encoding='utf-8'))
        actual=calculate('0300808000')['statistics']
        self.assertAlmostEqual(actual['mean_slope_deg'],expected['statistics']['slope_native_30m']['mean'],places=8)
        self.assertAlmostEqual(actual['high_ndvi_steep_pct'],expected['bivariate']['percent_joint_valid_area_rows_slope_columns_ndvi'][2][2],places=8)

    def test_banawang_has_extrema_overlap_and_cached_result(self):
        result=calculate(None,'0300802002')
        self.assertTrue(all(value is not None for value in result['statistics'].values()))
        self.assertLessEqual(result['statistics']['elevation_min_m'],result['statistics']['elevation_max_m'])
        self.assertTrue(0<=result['statistics']['high_ndvi_steep_pct']<=100)
        self.assertIs(result,calculate(None,'0300802002'))

if __name__=='__main__': unittest.main()
