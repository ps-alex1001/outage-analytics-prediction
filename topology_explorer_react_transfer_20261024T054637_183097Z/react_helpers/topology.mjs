// Read-only helpers. No React, map-library or generation dependencies.
export function buildTopology(nodesGeoJSON, activeTree) {
  const nodes = new Map(nodesGeoJSON.features.map(f => [f.properties.node_id, f]));
  const children = new Map(), parents = new Map(), edges = new Map();
  for (const [id, from, to] of activeTree.edges) {
    if (!nodes.has(from) || !nodes.has(to)) throw new Error(`Missing endpoint: ${id}`);
    if (parents.has(to)) throw new Error(`Multiple active parents: ${to}`);
    edges.set(id, {id, from, to});
    if (!children.has(from)) children.set(from, []);
    children.get(from).push({nodeId: to, edgeId: id});
    parents.set(to, {nodeId: from, edgeId: id});
  }
  return {nodes, edges, children, parents};
}

export function traceSelection(index, kind, id) {
  let start = id, selectedEdge;
  if (kind === 'edge') {
    selectedEdge = index.edges.get(id);
    if (!selectedEdge) throw new Error('No downstream direction: inactive/candidate edge');
    start = selectedEdge.to;
  } else if (kind !== 'node') throw new Error('kind must be node or edge');
  if (!index.nodes.has(start)) throw new Error(`Unknown node: ${start}`);
  const nodeIds = new Set(), edgeIds = new Set(), stack = [start];
  while (stack.length) {
    const current = stack.pop();
    if (nodeIds.has(current)) continue;
    nodeIds.add(current);
    for (const child of index.children.get(current) || []) {
      edgeIds.add(child.edgeId); stack.push(child.nodeId);
    }
  }
  if (selectedEdge) edgeIds.add(selectedEdge.id);
  const upstreamNodeIds = [start], upstreamEdgeIds = new Set(), seen = new Set([start]);
  let current = start;
  while (index.parents.has(current)) {
    const parent = index.parents.get(current);
    if (seen.has(parent.nodeId)) throw new Error('Cycle in active topology');
    seen.add(parent.nodeId); upstreamNodeIds.push(parent.nodeId);
    upstreamEdgeIds.add(parent.edgeId); current = parent.nodeId;
  }
  return {nodeIds, edgeIds, upstreamNodeIds, upstreamEdgeIds,
    substationNodeId: index.nodes.get(start).properties.source_node_id || null};
}

export function selectionStats(index, selection, edgesGeoJSON) {
  const municipalities = new Set(), barangays = new Set();
  let transformers = 0, routeMetres = 0;
  for (const id of selection.nodeIds) {
    const p = index.nodes.get(id).properties;
    if (p.node_type === 'transformer') transformers++;
    if (p.node_type === 'pole' || p.node_type === 'transformer') {
      if (p.municipality) municipalities.add(p.municipality);
      if (p.barangay) barangays.add(`${p.municipality}|${p.barangay}`);
    }
  }
  for (const feature of edgesGeoJSON.features) {
    const p = feature.properties;
    if (selection.edgeIds.has(p.edge_id) && feature.geometry?.type === 'LineString') {
      routeMetres += Number(p.length_m) || 0;
    }
  }
  return {transformers, routeKm: routeMetres / 1000,
    municipalities: municipalities.size, barangays: barangays.size};
}

export function filterNetwork(nodesGeoJSON, edgesGeoJSON, substationNodeId, feederId = null) {
  const nodes = nodesGeoJSON.features.filter(f => {
    const p = f.properties;
    return p.source_node_id === substationNodeId &&
      (!feederId || p.feeder_id === feederId || p.node_id === substationNodeId);
  });
  const edges = edgesGeoJSON.features.filter(f => {
    const p = f.properties;
    return p.is_tree_edge === true && p.source_node_id === substationNodeId &&
      (!feederId || p.feeder_id === feederId);
  });
  return {nodes: {type: 'FeatureCollection', features: nodes},
    edges: {type: 'FeatureCollection', features: edges}};
}
