# V2 outage-to-topology impact integration

The existing V2 event CSV and `data/topology/react_data/` are immutable inputs.
The static current topology is applied to historical synthetic events as a scenario
model. It does not establish which electrical assets existed at historical dates.

```powershell
uv run python scripts/map_outage_topology_impacts.py
uv run python scripts/verify_outage_topology_impacts.py
uv run python -m unittest backend.tests.test_impacts backend.tests.test_topology -v
```

The current output is `data/impacts/v2_topology_1_2_0/`. Versions 1.0 and 1.1 remain
preserved in its original directory. The builder refuses to
overwrite an existing run. For a new simulation, pass a new directory under
`data/impacts/`, an optional `--seed`, and an optional `--weights` JSON containing
the cause/type weights. Update `backend/datasets.py` to pin a reviewed replacement
run. Restart the backend after replacing datasets to clear process-local caches.

The API rejects event/topology hash mismatches instead of displaying stale
precomputed impacts. Existing overview queries and topology routes remain available.

Endpoints:

- `GET /api/impacts/metadata`: filter choices and mapping coverage.
- `GET /api/impacts/events`: paginated search, year/municipality/barangay/cause/status
  filters, and allowlisted date/duration/location sorting.
- `GET /api/impacts/events/{outage_id}`: original event, source assignment,
  precomputed affected/upstream topology IDs, measures and barangay outlines.
- `GET /api/impacts/network?feeder_id=...`: cached, compressed geometry for one
  connected feeder, preserving node/edge IDs and active-tree orientation.

Spatial & Event Analysis (`/spatial`) uses one map and a selectable table. On the
local checkout this page was a placeholder; no existing table was removed. Row
selection synchronizes the map and details. Orange marks the simulated fault;
red denotes modeled supply loss; blue is upstream context; gray is unaffected
infrastructure in the selected feeder. Dashed outlines distinguish the original
event barangay. Component inspection never changes the precomputed impact.

Version 1.2 chooses a single source at the start of the affected branch. To
reduce tiny outages, a sampled source can move upstream within the same active
feeder and original event barangay. Its line and upstream anchor must both be
geographically eligible. The soft target is three transformers and 0.5 km of
lines, bounded by 30 steps, 1 km upstream travel, 100 transformers and 10 km of
lines. Already-large sources and supply-loss feeder scenarios remain intact.
Targets can remain unmet when geography or topology prevents a larger branch.
Initial sampled IDs are provenance only; affected traversal starts at the FINAL
source. No separate cut location or connection marker is displayed.

The relationship data is normalized: join `outage_fault_mapping.impact_set_id` to
`affected_assets.impact_set_id` for event-to-asset memberships. `impact_sets.parquet`
stores compact affected/upstream ID lists and summaries, and
`outage_affected_barangays.parquet` stores per-event administrative relationships.
No geometry is copied for each event. Distribution length counts the explicitly
documented distribution edge types; station/junction connectors and zero-length
transformer attachments are not added as distribution-line route length.

Read the run's `methodology.md`, `validation.json`, `independent_validation.json`
and `impact_size_distribution.csv` for the assumptions and measured results.
The large-impact review flag uses at least 1,000 transformers or 20 intersected
barangays; it is a review trigger, not an assertion of a topology error. Distribution
maxima should also be reviewed even if they do not cross that particular flag.

Source inputs and output data are already ignored by this repository's `data/`
rule. Preserve the impact output directory along with V2 and the topology export
when transferring the application. No infrastructure or outage generation, customer
counts, geographic surface-area estimates or predictive modeling is performed.

The source ripple sits on the selected transformer or the upstream node of the
selected line. Red starts at that anchor. A soft translucent screen-space halo
follows the affected lines, with stronger strokes and larger outlined
transformers. Transformer radii increase smoothly from tiny dots at province
scale to their full size at zoom 17. Affected poles use small red dots with a fine
white edge: hidden below zoom 15, fading in by zoom 16 and reaching their current
2.2px radius at zoom 17. Lines and halos also thin out with distance. The source
keeps its recognizable red center and ripples.
The halo does not define geographic coverage or change any metric.
Unaffected infrastructure is muted. Both ripple and halo have layer controls;
the ripple respects reduced-motion preferences.

The impact map defaults to muted OpenStreetMap raster tiles, with a Background
selector for Street map (muted), Color street map, Satellite imagery or Plain
background. Color street map renders OSM's original colors; satellite uses Esri
World Imagery with its source credits displayed on the map. Hidden background
layers do not request tiles. Tile errors fall back to plain
without blocking outage data; choosing a map background again retries. Browser HTTP
caching is preserved and only visible tiles are requested. Attribution stays
visible. `NEXT_PUBLIC_STREET_MAP_TILES` can configure an alternate compatible
tile endpoint (retain the appropriate provider attribution when changing it).
The street background requires internet access. No base layer changes electrical
connectivity, asset coordinates or impact measures.

Additional validation:

```powershell
node --test scripts/tests/test_outage_origin.mjs
```

`impact_size_comparison.csv` records the before/after measures and demonstrates
the source identity changes alongside their impact sizes. The original V2 events and topology
retain their exact source hashes.
