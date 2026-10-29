"""Install the evaluated V3 winner and activate its matching dataset bundle."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.forecast_model import ForecastModel
from scripts.install_forecast_model import install


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def publish_bundle(bundle, receipt):
    data=ROOT/'data';pointer=data/'active_bundle.json'
    paths={key:(data/bundle[key]).resolve() for key in ['events','impacts','ml','model']}
    if any(not path.is_relative_to(data) for path in paths.values()):raise ValueError('Bundle path outside data')
    model=ForecastModel(paths['model'])
    metadata=read(paths['ml']/'ml_dataset_metadata.json')
    impacts=read(paths['impacts']/'run_manifest.json')
    event_hash=digest(paths['events'])
    if event_hash!=metadata['sources']['events']['sha256'] or event_hash!=impacts['inputs']['outages']['sha256']:
        raise ValueError('Events, topology impacts and ML labels are from different runs')
    if model.manifest.get('dataset_version')=='3.0.0':
        if digest(paths['ml']/'ml_dataset_metadata.json')!=model.manifest['dataset_id'] or event_hash!=model.manifest['event_source_sha256']:
            raise ValueError('Model does not match the active V3 labels')
    receipt.update({'bundle':bundle,'model_id':model.manifest['model_id'],'parity_rows_verified':model.parity_rows,
                    'previous_bundle':read(pointer) if pointer.is_file() else None,'activated_at':datetime.now(timezone.utc).isoformat()})
    log=data/'models/deployments';log.mkdir(exist_ok=True)
    name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    (log/(name+'.json')).write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    temp=data/'active_bundle.partial.json';temp.write_text(json.dumps(bundle,indent=2),encoding='utf-8');os.replace(temp,pointer)
    print(json.dumps(receipt,indent=2));print('Restart the backend so analytics and model resolve the same bundle.')
    return receipt


def activate(archive):
    with zipfile.ZipFile(archive) as z:m=json.loads(z.read('manifest.json'))
    if m.get('dataset_version')!='3.0.0':raise ValueError('Expected V3 candidate')
    if not re.fullmatch('[0-9a-f]{64}',m.get('model_id','')):
        raise ValueError('Invalid model identity')
    ml=ROOT/'data/ml/v3';metadata=read(ml/'ml_dataset_metadata.json');validation=read(ml/'audit/validation.json')
    if validation['status']!='passed':raise ValueError('ML dataset validation is incomplete')
    if not read(ROOT/'data/impacts/v3_topology_1_0_0/independent_validation.json')['passed']:
        raise ValueError('Topology validation is incomplete')
    if m['dataset_id']!=digest(ml/'ml_dataset_metadata.json'):raise ValueError('Different V3 dataset')
    population=validation['split_counts']
    if m['training_rows']!=population['train']['positive']+population['train']['negative'] or m['training_positives']!=population['train']['positive']:
        raise ValueError('Serving model was not fitted on every eligible training row')
    for key,metric in [('validation','validation_metrics'),('test','held_out_metrics')]:
        if m[metric]['rows']!=population[key]['positive']+population[key]['negative'] or m[metric]['positives']!=population[key]['positive']:
            raise ValueError('Incomplete chronological evaluation')
    confirmed=m['model_provenance']['confirmation']
    winner=max(confirmed,key=lambda row:row['average_precision'])
    if winner['model']!=m['algorithm']:raise ValueError('Export is not the confirmed winner')
    if len(m['model_provenance']['benchmark'])!=7:raise ValueError('Seven-model benchmark is incomplete')
    slug=re.sub('[^a-z0-9]+','_',m['algorithm'].lower()).strip('_')
    relative='models/outage_v3_'+slug+'_'+m['model_id'][:12]
    receipt=install(archive,ROOT/'data'/relative)
    if receipt['parity_rows_verified'] != 153:
        raise ValueError('Expected all 153 municipality/missing-input parity cases')
    bundle={'bundle_version':1,'dataset_version':'3.0.0','events':'synthetic/v3_20261029T115229_718511Z/synthetic_outage_events_v3.csv',
            'impacts':'impacts/v3_topology_1_0_0','ml':'ml/v3','model':relative,'model_id':m['model_id']}
    return publish_bundle(bundle,receipt)


def rollback_v2():
    return publish_bundle({'bundle_version':1,'dataset_version':'2.0.0',
        'events':'synthetic/v2_20261024T181237_896469Z/synthetic_outage_events_v2.csv',
        'impacts':'impacts/v2_topology_1_2_0','ml':'ml/v2','model':'models/outage_v2_xgboost'}, {'action':'restore_preserved_v2_bundle'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--archive',type=Path);group.add_argument('--rollback-v2',action='store_true')
    args=parser.parse_args()
    rollback_v2() if args.rollback_v2 else activate(args.archive)
