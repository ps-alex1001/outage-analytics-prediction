import unittest
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
import pandas as pd
from backend.forecast_features import FEATURES
from backend.forecast_model import ForecastModel, ModelUnavailable, active_model_directory, model_values


class MunicipalityTests(unittest.TestCase):
    def test_same_encoding_for_training_and_serving_and_legacy_unchanged(self):
        categories = [f"03008{i:05d}" for i in range(12)]
        f = pd.DataFrame(np.arange(3*24).reshape(3,24), columns=FEATURES)
        f['municipality_psgc'] = [categories[0],categories[11],categories[3]]
        legacy=model_values(SimpleNamespace(),f)
        np.testing.assert_array_equal(legacy, f[FEATURES].to_numpy('float32'))
        encoded=model_values(SimpleNamespace(municipality_categories=categories),f)
        self.assertEqual(encoded.shape,(3,36))
        np.testing.assert_array_equal(encoded[:,:24],legacy)
        np.testing.assert_array_equal(encoded[:,24:].sum(axis=1),[1,1,1])
        self.assertEqual(encoded[0,24],1);self.assertEqual(encoded[1,35],1)
        f.loc[0,'municipality_psgc']='9999999999'
        with self.assertRaises(ModelUnavailable):model_values(SimpleNamespace(municipality_categories=categories),f)

    def test_native_36_feature_export_roundtrip_and_tampering(self):
        # Small software-contract fixture only; never an experiment/deployment model.
        import xgboost as xgb
        categories = [f"03008{i:05d}" for i in range(12)]
        rng = np.random.default_rng(42)
        x = np.column_stack([rng.normal(size=(144,24)), np.tile(np.eye(12), (12,1))]).astype('float32')
        y = (np.argmax(x[:,24:],axis=1) < 4).astype('float32')
        booster = xgb.train({'objective':'binary:logistic','tree_method':'hist','nthread':2,'max_depth':2}, xgb.DMatrix(x,label=y), num_boost_round=3)
        sample = x[:24].copy(); sample[0,0] = np.nan
        expected = booster.inplace_predict(np.nan_to_num(sample))
        with TemporaryDirectory() as tmp:
            directory=Path(tmp)
            booster.save_model(directory/'model.ubj')
            parity={'inputs':[[None if np.isnan(v) else float(v) for v in row] for row in sample], 'expected_scores':expected.tolist()}
            (directory/'parity.json').write_text(json.dumps(parity))
            manifest={'artifact_version':2,'features':FEATURES+['municipality_'+p for p in categories],
                'municipality_categories':categories,'model_type':'xgboost_binary_logistic',
                'weather_lag_hours':1,'target':'outage_next_hour','target_interval':'(t,t+1h]',
                'imputer_medians':[0.]*36,
                **{name+'_sha256':hashlib.sha256((directory/file).read_bytes()).hexdigest() for name,file in [('model','model.ubj'),('parity','parity.json')]}}
            (directory/'manifest.json').write_text(json.dumps(manifest))
            loaded=ForecastModel(directory)
            self.assertEqual(loaded.parity_rows,24)
            np.testing.assert_allclose(loaded.predict(sample),expected)
            with (directory/'model.ubj').open('ab') as f:f.write(b'tampered')
            with self.assertRaises(ModelUnavailable):ForecastModel(directory)

    def test_active_pointer_selection_and_rollback_with_explicit_override(self):
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {'FORECAST_MODEL_DIR':''}):
            root=Path(tmp)
            self.assertEqual(active_model_directory(root),root/'outage_v2_xgboost')
            pointer=root/'active_forecast.json'
            for name in ['outage_v2_municipality_v1','outage_v2_xgboost']:
                pointer.write_text(json.dumps({'directory':name}))
                self.assertEqual(active_model_directory(root),root/name)
            pointer.write_text(json.dumps({'directory':'../escape'}))
            with self.assertRaises(ModelUnavailable):active_model_directory(root)
            with patch.dict(os.environ, {'FORECAST_MODEL_DIR':str(root/'explicit')}):
                self.assertEqual(active_model_directory(root),root/'explicit')


if __name__=='__main__':unittest.main()
