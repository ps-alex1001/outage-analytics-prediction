"""Verify a versioned export and atomically activate only an improved candidate."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.install_forecast_model import install
from backend.forecast_model import ForecastModel


def activate(archive):
    root = ROOT / 'data/models'
    target = root / 'outage_v2_municipality_v1'
    receipt = install(archive, target)
    model = ForecastModel(target)
    if model.manifest['artifact_version'] != 2:
        raise ValueError('Expected the municipality-aware model')
    baseline = json.loads((root/'municipality_experiment_v1/baseline_validation.json').read_text())
    validation = model.manifest['validation_metrics']
    if validation['average_precision'] <= baseline['average_precision'] or validation['recall'] < baseline['recall']:
        raise ValueError('Candidate failed the predefined validation promotion gate')
    comparison = model.manifest['comparison']
    if comparison['candidate']['average_precision'] <= comparison['baseline']['average_precision']:
        raise ValueError('Reused test benchmark regressed; do not silently promote')
    pointer = root/'active_forecast.json'
    previous = json.loads(pointer.read_text()) if pointer.exists() else {'directory':'outage_v2_xgboost'}
    temporary = root/'active_forecast.partial.json'
    temporary.write_text(json.dumps({'directory':target.name,'model_id':receipt['model_id']},indent=2),encoding='utf-8')
    os.replace(temporary,pointer)
    receipt.update({'previous':previous,'validation':validation,'comparison':comparison})
    (root/'municipality_deployment.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('archive',type=Path)
    activate(parser.parse_args().archive)
