# Network Topology display

Changes apply to `/network`; `/spatial` keeps its red outage styling and existing behavior.

- The initial view has no selected station or component. All current topology
  nodes/connections and all cleaned observed OSM infrastructure are muted gray.
- `/api/topology/context` returns display geometry with compact provenance and
  identifiers. It includes inactive candidate connections and disconnected nodes
  for context, without inserting them into `active_tree.json` or any trace.
- The current context has 87,958 topology nodes, 92,847 topology connections and
  3,761 observed OSM records. Older generation outputs and service-zone estimates
  are not duplicated over the authoritative current topology.
- Selecting a connected generated component loads its station-scoped active
  graph. Orange marks downstream, blue upstream, and orange the selected asset.
  Observed or inactive context records can be inspected without inventing paths.
- Clear selection / Show all restores the gray overview. The station selector
  also has an All infrastructure option.
- Backgrounds: muted streets, color streets, satellite imagery and plain.
  Provider credits, automatic fallback and retry behavior match the outage map.
- Poles are hidden below zoom 15 and fade in by zoom 16. Transformer, line and
  pole sizes vary with zoom. The pole checkbox controls both context and active
  network poles.

The stored active model has 32 `inferred_station_lead` edges originating at 18
substation nodes whose asset IDs match observed OSM records. Their links are
explicitly marked as inferred and unverified; the recorded on-site routes are
not verified. The other observed OSM lines, poles and towers are not automatically
part of this radial graph. Geometry contact or overlap is not electrical proof.

Validation: `python -m unittest backend.tests.test_topology -v`; frontend TypeScript
and ESLint checks; browser checks for neutral overview, selection, clear,
background switching and zoom visibility.
