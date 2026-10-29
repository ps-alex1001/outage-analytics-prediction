"""Versioned dataset bundle; paths resolve together at backend startup."""
import json
from pathlib import Path

DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'

def dataset_paths(data_root=DATA_ROOT):
    root = Path(data_root).resolve()
    pointer = root / 'active_bundle.json'
    if not pointer.is_file():
        return {'events': root/'synthetic/v3_20261029T115229_718511Z/synthetic_outage_events_v3.csv',
                'impacts': root/'impacts/v3_topology_1_0_0', 'ml': root/'ml/v2'}
    bundle = json.loads(pointer.read_text(encoding='utf-8'))
    if bundle.get('bundle_version') != 1:
        raise ValueError('Unsupported active dataset bundle')
    paths = {}
    for key in ['events', 'impacts', 'ml', 'model']:
        value = bundle[key]
        if not isinstance(value, str) or Path(value).is_absolute():
            raise ValueError('Bundle paths must be relative to data')
        path = (root/value).resolve()
        if not path.is_relative_to(root) or '..' in Path(value).parts:
            raise ValueError('Bundle path leaves the data directory')
        paths[key] = path
    return paths


_paths = dataset_paths()
OUTAGES_PATH = _paths['events']
IMPACTS_PATH = _paths['impacts']
