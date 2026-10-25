"""Integration checks against the preserved, locally available topology export."""
import gzip
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from backend import topology


@unittest.skipUnless(topology.DATA.is_dir(), "Local topology export is not installed")
class TopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta = topology.metadata()
        cls.stations = cls.meta["substations"]["features"]

    def test_metadata_never_loads_network(self):
        with patch.object(topology, "network_index", side_effect=AssertionError("large index accessed")):
            raw, _ = topology.encoded_payload("metadata")
        data = json.loads(raw)
        self.assertEqual(len(data["substations"]["features"]), 18)
        self.assertTrue(all(s["properties"]["active_feeder_count"] > 0 for s in self.stations))
        self.assertEqual(sum(s["properties"]["active_feeder_count"] > 0 for s in self.stations), 18)
        self.assertEqual(len(data["feeders"]), 32)
        self.assertLess(len(raw), 50_000)

    def test_all_infrastructure_context_keeps_geometry_without_adding_connectivity(self):
        raw, packed = topology.encoded_payload("context")
        self.assertEqual(gzip.decompress(packed), raw)
        data = json.loads(raw)
        features = data["infrastructure"]["features"]
        counts = data["counts"]
        self.assertEqual(counts, {"topology_nodes": 87958, "topology_edges": 92847, "observed_assets": 3761})
        self.assertEqual(len(features), sum(counts.values()))
        self.assertEqual(len({f["properties"]["context_id"] for f in features}), len(features))
        observed = [f for f in features if f["properties"]["provenance"] == "observed_osm"]
        self.assertEqual(len(observed), 3761)
        self.assertTrue(all(not f["properties"]["is_synthetic"] for f in observed))
        with (topology.DATA.parents[1] / "infrastructure/observed/bataan_electrical_infrastructure_cleaned.geojson").open(encoding="utf-8") as file:
            original = json.load(file)["features"]
        self.assertTrue(all(a["geometry"] == b["geometry"] and a["properties"]["asset_id"] == b["properties"]["asset_id"] for a, b in zip(observed, original)))
        inactive = [f for f in features if f["properties"].get("is_active_tree") is False]
        self.assertEqual(len(inactive), 92847 - 87910)
        unresolved = [f for f in features if f["properties"].get("node_type") == "transformer" and not f["properties"].get("source_node_id")]
        self.assertEqual(len(unresolved), 81)
        self.assertNotIn("tree", data)

    def test_unknown_or_unassigned_station_is_not_connected(self):
        for station_id in ["missing", "../../network_nodes.geojson", ""]:
            with self.assertRaises(HTTPException) as caught:
                topology.network(station_id)
            self.assertEqual(caught.exception.status_code, 404)
        with self.assertRaises(HTTPException):
            topology.network("supply:observed-osm:way/627757674")

    def test_all_scopes_and_feeder_counts_match_export(self):
        logical_points = 0
        for station in self.stations:
            p = station["properties"]
            if not p["active_feeder_count"]:
                continue
            sid = p["modeled_source_node_id"]
            data = topology.network(sid)
            nodes = {f["properties"]["node_id"]: f for f in data["nodes"]["features"]}
            edges = {f["properties"]["edge_id"]: f for f in data["edges"]["features"]}
            children = {}
            for edge_id, source, target in data["tree"]["edges"]:
                self.assertIsInstance(edge_id, str)
                self.assertIn(source, nodes)
                self.assertIn(target, nodes)
                self.assertIn(edge_id, edges)
                children.setdefault(source, []).append(target)
            self.assertEqual(len(edges), len(data["tree"]["edges"]))
            self.assertTrue(all(f["properties"]["source_node_id"] == sid for f in edges.values()))
            logical_points += sum(f["geometry"]["type"] == "Point" for f in edges.values())
            for feeder in self.meta["feeders"]:
                if feeder["substation_node_id"] != sid:
                    continue
                seen, stack, count = set(), [feeder["root_node_id"]], 0
                while stack:
                    node = stack.pop()
                    self.assertNotIn(node, seen, "Active tree must be acyclic")
                    seen.add(node)
                    count += nodes[node]["properties"]["node_type"] == "transformer"
                    stack.extend(children.get(node, []))
                self.assertEqual(count, feeder["transformer_count"])
        self.assertGreater(logical_points, 0, "Logical Point edges must survive filtering")

    def test_balanga_numbers_and_unresolved_components(self):
        feeders = [f for f in self.meta["feeders"] if f["substation_asset_id"] == "observed-osm:way/790632397"]
        self.assertEqual(sorted((f["modeled_feeder_number"], f["transformer_count"]) for f in feeders), [(2, 467), (3, 574)])
        nodes, _, _ = topology.network_index()
        self.assertEqual(sum(f["properties"]["node_type"] == "transformer" for f in nodes[None]), 81)

    def test_removed_stations_have_no_remaining_endpoint_references(self):
        nodes = topology.read_json("network_nodes.geojson")["features"]
        edges = topology.read_json("network_edges.geojson")["features"]
        node_ids = {f["properties"]["node_id"] for f in nodes}
        removed = {"supply:observed-osm:way/627757674", "supply:observed-osm:way/98318338"}
        self.assertTrue(node_ids.isdisjoint(removed))
        self.assertEqual(sum(f["properties"]["node_type"] == "substation" for f in nodes), 18)
        for feature in edges:
            self.assertIn(feature["properties"]["from_node_id"], node_ids)
            self.assertIn(feature["properties"]["to_node_id"], node_ids)

    def test_cache_compression_and_conditional_response(self):
        request = Request({"type": "http", "headers": [(b"accept-encoding", b"gzip")]})
        response = topology.get_metadata(request)
        self.assertEqual(response.headers["content-encoding"], "gzip")
        self.assertEqual(len(json.loads(gzip.decompress(response.body))["substations"]["features"]), 18)
        request = Request({"type": "http", "headers": [(b"accept-encoding", b"gzip"),
                          (b"if-none-match", response.headers["etag"].encode())]})
        self.assertEqual(topology.get_metadata(request).status_code, 304)
        request = Request({"type": "http", "headers": [(b"accept-encoding", b"gzip;q=0")]})
        self.assertNotIn("content-encoding", topology.get_metadata(request).headers)

    def test_boundary_allowlist(self):
        for level, expected in [("municipality", 12), ("barangay", 237)]:
            raw, _ = topology.encoded_payload("boundaries", level)
            data = json.loads(raw)
            self.assertEqual(len(data["boundaries"]["features"]), expected)
            self.assertEqual(len(data["labels"]["features"]), expected)
        with self.assertRaises(HTTPException):
            topology.encoded_payload("boundaries", "../network_nodes")


if __name__ == "__main__":
    unittest.main()
