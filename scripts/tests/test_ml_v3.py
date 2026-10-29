"""Meaningful interval and causal-history controls; no model is fitted."""
import sys
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from build_ml_dataset_v3 import (TZ, HOUR, RAW, engineer_weather, engineer_cape,
    calendar_features, label_arrays, rainy_streak, local)

class CausalityTests(unittest.TestCase):
    def source(self):
        index=pd.date_range('2023-12-30',periods=100,freq='h',tz=TZ)
        return pd.DataFrame({c:np.arange(100,dtype=float) for c in RAW},index=index)

    def test_hand_sum_and_future_perturbation(self):
        source=self.source();t=source.index[30]
        features=engineer_weather(source)
        self.assertEqual(features.loc[t,'rainfall_24h_sum'],sum(range(6,30)))
        self.assertEqual(features.loc[t,'wind_gust_change_3h'],3)
        changed=source.copy();changed.loc[t:]=99999
        pd.testing.assert_frame_equal(features.loc[:t],engineer_weather(changed).loc[:t])

    def test_gaps_warmup_and_location_isolation(self):
        source=self.source();features=engineer_weather(source)
        self.assertTrue(pd.isna(features.iloc[22].rainfall_24h_sum))
        self.assertEqual(features.iloc[23].rainfall_24h_sum,sum(range(24)))
        missing=source.drop(source.index[15]);gapped=engineer_weather(missing)
        self.assertTrue(pd.isna(gapped.loc[source.index[30],'rainfall_24h_sum']))
        other=source.copy();other['precipitation_mm']=0
        self.assertEqual(engineer_weather(other).iloc[50].rainfall_24h_sum,0)
        self.assertGreater(features.iloc[50].rainfall_24h_sum,0)

    def test_rain_streak_unknown_and_cap(self):
        actual=rainy_streak([1,1,0,1,1,np.nan,1,0,1],.1,3)
        np.testing.assert_allclose(actual,[np.nan,np.nan,0,1,2,np.nan,np.nan,0,1],equal_nan=True)
        np.testing.assert_allclose(rainy_streak([1,1,1,1],.1,3),[np.nan,np.nan,3,3],equal_nan=True)

    def test_cape_lag_and_calendar_wrap(self):
        f=self.source();s=f.precipitation_mm
        features=engineer_cape(s)
        self.assertEqual(features.loc[f.index[8],'cape_3h_max'],7)
        cal=calendar_features(pd.DatetimeIndex([local('2023-12-31 23:00'),local('2024-01-01 00:00')]))
        self.assertLess(np.linalg.norm(cal.iloc[0][['hour_sin','hour_cos']]-cal.iloc[1][['hour_sin','hour_cos']]),.3)

class LabelTests(unittest.TestCase):
    def events(self,starts,ends):
        return pd.DataFrame({'barangay_psgc':['A']*len(starts),
            'start_time':pd.DatetimeIndex([local(s) for s in starts]),
            'end_time':pd.DatetimeIndex([local(e) for e in ends])})

    def test_exact_and_subhour_start_and_restoration(self):
        times=pd.date_range(local('2023-06-01 08:00'),periods=7,freq='h')
        events=self.events(['2023-06-01 10:00','2023-06-01 12:30'],['2023-06-01 11:00','2023-06-01 13:15'])
        c,a,eligible,why=label_arrays(events,times,['A','B'],times[0],times[-1],local('2024-01-01'),local('2025-01-01'))
        self.assertEqual(c[1,0],1) # exact 10:00 -> 09:00 cutoff
        self.assertEqual(c[4,0],1) # 12:30 -> 12:00 cutoff
        self.assertTrue(a[2,0]);self.assertFalse(a[3,0]) # active at start; restored at 11
        self.assertFalse(eligible[-1,0]);self.assertEqual(why[-1,0],'incomplete_label_window')
        self.assertEqual(c[:,1].sum(),0) # no topology/neighbor positives

    def test_boundary_purge_and_multiple_starts(self):
        times=pd.date_range(local('2023-12-31 22:00'),periods=5,freq='h')
        e=self.events(['2024-01-01 00:00','2024-01-01 00:30'],['2024-01-01 00:10','2024-01-01 00:40'])
        c,a,eligible,why=label_arrays(e,times,['A'],times[0],times[-1],local('2024-01-01'),local('2025-01-01'))
        self.assertEqual(c[1,0],1);self.assertEqual(why[1,0],'split_boundary_purge')
        self.assertEqual(c[2,0],1);self.assertFalse(eligible[2,0]) # already active
        e=self.events(['2024-01-01 01:10','2024-01-01 01:40'],['2024-01-01 01:20','2024-01-01 01:50'])
        c,*_=label_arrays(e,times,['A'],times[0],times[-1],local('2024-01-01'),local('2025-01-01'))
        self.assertEqual(c[3,0],2) # two events collapse to one binary positive

    def test_nanosecond_restoration_on_microsecond_clock(self):
        times=pd.date_range(local('2023-06-01 08:00'),periods=5,freq='h').as_unit('us')
        e=self.events(['2023-06-01 09:00'],['2023-06-01 10:00:00.000000001'])
        c,active,eligible,why=label_arrays(e,times,['A'],times[0],times[-1],local('2024-01-01'),local('2025-01-01'))
        self.assertTrue(active[2,0]);self.assertFalse(active[3,0])

if __name__=='__main__':
    unittest.main()
