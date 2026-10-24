# React integration / data contract

Static source: `../data/topology/react_data/`, relative to this document.
The helper remains at `react_helpers/topology.mjs`; adapt the import below to
the eventual caller. No serving route or dashboard page has been added.

For future integration, serve that directory through your React server, for
example at `/topology/`. Do not fetch these files directly through file:// URLs.
No API key, Python API or data-generation step is required.

```js
import {buildTopology, traceSelection, selectionStats, filterNetwork}
  from './topology.mjs';

const base = '/topology';
const [nodes, edges, tree, feeders, stations] = await Promise.all([
  'network_nodes.geojson', 'network_edges.geojson', 'active_tree.json',
  'feeders.json', 'substations.geojson'
].map(name => fetch(`${base}/${name}`).then(r => {
  if (!r.ok) throw new Error(`${name}: ${r.status}`);
  return r.json();
})));
const graph = buildTopology(nodes, tree);
const balanga = stations.features.find(f =>
  f.properties.asset_id === 'observed-osm:way/790632397');
const stationId = balanga.properties.modeled_source_node_id;
const choices = feeders.filter(f => f.substation_node_id === stationId);
// choices contain stable modeled numbers 2 and 3, with counts 467 and 574.
const visible = filterNetwork(nodes, edges, stationId, choices[0].feeder_id);
const selected = traceSelection(graph, 'node', choices[0].root_node_id);
const metrics = selectionStats(graph, selected, edges); // 467 transformers
// A clicked line uses traceSelection(graph, 'edge', feature.properties.edge_id).
// A clicked pole/transformer uses kind='node' and properties.node_id.
```

## Layers, identifiers and coordinates

All GeoJSON uses longitude, latitude in EPSG:4326. Preserve IDs as exact strings.
Feature identifiers live in **properties**, not an assumed GeoJSON feature.id.

| Layer | Identifier | Useful fields |
|---|---|---|
| network_nodes | node_id | node_type, source_node_id, feeder_id, parent_node_id, network_depth |
| network_edges | edge_id | from_node_id, to_node_id, is_tree_edge, edge_type, source_node_id, feeder_id, length_m |
| substations | asset_id | name, modeled_source_node_id, active_feeder_count, transformer_count, connection_status |
| feeders.json | feeder_id | root_node_id, substation_node_id, modeled_feeder_number, transformer_count |
| administrative polygons | municipality_psgc / barangay_psgc | municipality / barangay |

`node_type` is pole, transformer or substation. Render only LineString edge
geometry in line layers: some logical attachments are Point features because
their endpoints are colocated. Those attachments remain real graph edges for
the modeled reachability calculation. Non-tree edges have no asserted downstream
direction; style them as optional inactive candidates and do not follow them.

`active_tree.json` encodes edges as [edge_id, from_node_id, to_node_id]. It includes
source-connected trees and explicitly unresolved local trees. A node with null
source_node_id is not asserted to reach a substation. Some mapped substations
have no modeled feeder; retain their markers without inventing connections.

## Reproduce the current display

- Substation dropdown: stations with active_feeder_count > 0. Keep the 18 mapped station markers with active modeled feeders on the map.
  Unassigned observations have been removed from this visualization dataset.
- Feeder dropdown: filter feeders.json by substation_node_id. Preserve
  modeled_feeder_number; do not renumber Balanga 2/3 into 1/2.
- Default focus is the selected substation's active network. Other networks
  are optional pale gray context. Do not turn inactive candidate lines into
  active connections merely because they visually cross another line.
- Purple: downstream of selection. Blue: upstream supply path and station
  markers. Orange: selected component. Keep municipality borders and names.
- Hide individual poles at broad zooms; show them on request or close zoom.
  Barangay borders/labels are optional and should appear only at closer zoom.
- Use the local label point files for HTML/React map markers if your map style
  does not include glyphs/fonts. Labels should not obscure station symbols.
- Build line highlighting from selected.edgeIds and upstreamEdgeIds. Graph
  counts remain complete; filtering rendered features does not prune the graph.
- Include a fit-to-substation/feeder control, scrolling zoom and pan.

style.json records the exact palette and original viewer display limits. It
requires no remote image assets. Any GeoJSON-capable React mapping library can
render the layers; the data and graph helper do not require a specific library.

## Data status and audit

These are synthetic/inferred electrical connections, not verified official
PENELCO routes. `data_status`, `is_synthetic` and `is_verified_connectivity` must
remain available in inspection panels. The station display points are existing
points or representative points of observed station geometry, not verified bus
coordinates. All reported headings remain in reported_substations.json; do not
plot unmatched headings at guessed coordinates.

Reported feeder IDs and modeled feeder IDs are separate inventories. The active
reported catalog excludes four explicitly reported No load records. The modeled
feeder list contains 32 groups after below-20 consolidation; Balanga reused its
empty modeled Feeder 2. No customer counts, measured loads, outage events or
switching/protection states are supplied. Municipality/barangay counts refer to
assets present, not entire administrative areas losing power.
