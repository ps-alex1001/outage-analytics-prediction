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


@lru_cache(maxsize=8)
def encoded_payload(kind, key=""):
    if kind == "metadata":
        payload = metadata()
    elif kind == "network":
        payload = network(key)
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


@router.get("/boundaries/{level}")
def get_boundaries(request: Request, level: str):
    return payload_response(request, "boundaries", level)
