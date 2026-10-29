import hashlib,json,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from backend.datasets import dataset_paths
from backend.forecast_features import FEATURES
from backend.forecast_model import ForecastModel,ModelUnavailable,active_model_directory
from backend.portable_classifier import PortableClassifier


class PortableTests(unittest.TestCase):
    def test_numerical_export_loader_parity_and_checksum(self):
        spec={'kind':'tree_average','trees':[{'left':[1,-1,-1],'right':[2,-1,-1],'feature':[0,-2,-2],'threshold':[2.,-2.,-2.],'value':[.5,.2,.8]}]}
        with TemporaryDirectory() as temp:
            path=Path(temp);(path/'model.json').write_text(json.dumps(spec))
            x=np.zeros((3,24),dtype='float32');x[1,0]=3;x[2,0]=np.nan
            (path/'parity.json').write_text(json.dumps({'inputs':[[None if np.isnan(v) else float(v) for v in r] for r in x],'expected_scores':[.2,.8,.2]}))
            manifest={'artifact_version':1,'features':FEATURES,'model_type':'portable_binary_classifier','model_artifact':'model.json',
                      'target':'outage_next_hour','target_interval':'(t,t+1h]','weather_lag_hours':1,'imputer_medians':[0.]*24,
                      **{name+'_sha256':hashlib.sha256((path/file).read_bytes()).hexdigest() for name,file in [('model','model.json'),('parity','parity.json')]}}
            (path/'manifest.json').write_text(json.dumps(manifest))
            model=ForecastModel(path);np.testing.assert_allclose(model.predict(x),[.2,.8,.2]);self.assertEqual(model.parity_rows,3)
            (path/'model.json').write_text('{}')
            with self.assertRaises(ModelUnavailable):ForecastModel(path)

    def test_invalid_cyclic_tree_rejected_before_inference(self):
        spec={'kind':'tree_average','trees':[{'left':[0],'right':[0],'feature':[0],'threshold':[2.],'value':[.5]}]}
        with self.assertRaises(ValueError):PortableClassifier(spec,24)

    def test_bundle_paths_and_model_selection_change_together(self):
        with TemporaryDirectory() as temp:
            data=Path(temp);models=data/'models';models.mkdir()
            bundle={'bundle_version':1,'events':'synthetic/v3/events.csv','impacts':'impacts/v3','ml':'ml/v3','model':'models/v3_frozen'}
            pointer=data/'active_bundle.json';pointer.write_text(json.dumps(bundle))
            self.assertEqual(dataset_paths(data)['events'],data/'synthetic/v3/events.csv')
            self.assertEqual(active_model_directory(models),models/'v3_frozen')
            bundle['model']='../escape';pointer.write_text(json.dumps(bundle))
            with self.assertRaises(ValueError):dataset_paths(data)
            with self.assertRaises(ModelUnavailable):active_model_directory(models)

if __name__=='__main__':unittest.main()
