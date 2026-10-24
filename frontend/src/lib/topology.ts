import type { Feature, FeatureCollection, Geometry, Point } from "geojson";

export type Properties = Record<string, string | number | boolean | null>;
export type Asset = Feature<Geometry, Properties>;
export type Assets = FeatureCollection<Geometry, Properties>;
export type Station = Feature<Point, Properties & {
  asset_id: string;
  name: string | null;
  modeled_source_node_id: string | null;
  active_feeder_count: number;
  transformer_count: number;
}>;
export type Feeder = {
  feeder_id: string;
  root_node_id: string;
  substation_node_id: string;
  modeled_feeder_number: number;
  transformer_count: number;
  municipality: string;
  barangay: string;
};
export type Metadata = {
  substations: FeatureCollection<Point, Station["properties"]>;
  feeders: Feeder[];
  style: {
    municipality_label_min_zoom: number;
    station_label_min_zoom: number;
    line_width: Record<string, number>;
  };
  dataset: { counts: Record<string, number>; notice: string; source_version: string };
};
export type Network = {
  station_id: string;
  nodes: Assets;
  edges: Assets;
  tree: { edges: [string, string, string][] };
};
export type Selection = { kind: "node" | "edge"; id: string };
export type Graph = {
  nodes: Map<string, Asset>;
  edges: Map<string, Asset>;
  children: Map<string, { nodeId: string; edgeId: string }[]>;
  parents: Map<string, { nodeId: string; edgeId: string }>;
};
export type LoadedNetwork = Network & { graph: Graph };
export type Trace = {
  nodeIds: Set<string>;
  edgeIds: Set<string>;
  upstreamEdgeIds: Set<string>;
  transformers: number;
};
export type InspectionTrace = Trace & { connectionPoleId?: string };

export const emptyCollection = (): Assets => ({ type: "FeatureCollection", features: [] });

// Typed adaptation of the preserved react_helpers/topology.mjs. This is graph
// inspection only: no spatial inference, switching, or outage simulation.
export function buildTopology(network: Network): Graph {
  const nodes = new Map(network.nodes.features.map(f => [String(f.properties.node_id), f]));
  const activeIds = new Set(network.tree.edges.map(([id]) => id));
  const edges = new Map(network.edges.features
    .filter(f => activeIds.has(String(f.properties.edge_id)))
    .map(f => [String(f.properties.edge_id), f]));
  const children: Graph["children"] = new Map();
  const parents: Graph["parents"] = new Map();
  for (const [id, from, to] of network.tree.edges) {
    if (!nodes.has(from) || !nodes.has(to) || !edges.has(id)) throw new Error("Incomplete topology export");
    if (parents.has(to)) throw new Error("Multiple active parents in topology export");
    if (!children.has(from)) children.set(from, []);
    children.get(from)!.push({ nodeId: to, edgeId: id });
    parents.set(to, { nodeId: from, edgeId: id });
  }
  return { nodes, edges, children, parents };
}

export function traceSelection(graph: Graph, selection: Selection): Trace {
  const edge = selection.kind === "edge" ? graph.edges.get(selection.id) : undefined;
  const start = edge ? String(edge.properties.to_node_id) : selection.id;
  if (!graph.nodes.has(start)) throw new Error("Unknown topology component");
  const nodeIds = new Set<string>(), edgeIds = new Set<string>();
  const stack = [start];
  let transformers = 0;
  while (stack.length) {
    const current = stack.pop()!;
    if (nodeIds.has(current)) continue;
    nodeIds.add(current);
    if (graph.nodes.get(current)!.properties.node_type === "transformer") transformers++;
    for (const child of graph.children.get(current) ?? []) {
      edgeIds.add(child.edgeId);
      stack.push(child.nodeId);
    }
  }
  if (edge) edgeIds.add(selection.id);
  const upstreamEdgeIds = new Set<string>(), seen = new Set([start]);
  let current = start;
  while (graph.parents.has(current)) {
    const parent = graph.parents.get(current)!;
    if (seen.has(parent.nodeId)) throw new Error("Cycle in topology export");
    seen.add(parent.nodeId);
    upstreamEdgeIds.add(parent.edgeId);
    current = parent.nodeId;
  }
  return { nodeIds, edgeIds, upstreamEdgeIds, transformers };
}

/** Transformers in this export are terminal attachments, not through-nodes.
 * Inspect their recorded pole's branch so clicking a transformer can show the
 * surrounding downstream network. Keep the actual selection and raw traversal
 * unchanged, and expose the branch origin for an explicit label in the UI. */
export function traceAssetInspection(graph: Graph, selection: Selection): InspectionTrace {
  const node = selection.kind === "node" ? graph.nodes.get(selection.id) : undefined;
  if (node?.properties.node_type === "transformer" && !graph.children.get(selection.id)?.length) {
    const parent = graph.parents.get(selection.id);
    if (parent && graph.nodes.get(parent.nodeId)?.properties.node_type === "pole") {
      return {
        ...traceSelection(graph, { kind: "node", id: parent.nodeId }),
        connectionPoleId: parent.nodeId,
      };
    }
  }
  return traceSelection(graph, selection);
}

const API = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
async function fetchTopology<T>(path: string): Promise<T> {
  const response = await fetch(`${API}/api/topology/${path}`, { cache: "no-cache" });
  if (!response.ok) throw new Error(`Topology request failed (${response.status}). Check the FastAPI server and topology assets.`);
  return response.json() as Promise<T>;
}

let metadataPromise: Promise<Metadata> | undefined;
export function loadMetadata() {
  metadataPromise ??= fetchTopology<Metadata>("metadata").then(data => ({
    ...data,
    substations: { ...data.substations, features: data.substations.features.filter(station => station.properties.active_feeder_count > 0) },
  })).catch(error => {
    metadataPromise = undefined;
    throw error;
  });
  return metadataPromise;
}

// A bounded cache outside React state. Concurrent requests for the same station
// share their download and build the active-tree index just once.
const networkCache = new Map<string, Promise<LoadedNetwork>>();
export function loadNetwork(stationId: string) {
  const cached = networkCache.get(stationId);
  if (cached) {
    networkCache.delete(stationId);
    networkCache.set(stationId, cached);
    return cached;
  }
  const pending = fetchTopology<Network>(`network?${new URLSearchParams({ station_id: stationId })}`)
    .then(network => ({ ...network, graph: buildTopology(network) }))
    .catch(error => { networkCache.delete(stationId); throw error; });
  networkCache.set(stationId, pending);
  if (networkCache.size > 3) networkCache.delete(networkCache.keys().next().value!);
  return pending;
}

const boundaryCache = new Map<string, Promise<{ boundaries: Assets; labels: Assets }>>();
export function loadBoundaries(level: "municipality" | "barangay") {
  let result = boundaryCache.get(level);
  if (!result) {
    result = fetchTopology<{ boundaries: Assets; labels: Assets }>(`boundaries/${level}`)
      .catch(error => { boundaryCache.delete(level); throw error; });
    boundaryCache.set(level, result);
  }
  return result;
}

export function stationName(station: Station) {
  return station.properties.name ?? `Unnamed station · OSM ${station.properties.asset_id.split("/").at(-1)}`;
}

export function assetName(asset: Asset) {
  const p = asset.properties;
  return `${p.node_type ?? p.edge_type ?? "Component"}${p.barangay ? ` · ${p.barangay}` : ""}`.replaceAll("_", " ");
}
