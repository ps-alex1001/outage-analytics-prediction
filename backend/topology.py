"""Read-only, lazily indexed access to the imported topology export.

No source files are changed. Connectivity comes exclusively from active_tree.json.
Restart the backend after replacing an export to clear the process-local caches.
"""
from collections import defaultdict
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response


DATA = Path(__file__).resolve().parents[1] / "data/topology/react_data"
router = APIRouter(prefix="/api/topology", tags=["topology"])
_index_lock = Lock()
_index = None


def read_json(name):
    try:
        with (DATA / name).open(encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError as exc:
        raise HTTPException(503, f"Topology asset missing: {name}") from exc


def collection(features):
    return {"type": "FeatureCollection", "features": features}


@lru_cache(maxsize=1)
def metadata():
    # Deliberately independent of the large provincial network index.
    return {
        "substations": read_json("substations.geojson"),
        "feeders": read_json("feeders.json"),
        "style": read_json("style.json"),
        "dataset": read_json("dataset_info.json"),

    }


def network_index():
    global _index
    with _index_lock:
        if _index is not None:
            return _index
        nodes_by_station, edges_by_station, trees_by_station = (
            defaultdict(list), defaultdict(list), defaultdict(list)
        )
        nodes = read_json("network_nodes.geojson")["features"]
        node_station = {}
        for feature in nodes:
            props = feature["properties"]
            identity = props["node_id"]
            if not isinstance(identity, str):
                raise ValueError("Topology node IDs must be strings")
            station = props.get("source_node_id")
            if props["node_type"] == "substation":
                station = identity
            node_station[identity] = station
            nodes_by_station[station].append(feature)
        tree = {row[0]: row for row in read_json("active_tree.json")["edges"]}
        for feature in read_json("network_edges.geojson")["features"]:
            props = feature["properties"]
            identity = props["edge_id"]
            row = tree.get(identity)
            if row is None:
                continue  # Candidate intersections never create connectivity.
            if row != [identity, props["from_node_id"], props["to_node_id"]]:
                raise ValueError(f"Active tree endpoint mismatch: {identity}")
            station = props.get("source_node_id")
            if any(node_station.get(endpoint) != station for endpoint in row[1:]):
                raise ValueError(f"Active tree crosses station scopes: {identity}")
            edges_by_station[station].append(feature)  # Includes logical Point edges.
            trees_by_station[station].append(row)
        if sum(map(len, trees_by_station.values())) != len(tree):
            raise ValueError("An active tree edge has no corresponding geometry record")
        _index = nodes_by_station, edges_by_station, trees_by_station
        return _index


def network(station_id):
    allowed = {
        f["properties"]["modeled_source_node_id"]
        for f in metadata()["substations"]["features"]
        if f["properties"]["active_feeder_count"] > 0
    }
    if station_id not in allowed:
        raise HTTPException(404, "No active modeled network for this substation")
    nodes, edges, trees = network_index()
    return {
        "station_id": station_id,
        "nodes": collection(nodes[station_id]),
        "edges": collection(edges[station_id]),
        "tree": {"edges": trees[station_id]},
    }


def infrastructure_context():
    """Complete display context; these records never add active graph links."""
    active = {row[0] for row in read_json("active_tree.json")["edges"]}
    features, counts = [], {}
    fields = {"asset_id", "node_id", "edge_id", "node_type", "edge_type", "source_node_id",
              "feeder_id", "is_synthetic", "connection_status", "data_status", "name",
              "municipality", "barangay", "geometry_basis", "is_verified_connectivity",
              "from_node_id", "to_node_id", "length_m"}
    for kind, filename in [("node", "network_nodes.geojson"), ("edge", "network_edges.geojson")]:
        records = read_json(filename)["features"]
        counts[f"topology_{kind}s"] = len(records)
        for feature in records:
            p = feature["properties"]
            identity = p[f"{kind}_id"]
            props = {k: v for k, v in p.items() if k in fields}
            props.update(context_id=f"{kind}:{identity}", asset_type=p[f"{kind}_type"],
                         provenance="generated_topology" if p.get("is_synthetic", True) else "observed_anchor",
                         is_synthetic=p.get("is_synthetic", True),
                         is_active_tree=identity in active if kind == "edge" else None)
            features.append({"type": "Feature", "geometry": feature["geometry"], "properties": props})
    observed_path = DATA.parents[1] / "infrastructure/observed/bataan_electrical_infrastructure_cleaned.geojson"
    if not observed_path.is_file():
        raise HTTPException(503, "Observed infrastructure dataset missing")
    with observed_path.open(encoding="utf-8") as file:
        observed = json.load(file)["features"]
    counts["observed_assets"] = len(observed)
    observed_fields = {"asset_id", "asset_type", "name", "operator", "voltage", "voltage_levels_v",
                       "municipality", "barangay", "source_id", "source_type", "network_tier_inferred"}
    for i, feature in enumerate(observed):
        p = feature["properties"]
        props = {k: v for k, v in p.items() if k in observed_fields}
        props.update(context_id=f"observed:{p['asset_id']}:{i}", provenance="observed_osm",
                     is_synthetic=False, is_verified_connectivity=False,
                     data_status="observed_osm_geometry; electrical_connection_not_verified")
        features.append({"type": "Feature", "geometry": feature["geometry"], "properties": props})
    return {"infrastructure": collection(features), "counts": counts}


@lru_cache(maxsize=8)
def encoded_payload(kind, key=""):
    if kind == "metadata":
        payload = metadata()
    elif kind == "network":
        payload = network(key)
    elif kind == "context":
        payload = infrastructure_context()
    else:
        names = {
            "municipality": ("municipalities.geojson", "municipality_labels.geojson"),
            "barangay": ("barangays.geojson", "barangay_labels.geojson"),
        }
        if key not in names:
            raise HTTPException(404, "Unknown boundary layer")
        polygons, labels = names[key]
        payload = {"boundaries": read_json(polygons), "labels": read_json(labels)}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False,
                     allow_nan=False).encode("utf-8")
    packed = gzip.compress(raw, compresslevel=3, mtime=0)
    return raw, packed


def payload_response(request, kind, key=""):
    raw, packed = encoded_payload(kind, key)
    encodings = {}
    for item in request.headers.get("accept-encoding", "").split(","):
        name, _, params = item.strip().partition(";")
        encodings[name.lower()] = params.replace(" ", "") != "q=0"
    compressed = encodings.get("gzip", encodings.get("*", False))
    body = packed if compressed else raw
    etag = '"' + hashlib.sha256(body).hexdigest() + '"'
    headers = {"Cache-Control": "public, max-age=300", "ETag": etag,
               "Vary": "Accept-Encoding"}
    if compressed:
        headers["Content-Encoding"] = "gzip"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


@router.get("/metadata")
def get_metadata(request: Request):
    return payload_response(request, "metadata")


@router.get("/network")
def get_network(request: Request, station_id: str):
    return payload_response(request, "network", station_id)


@router.get("/context")
def get_context(request: Request):
    return payload_response(request, "context")


@router.get("/boundaries/{level}")
def get_boundaries(request: Request, level: str):
    return payload_response(request, "boundaries", level)
