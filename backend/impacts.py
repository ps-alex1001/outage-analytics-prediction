"""Precomputed impact service, scoped/cached geometry, and event-table queries."""
from functools import lru_cache
import gzip
import hashlib
import json
from threading import Lock

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
import pandas as pd

from backend import topology
from backend.datasets import IMPACTS_PATH, OUTAGES_PATH

router = APIRouter(prefix='/api/impacts', tags=['synthetic impacts'])
EVENT_FIELDS = ['outage_id', 'municipality_psgc', 'municipality', 'barangay_psgc', 'barangay',
                'start_time', 'end_time', 'duration_minutes', 'cause_category', 'resolution_status', 'unresolved_reason']
SUMMARY_FIELDS = ['affected_line_segment_count', 'affected_line_length_km', 'affected_pole_count',
                  'affected_transformer_count', 'affected_barangay_count', 'affected_feeder_ids',
                  'large_modeled_impact', 'no_downstream_transformers']
_dataset_lock = Lock()


def records(frame):
    # pandas converts nullable scalars and nested Arrow arrays to JSON-safe types.
    return json.loads(frame.to_json(orient='records'))


@lru_cache(maxsize=1)
def _read_dataset():
    try:
        manifest = json.loads((IMPACTS_PATH / 'run_manifest.json').read_text(encoding='utf-8'))
        expected_paths = {'outages': OUTAGES_PATH, **{name: topology.DATA / name for name in [
            'network_nodes.geojson', 'network_edges.geojson', 'active_tree.json', 'feeders.json',
            'substations.geojson', 'barangays.geojson', 'dataset_info.json']}}
        for name, path in expected_paths.items():
            with path.open('rb') as f:
                actual = hashlib.file_digest(f, 'sha256').hexdigest()
            if actual != manifest['inputs'][name]['sha256']:
                raise HTTPException(503, 'Precomputed impacts are stale for the current outage/topology source. Recompute impacts and restart the backend.')
        frame = pd.read_parquet(IMPACTS_PATH / 'outage_impact_summary.parquet')
        sets = pd.read_parquet(IMPACTS_PATH / 'impact_sets.parquet').set_index('impact_set_id', drop=False)
    except FileNotFoundError as exc:
        raise HTTPException(503, 'Impact dataset is unavailable. Run scripts/map_outage_topology_impacts.py.') from exc
    if not frame.outage_id.is_unique or set(frame.outage_id) != set(pd.read_csv(OUTAGES_PATH, usecols=['outage_id']).outage_id):
        raise HTTPException(503, 'Impact dataset event IDs do not match the current outages')
    indexed = frame.set_index('outage_id', drop=False)
    indexed.index.name = 'event_key'
    return indexed, sets, manifest


def dataset():
    # Initial metadata/list/detail requests share one hash + Parquet load.
    with _dataset_lock:
        return _read_dataset()


@lru_cache(maxsize=1)
def boundary_index():
    return {f['properties']['barangay_psgc']: f for f in topology.read_json('barangays.geojson')['features']}


@router.get('/metadata')
def get_metadata():
    frame, _, manifest = dataset()
    keys = ['municipality_psgc', 'municipality', 'barangay_psgc', 'barangay']
    return {'total_events': len(frame), 'years': sorted(frame.start_time.str[:4].astype(int).unique().tolist()),
            'causes': sorted(frame.cause_category.unique().tolist()),
            'geography': records(frame[keys].drop_duplicates().sort_values(['municipality', 'barangay'])),
            'validation': manifest['validation'], 'seed': manifest['seed'],
            'notice': 'Synthetic downstream loss-of-supply scenario. Connectivity and source locations are unverified; outlines show infrastructure locations, not whole-barangay coverage.'}


@router.get('/events')
def list_events(q: str = '', year: int | None = None, municipality_psgc: str | None = None,
                barangay_psgc: str | None = None, cause_category: str | None = None,
                status: str | None = None, sort: str = 'start_time', direction: str = 'desc',
                page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    frame, _, _ = dataset()
    if sort not in {'start_time', 'duration_minutes', 'municipality', 'barangay', 'cause_category', 'outage_id'} or direction not in {'asc', 'desc'}:
        raise HTTPException(422, 'Unsupported table sort')
    if year is not None:
        frame = frame.loc[frame.start_time.str.startswith(str(year) + '-')]
    for column, value in [('municipality_psgc', municipality_psgc), ('barangay_psgc', barangay_psgc), ('cause_category', cause_category), ('resolution_status', status)]:
        if value:
            values = ['Other', 'Animal'] if column == 'cause_category' and value == 'Other' else [value]
            frame = frame.loc[frame[column].isin(values)]
    if q.strip():
        text = frame[['outage_id', 'municipality', 'barangay', 'cause_category']].astype(str).agg(' '.join, axis=1)
        frame = frame.loc[text.str.contains(q.strip(), case=False, regex=False)]
    total = len(frame)
    frame = frame.sort_values([sort, 'outage_id'] if sort != 'outage_id' else [sort], ascending=direction == 'asc', kind='stable')
    return {'events': records(frame[EVENT_FIELDS].iloc[(page - 1) * page_size:page * page_size]),
            'total': total, 'page': page, 'page_size': page_size, 'pages': max(1, (total + page_size - 1) // page_size)}


@router.get('/events/{outage_id}')
def event_impact(outage_id: str, include_boundaries: bool = True):
    frame, sets, _ = dataset()
    if outage_id not in frame.index:
        raise HTTPException(404, 'Unknown synthetic outage')
    row = records(frame.loc[[outage_id]])[0]
    impact = None
    if row['resolution_status'] == 'resolved_modeled':
        impact = records(sets.loc[[row['impact_set_id']]])[0]
    codes = impact['affected_barangay_psgcs'] if impact else []
    boundaries = boundary_index()
    return {'event': {k: row[k] for k in EVENT_FIELDS},
            'assignment': {k: v for k, v in row.items() if k not in EVENT_FIELDS and k not in SUMMARY_FIELDS},
            'impact': impact,
            'affected_barangays': [{**boundaries[code]['properties'], 'is_origin_barangay': code == row['barangay_psgc']} for code in codes],
            'boundaries': topology.collection([boundaries[c] for c in sorted(set(codes) | {row['barangay_psgc']})] if include_boundaries else [])}


@lru_cache(maxsize=32)
def view_payload(kind, key):
    if kind == 'detail':
        data = event_impact(key, include_boundaries=False)
    else:
        codes = key.split(',') if key else []
        boundaries = boundary_index()
        if len(codes) > 300 or any(code not in boundaries for code in codes):
            raise HTTPException(422, 'Unknown impact boundary codes')
        data = topology.collection([boundaries[code] for code in codes])
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    return raw, gzip.compress(raw, compresslevel=3, mtime=0)


def view_response(request, kind, key):
    raw, packed = view_payload(kind, key)
    gzip_ok = False
    for item in request.headers.get('accept-encoding', '').split(','):
        parts = item.strip().split(';')
        if parts[0] == 'gzip':
            try:
                gzip_ok = all(float(p.strip()[2:]) > 0 for p in parts[1:] if p.strip().startswith('q='))
            except ValueError:
                gzip_ok = False
    body = packed if gzip_ok else raw
    headers = {'Cache-Control': 'public, max-age=300', 'Vary': 'Accept-Encoding',
               'ETag': '"' + hashlib.sha256(body).hexdigest() + '"'}
    if gzip_ok:
        headers['Content-Encoding'] = 'gzip'
    if request.headers.get('if-none-match') == headers['ETag']:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type='application/json', headers=headers)


@router.get('/events/{outage_id}/view')
def get_event_view(request: Request, outage_id: str):
    dataset()
    return view_response(request, 'detail', outage_id)


@router.get('/boundaries')
def get_impact_boundaries(request: Request, codes: str = ''):
    dataset()  # Preserve input validation before serving cached geometry.
    return view_response(request, 'boundaries', ','.join(sorted(set(codes.split(',')) - {''})))


@lru_cache(maxsize=8)
def feeder_payload(feeder_id):
    # The existing topology service builds and caches the provincial index once.
    feeders = {f['feeder_id']: f for f in topology.metadata()['feeders']}
    if feeder_id not in feeders:
        raise HTTPException(404, 'Unknown connected modeled feeder')
    fid = feeders[feeder_id]
    network = topology.network(fid['substation_node_id'])
    edges = [f for f in network['edges']['features'] if f['properties'].get('feeder_id') == feeder_id]
    ids = {e['properties'][k] for e in edges for k in ['from_node_id', 'to_node_id']}
    edge_ids = {e['properties']['edge_id'] for e in edges}
    data = {'station_id': fid['substation_node_id'], 'nodes': topology.collection([n for n in network['nodes']['features'] if n['properties']['node_id'] in ids]),
            'edges': topology.collection(edges), 'tree': {'edges': [r for r in network['tree']['edges'] if r[0] in edge_ids]}}
    raw = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    return raw, gzip.compress(raw, compresslevel=3, mtime=0)


@router.get('/network')
def get_impact_network(request: Request, feeder_id: str):
    dataset()  # Reject stale preprocessing before geometry is served.
    raw, packed = feeder_payload(feeder_id)
    gzip_ok = any(p.strip().split(';')[0] == 'gzip' and 'q=0' not in p.replace(' ', '') for p in request.headers.get('accept-encoding', '').split(','))
    body = packed if gzip_ok else raw
    headers = {'Cache-Control': 'public, max-age=300', 'Vary': 'Accept-Encoding', 'ETag': '"' + hashlib.sha256(body).hexdigest() + '"'}
    if gzip_ok:
        headers['Content-Encoding'] = 'gzip'
    if request.headers.get('if-none-match') == headers['ETag']:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type='application/json', headers=headers)
