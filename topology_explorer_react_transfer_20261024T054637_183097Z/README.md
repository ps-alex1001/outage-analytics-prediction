# Topology explorer — unzipped viewing transfer

This is the accepted, consolidated output: 32 active modeled feeders, 7,828
transformers, and Balanga Feeders 2/3 with 467/574 transformers. All 1,041 Balanga
transformers remain. The minimum active feeder size is 20 (current smallest: 21).
There are 7,747 station-connected transformers and 81 explicitly unresolved ones.

## For your React.js project

The static assets now live at **../data/topology/react_data/** (relative to this
folder). Future integration can serve that directory through the application; no
public asset copies or dashboard page have been created.
Everything there is GeoJSON or ordinary JSON; React needs no Python, Parquet,
raw building/road data or generator scripts. Start with **REACT_INTEGRATION.md**.
**react_helpers/topology.mjs** implements the current filtering and full
upstream/downstream selection behavior without map-library dependencies.

Important assets:

- `../data/topology/react_data/network_nodes.geojson`: poles, transformers, modeled station nodes.
- `../data/topology/react_data/network_edges.geojson`: lines, attachments and inactive candidates.
- `../data/topology/react_data/active_tree.json`: compact active directed graph for tracing.
- `../data/topology/react_data/feeders.json`: 32 active modeled feeders and stable display numbers.
- `../data/topology/react_data/substations.geojson`: 18 mapped station records with active modeled feeders as display points.
- `../data/topology/react_data/municipalities.geojson`, `barangays.geojson`: borders.
- `../data/topology/react_data/municipality_labels.geojson`, `barangay_labels.geojson`: local label points.
- `../data/topology/react_data/reported_substations.json`: all 24 reported headings, including unmatched names.
- `../data/topology/react_data/dataset_info.json`, `style.json`: counts, model meaning and display conventions.

The datasets are large. Filter to a substation/feeder, hide poles at broad zooms,
and keep all graph edges in the tracing index even when a rendering layer is hidden.
The overview highlight and model counts must not be calculated from a point sample.

## Separate explorer reference

`streamlit_reference/` is a self-contained read-only copy of the working explorer
with its required generated Parquet/CSV data. It is optional for React; use it to
compare your React view against the current UI. From that folder, use an existing
uv environment with the packages in `dependencies.txt`:

```powershell
uv run streamlit run app.py
```

No second environment was created while preparing this folder. No Python project,
raw inputs, generation scripts, node_modules or archive files are included.

## Audit and meaning

`../data/topology/audit/` contains consolidation/transfer tables, source-reported active feeder
records, validation receipts and the active GraphML topology. `../data/topology/previews/` shows
the accepted Balanga interface. `file_inventory.csv` and `checksums.json` list and
fingerprint the transferred files. Retain this reference folder together with `../data/topology/` to preserve the
viewer and audit trail. React itself needs only the relocated static assets and
optional helpers. Inventory and checksum paths are relative to this folder.

Existing original datasets and previous versions were not edited. This folder
duplicates the generated viewing outputs and adds interoperable presentation
exports. Station coordinates derive from existing mapped geometry. Electrical
routes, feeder assignments and short transfer links remain modeled/inferred.
Feeder counts are modeled transformers, not measured electrical load or customers.
Do not represent these as officially verified PENELCO routes or service boundaries.
The 81 unresolved transformers do not have an asserted source connection.

Map footprints, roads and observations derive from © OpenStreetMap contributors.
Retain OpenStreetMap attribution and source licensing when transferring the data.

## Local organization

The 13 React JSON/GeoJSON assets, eight audit files and two preview images were
moved into `../data/topology/`, preserving their names and folder relationships.
`react_helpers/topology.mjs` and all of `streamlit_reference/` remain here unchanged.
The reference viewer retains its adjacent `dataset/` so its entry point continues
to resolve the same files. Existing topology datasets were not overwritten.

The reference dataset manifest, summary and inventory source columns contain
historical paths into the originating `Outage Prediction` project. These are
provenance, not relocated runtime paths; those source inputs are not bundled.
The optional legacy building-infrastructure viewer also refers to original
`data/synthetic` runs, which are not included. The provided `app.py` explicitly
uses the bundled `streamlit_reference/dataset/` instead.

`verification.json` is the original export receipt. See
`organization_verification.json` for checks performed after local relocation.
The project ignores `data/` in Git, so the moved assets remain local.

## Connected-station dataset filter

On request, the visualization export now excludes the 20 station records with
no active modeled feeder. It retains 18 mapped stations, all 32 feeders and the
unchanged active tree. Two unused modeled station nodes and their three inactive
candidate edges were also removed to keep endpoint references consistent. The
original Streamlit reference, previews and audit records retain the full export.
See `../data/topology/station_filter_verification.json` for removed IDs and counts.
