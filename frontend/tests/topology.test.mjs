import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { buildTopology, traceSelection, traceAssetInspection } from "../src/lib/topology.ts";

const root = new URL("../../data/topology/react_data/", import.meta.url);
const read = name => JSON.parse(readFileSync(new URL(name, root), "utf8"));

test("typed helper reproduces all exported feeder counts and logical edges", {
  skip: !existsSync(root),
}, async () => {
  const network = { nodes: read("network_nodes.geojson"), edges: read("network_edges.geojson"), tree: read("active_tree.json") };
  const graph = buildTopology(network);
  const reference = await import("../../topology_explorer_react_transfer_20261024T054637_183097Z/react_helpers/topology.mjs");
  const original = reference.buildTopology(network.nodes, network.tree);
  for (const feeder of read("feeders.json")) {
    const selection = { kind: "node", id: feeder.root_node_id };
    const trace = traceSelection(graph, selection);
    const expected = reference.traceSelection(original, selection.kind, selection.id);
    assert.equal(trace.transformers, feeder.transformer_count);
    assert.deepEqual(trace.nodeIds, expected.nodeIds);
    assert.deepEqual(trace.edgeIds, expected.edgeIds);
    assert.deepEqual(trace.upstreamEdgeIds, expected.upstreamEdgeIds);
  }
  const point = network.edges.features.find(f => f.geometry.type === "Point" && original.edges.has(f.properties.edge_id));
  assert.ok(point);
  const trace = traceSelection(graph, { kind: "edge", id: point.properties.edge_id });
  assert.ok(trace.edgeIds.has(point.properties.edge_id));
  const transformer = network.nodes.features.find(f => f.properties.node_type === "transformer");
  assert.equal(traceSelection(graph, { kind: "node", id: transformer.properties.node_id }).transformers, 1);
  const inspection = traceAssetInspection(graph, { kind: "node", id: transformer.properties.node_id });
  assert.equal(inspection.connectionPoleId, graph.parents.get(transformer.properties.node_id).nodeId);
  const parentTrace = reference.traceSelection(original, "node", inspection.connectionPoleId);
  assert.deepEqual(inspection.nodeIds, parentTrace.nodeIds);
  assert.deepEqual(inspection.edgeIds, parentTrace.edgeIds);
  assert.deepEqual(inspection.upstreamEdgeIds, parentTrace.upstreamEdgeIds);
  const candidate = network.edges.features.find(f => !original.edges.has(f.properties.edge_id));
  assert.throws(() => traceSelection(graph, { kind: "edge", id: candidate.properties.edge_id }), /Unknown topology/);
});

test("missing endpoints fail without invented connections", () => {
  assert.throws(() => buildTopology({ nodes: { features: [] }, edges: { features: [] },
    tree: { edges: [["edge-01", "node-01", "node-02"]] } }), /Incomplete topology/);
});

function branchFixture({ withParent = true, withTransformerChild = false } = {}) {
  const feature = (id, type) => ({ type: "Feature", geometry: { type: "Point", coordinates: [120.5, 14.6] },
    properties: { node_id: id, node_type: type } });
  const rows = [
    ["supply-line", "station", "pole-01"],
    ...(withParent ? [["tx-point-attachment", "pole-01", "transformer-01"]] : []),
    ["downstream-line", "pole-01", "pole-02"],
    ["other-tx-attachment", "pole-02", "transformer-02"],
    ...(withTransformerChild ? [["transformer-child", "transformer-01", "transformer-03"]] : []),
  ];
  return buildTopology({
    nodes: { type: "FeatureCollection", features: [feature("station", "substation"), feature("pole-01", "pole"),
      feature("pole-02", "pole"), ...["transformer-01", "transformer-02", "transformer-03"].map(id => feature(id, "transformer"))] },
    edges: { type: "FeatureCollection", features: rows.map(([id, from, to]) => ({ type: "Feature",
      geometry: id === "downstream-line" || id === "supply-line"
        ? { type: "LineString", coordinates: [[120.5, 14.6], [120.51, 14.61]] }
        : { type: "Point", coordinates: [120.5, 14.6] },
      properties: { edge_id: id, from_node_id: from, to_node_id: to } })) },
    tree: { edges: rows },
  });
}

test("terminal transformer inspection highlights its recorded pole branch without changing connectivity", () => {
  const graph = branchFixture();
  const selected = { kind: "node", id: "transformer-01" };
  const raw = traceSelection(graph, selected);
  assert.equal(raw.transformers, 1);
  assert.equal(raw.edgeIds.size, 0);
  const inspection = traceAssetInspection(graph, selected);
  assert.equal(inspection.connectionPoleId, "pole-01");
  assert.equal(inspection.transformers, 2);
  assert.ok(inspection.edgeIds.has("downstream-line"));
  assert.ok(inspection.edgeIds.has("tx-point-attachment"));
  assert.deepEqual(inspection.upstreamEdgeIds, new Set(["supply-line"]));
  assert.deepEqual(selected, { kind: "node", id: "transformer-01" });
  assert.deepEqual(traceSelection(graph, selected), raw);
  assert.deepEqual(traceAssetInspection(graph, { kind: "edge", id: "downstream-line" }),
    traceSelection(graph, { kind: "edge", id: "downstream-line" }));
});

test("transformer inspection never invents a parent or overrides real transformer descendants", () => {
  const selection = { kind: "node", id: "transformer-01" };
  for (const graph of [branchFixture({ withParent: false }), branchFixture({ withTransformerChild: true })]) {
    assert.deepEqual(traceAssetInspection(graph, selection), traceSelection(graph, selection));
  }
});
