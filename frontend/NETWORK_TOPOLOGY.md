# Network Topology

Run the existing backend from the repository root:

```powershell
.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Run `npm run dev` in `frontend`, then open `http://localhost:3000/network`.
The new page uses `NEXT_PUBLIC_API_URL` if set, otherwise `http://127.0.0.1:8000`.
The backend permits the existing localhost:3000 origin and its 127.0.0.1 equivalent.

## Source fidelity

The implementation was based on the actual transfer's
`streamlit_reference/utils/power_connections_view.py`, `persistent_infrastructure_map.py`,
`theme.py`, `react_helpers/topology.mjs`, and `REACT_INTEGRATION.md`, together with
`data/topology/react_data/style.json` and the exported data. Those references and
datasets are preserved. The Streamlit environment is not installed in this project;
source inspection and comparisons against its exported helper were used.

Reproduced behaviors:

- 18 mapped stations with active modeled feeders in both the map and selector.
  At the user’s request, 20 unassigned station records were removed from the
  visualization dataset, along with two unused modeled station nodes and three
  inactive candidate links. The active tree and all 32 feeders are unchanged.
- Station-specific feeder options with original IDs and modeled numbers, including
  Balanga feeders 2 and 3 (467 and 574 transformers).
- Blue square markers for the retained connected stations.
- Gray remaining network, purple downstream paths, blue upstream paths,
  dashed inferred station links with a casing, and orange selected assets.
- Clicking a station loads its modeled network. Stations are also available in
  the keyboard-accessible component search.
- Node/edge selection uses the existing active-tree traversal, with no spatial
  intersection inference. Logical Point edges participate in tracing and are
  accessible in component search; only LineStrings enter line layers.
- Whole-feeder reset, fit network/all stations, pan, wheel and button zoom,
  fullscreen, scale, municipality borders/labels, optional barangay context,
  automatic poles at zoom 14+, and an explicit all-zoom pole toggle.
- Component search by exact ID or location, limited to the first 50 matches.
  Attribute inspection retains the export's provenance and connectivity flags.
- Transformer clicks keep the transformer selected in orange and highlight the
  downstream branch from its recorded connection pole. All 7,828 transformers
  in the export are terminal nodes; the branch overlay, origin pole ID and count
  are explicitly labeled so they do not imply downstream supply through a
  transformer. The raw tree traversal and line/pole selection remain unchanged.
- Shared dashboard appearance controls update map paint and local label sprites
  without replacing the map. Symbols, dash patterns, text and visible controls
  supplement the colorblind palette. Fit/focus respect reduced-motion settings.

## Loading and caching

`backend/topology.py` registers read-only endpoints on the existing FastAPI app:

| Endpoint | Purpose |
| --- | --- |
| `/api/topology/metadata` | Small station/feeder catalog, display settings and dataset notice |
| `/api/topology/network?station_id=...` | Only one active station's nodes, active edges and tree |
| `/api/topology/boundaries/municipality` | Municipality polygons and local label points |
| `/api/topology/boundaries/barangay` | Optional barangay context, fetched on demand |

Metadata does not load the large network files. A locked, process-local index is
built once on the first network request. It validates endpoint membership against
`active_tree.json`, groups existing features by station, and never changes files.
Serialized responses have a bounded eight-entry cache, gzip, ETags and a five-minute
HTTP cache lifetime. Restart the backend after replacing an export.

The browser retains up to three station networks and their connectivity indices
outside React state. Concurrent identical requests share a promise. Feeder changes
reuse the selected station's network and update MapLibre filters. Selection updates
feature state in place; it does not rebuild the graph. Stale station responses are
ignored. MapLibre sources/layers render assets, with local canvas label sprites;
there are no external font requests or per-asset HTML markers.

Initial measurements on the local export: Balanga's selected network is 14,975,613
bytes of JSON / 2,321,699 bytes gzip, versus 115,806,527 bytes for the complete
provincial node and edge files before including the tree. The first local index and
serialization measurement took 12.9 seconds on this filesystem; markers are shown
while this cold request runs. Cached requests avoid that parsing/indexing work.

## Deliberate limits

This phase implements stored connectivity inspection, not outage propagation,
customer interruptions, ML, measured loading, or switching alternatives. No data is
regenerated. The full province context toggle and route/area impact metrics from
Streamlit are omitted. The 81 unresolved transformers are not assigned to a station
or silently included in a connected feeder. Unassigned station positions are excluded from this page and the visualization
dataset; the original Streamlit reference and historical audit records retain them. The clean geographic background matches the source's white-bg
viewer, with a dark equivalent; no road basemap is required.

## Checks

```powershell
# From the repository root (requires the local, Git-ignored data export)
.venv/Scripts/python.exe -B -m unittest backend.tests.test_topology -v

# From frontend
node node_modules/typescript/bin/tsc --noEmit
node node_modules/eslint/bin/eslint.js .
node --test tests/topology.test.mjs
```

The graph test compares all 32 feeder traversals against the original JavaScript
helper. Backend checks cover metadata isolation, all station scopes, Point edges,
unresolved components, invalid inputs, compression/ETags, and boundary counts.

## Verification results

- TypeScript and scoped ESLint: passed for the new page, map, graph helper,
  component tests and sidebar change.
- Backend: seven integration tests passed, including the connected-station filter,
  endpoint integrity, all 32 feeder counts, logical edges, compression and boundaries.
- Frontend graph helper: four tests passed; all 32 feeder traversals match the
  preserved original JavaScript helper. Candidate edges are not traceable.
  Transformer inspection is also checked against the original helper's traversal
  from the recorded parent pole, including logical Point attachments, missing
  parents and unchanged handling of actual transformer descendants.
- Live API after filtering: 18 stations, zero without active feeders, 32 feeders.
- Browser checks before the final station filter: Balanga feeder switching
  (467/574), switching to Alasasin (48), single-transformer inspection (1),
  station inspection, fit controls, zoom buttons, fullscreen, pole/barangay
  toggles, light/dark appearance and colorblind mode. A single map canvas remained.
- A hot-reload startup race was found and fixed by deferring map source/layer
  operations until the map load event. The final browser reload/retest and further
  mobile/panning/wheel checks could not be completed: automatic approval review
  rejected browser access after reaching its usage limit.
- Full-project ESLint remains blocked by existing Overview effect errors and
  warnings in the vendored MapLibre worker. Overview source was left unchanged.
