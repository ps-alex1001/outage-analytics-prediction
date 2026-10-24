import * as maplibregl from "maplibre-gl";
import type { Position } from "geojson";
import { getVisualizationColors } from "./visualization-theme";
import {
  emptyCollection, type Asset, type Assets, type LoadedNetwork, type Metadata,
  type Selection, type Station, type Trace, stationName,
} from "./topology";

type Callbacks = {
  ready: () => void;
  select: (selection: Selection) => void;
  station: (station: Station) => void;
  error: (message: string) => void;
};

const lineLayers = ["network-lines", "supply-casing", "supply-links", "line-hit"];
const lead: maplibregl.ExpressionSpecification = ["in", ["get", "edge_type"],
  ["literal", ["inferred_supply_connection", "inferred_station_lead"]]];

function colors() {
  const style = getComputedStyle(document.documentElement);
  const read = (name: string) => style.getPropertyValue(`--network-${name}`).trim();
  return {
    ...getVisualizationColors(),
    station: read("station"), downstream: read("downstream"), selected: read("selected"),
    remaining: read("remaining"), pole: read("pole"), boundary: read("boundary"),
  };
}

function boundsFor(features: Asset[]) {
  const bounds = new maplibregl.LngLatBounds();
  for (const feature of features) {
    if (feature.geometry.type === "Point") bounds.extend(feature.geometry.coordinates as [number, number]);
  }
  return bounds;
}

function duration() { return matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 400; }

/** One map for the lifetime of the page. Filters, paint and feature state change
 * in place; theme/feeder changes never recreate the map or replace its style. */
export class TopologyMap {
  readonly map: maplibregl.Map;
  private resize: ResizeObserver;
  private metadata?: Metadata;
  private network?: LoadedNetwork;
  private feeder = "";
  private upstreamIds: string[] = [];
  private nodeRoles = new Map<string, number>();
  private edgeRoles = new Map<string, number>();
  private labels = new Map<string, string>();
  private ready = false;

  private deferUntilReady(work: () => void) {
    if (this.ready) return false;
    this.map.once("load", work);
    return true;
  }

  constructor(container: HTMLElement, callbacks: Callbacks) {
    maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");
    this.map = new maplibregl.Map({
      container,
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background",
        paint: { "background-color": colors().map.background } }] },
      center: [120.47, 14.67], zoom: 9, minZoom: 7, maxZoom: 19,
      attributionControl: false, renderWorldCopies: false,
    });
    this.map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    this.map.addControl(new maplibregl.FullscreenControl(), "top-right");
    this.map.addControl(new maplibregl.ScaleControl(), "bottom-left");
    this.map.addControl(new maplibregl.AttributionControl({ compact: true,
      customAttribution: 'Map data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>' }), "bottom-right");
    this.map.getCanvas().setAttribute("aria-label", "Modeled electrical network map. Use arrow keys to pan and plus or minus to zoom. Asset inspection is also available in the component search.");
    this.resize = new ResizeObserver(() => this.map.resize());
    this.resize.observe(container);
    this.map.on("error", event => callbacks.error(`Map rendering error: ${event.error.message}`));
    this.map.on("load", () => {
      this.initialize();
      this.ready = true;
      callbacks.ready();
    });
    const pick = (point: maplibregl.Point) => this.map.queryRenderedFeatures(
      [[point.x - 5, point.y - 5], [point.x + 5, point.y + 5]],
      { layers: ["stations", "transformers", "poles", "line-hit"] },
    );
    this.map.on("mousemove", event => {
      if (this.ready) this.map.getCanvas().style.cursor = pick(event.point).length ? "pointer" : "";
    });
    this.map.on("click", event => {
      if (!this.ready) return;
      const features = pick(event.point);
      const station = features.find(f => f.layer.id === "stations");
      if (station) {
        const record = this.metadata?.substations.features.find(f => f.properties.asset_id === station.properties.asset_id);
        if (record) callbacks.station(record);
        return;
      }
      const node = features.find(f => f.properties.node_id);
      const edge = features.find(f => f.properties.edge_id);
      if (node) callbacks.select({ kind: "node", id: node.properties.node_id });
      else if (edge) callbacks.select({ kind: "edge", id: edge.properties.edge_id });
    });
  }

  private initialize() {
    const map = this.map;
    for (const id of ["municipalities", "municipality-labels", "barangays", "barangay-labels", "stations", "station-labels", "selected-point"]) {
      map.addSource(id, { type: "geojson", data: emptyCollection() });
    }
    map.addSource("nodes", { type: "geojson", data: emptyCollection(), promoteId: "node_id" });
    map.addSource("edges", { type: "geojson", data: emptyCollection(), promoteId: "edge_id" });
    map.addLayer({ id: "municipality-borders", type: "line", source: "municipalities",
      paint: { "line-width": 1.4, "line-opacity": 0.65 } });
    map.addLayer({ id: "barangay-borders", type: "line", source: "barangays", minzoom: 12,
      layout: { visibility: "none" }, paint: { "line-width": 0.7, "line-opacity": 0.55 } });
    map.addLayer({ id: "network-lines", type: "line", source: "edges", filter: ["!", lead],
      paint: { "line-width": ["match", ["feature-state", "role"], 3, 7, 1, 4, 2, 4, 2] } });
    map.addLayer({ id: "supply-casing", type: "line", source: "edges", filter: lead,
      paint: { "line-width": 5 } });
    map.addLayer({ id: "supply-links", type: "line", source: "edges", filter: lead,
      paint: { "line-width": ["case", ["==", ["feature-state", "role"], 3], 7, 2.5], "line-dasharray": [2, 2] } });
    map.addLayer({ id: "line-hit", type: "line", source: "edges",
      paint: { "line-width": 12, "line-opacity": 0 } });
    map.addLayer({ id: "poles", type: "circle", source: "nodes", minzoom: 14,
      filter: ["==", ["get", "node_type"], "pole"], paint: { "circle-radius": 2, "circle-opacity": 0.8 } });
    map.addLayer({ id: "transformers", type: "circle", source: "nodes",
      filter: ["==", ["get", "node_type"], "transformer"],
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.5, 14, 4.5], "circle-stroke-width": 1 } });
    map.addLayer({ id: "selected-point", type: "circle", source: "selected-point",
      paint: { "circle-radius": 8, "circle-stroke-width": 2 } });
    this.stationIcons();
    map.addLayer({ id: "stations", type: "symbol", source: "stations",
      layout: { "icon-image": "station-active",
        "icon-size": 0.65, "icon-allow-overlap": true, "icon-ignore-placement": true } });
    map.moveLayer("selected-point");
    for (const [id, minzoom] of [["municipality-labels", 8], ["barangay-labels", 13], ["station-labels", 11.5]] as const) {
      map.addLayer({ id, type: "symbol", source: id, minzoom,
        layout: { "icon-image": ["get", "_labelImage"], "icon-size": 0.5,
          "icon-anchor": id === "station-labels" ? "top" : "center",
          "icon-offset": id === "station-labels" ? [0, 18] : [0, 0],
          "icon-padding": 5, visibility: id === "barangay-labels" ? "none" : "visible" } });
    }
    this.setTheme();
  }

  private source(id: string, data: Assets) {
    (this.map.getSource(id) as maplibregl.GeoJSONSource).setData(data);
  }

  private stationIcons() {
    const palette = colors();
    for (const active of [true, false]) {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 28;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = palette.chart.background;
      ctx.fillRect(0, 0, 28, 28);
      ctx.fillStyle = palette.station;
      ctx.fillRect(3, 3, 22, 22);
      if (!active) {
        ctx.fillStyle = palette.chart.background;
        ctx.fillRect(7, 7, 14, 14);
      }
      const key = active ? "station-active" : "station-unassigned";
      const data = ctx.getImageData(0, 0, 28, 28);
      if (this.map.hasImage(key)) this.map.updateImage(key, data);
      else this.map.addImage(key, data);
    }
  }

  // Local canvas label sprites avoid external font/glyph services and DOM markers.
  private labelImage(key: string, text: string) {
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d")!;
    ctx.font = "500 24px sans-serif";
    canvas.width = Math.min(600, Math.ceil(ctx.measureText(text).width) + 16);
    canvas.height = 38;
    ctx.font = "500 24px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineWidth = 6;
    ctx.lineJoin = "round";
    ctx.strokeStyle = colors().chart.background;
    ctx.strokeText(text, canvas.width / 2, 19, canvas.width - 12);
    ctx.fillStyle = colors().chart.text;
    ctx.fillText(text, canvas.width / 2, 19, canvas.width - 12);
    const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
    if (this.map.hasImage(key)) this.map.updateImage(key, image);
    else this.map.addImage(key, image);
    this.labels.set(key, text);
  }

  setMetadata(metadata: Metadata) {
    if (this.deferUntilReady(() => this.setMetadata(metadata))) return;
    this.metadata = metadata;
    this.source("stations", metadata.substations);
    const labels = metadata.substations.features.map(station => {
      const key = `station-label-${station.properties.asset_id}`;
      this.labelImage(key, stationName(station));
      return { ...station, properties: { ...station.properties, _labelImage: key } };
    });
    this.source("station-labels", { type: "FeatureCollection", features: labels });
    this.map.setLayerZoomRange("station-labels", metadata.style.station_label_min_zoom, 24);
    this.map.setLayerZoomRange("municipality-labels", metadata.style.municipality_label_min_zoom, 24);
    this.fitAll();
  }

  setBoundaries(level: "municipality" | "barangay", data: { boundaries: Assets; labels: Assets }) {
    if (this.deferUntilReady(() => this.setBoundaries(level, data))) return;
    this.source(level === "municipality" ? "municipalities" : "barangays", data.boundaries);
    const labels = data.labels.features.map(feature => {
      const key = `${level}-${feature.properties[`${level}_psgc`]}`;
      this.labelImage(key, String(feature.properties[level]));
      return { ...feature, properties: { ...feature.properties, _labelImage: key } };
    });
    this.source(`${level}-labels`, { type: "FeatureCollection", features: labels });
  }

  setTheme() {
    const map = this.map, c = colors();
    if (!map.getLayer("network-lines")) return;
    map.setPaintProperty("background", "background-color", c.map.background);
    for (const id of ["municipality-borders", "barangay-borders"]) map.setPaintProperty(id, "line-color", c.boundary);
    map.setPaintProperty("network-lines", "line-color", ["match", ["feature-state", "role"],
      3, c.selected, 1, c.downstream, 2, c.station, c.remaining]);
    map.setPaintProperty("supply-casing", "line-color", c.chart.background);
    map.setPaintProperty("supply-links", "line-color", ["case", ["==", ["feature-state", "role"], 3], c.selected, c.station]);
    map.setPaintProperty("poles", "circle-color", c.pole);
    map.setPaintProperty("transformers", "circle-color", ["match", ["feature-state", "role"],
      3, c.selected, 1, c.downstream, c.remaining]);
    map.setPaintProperty("transformers", "circle-stroke-color", c.chart.background);
    map.setPaintProperty("selected-point", "circle-color", c.selected);
    map.setPaintProperty("selected-point", "circle-stroke-color", c.chart.background);
    this.stationIcons();
    for (const [key, text] of this.labels) this.labelImage(key, text);
  }

  clearNetwork() {
    this.network = undefined;
    this.nodeRoles.clear(); this.edgeRoles.clear(); this.upstreamIds = [];
    if (!this.ready) return;
    for (const source of ["nodes", "edges"]) {
      this.map.removeFeatureState({ source });
      this.source(source, emptyCollection());
    }
    this.source("selected-point", emptyCollection());
  }

  setNetwork(network: LoadedNetwork) {
    if (this.deferUntilReady(() => this.setNetwork(network))) return;
    this.clearNetwork();
    this.network = network;
    this.source("nodes", network.nodes);
    // Logical Point edges stay in the graph but never enter a line layer.
    this.source("edges", { type: "FeatureCollection",
      features: network.edges.features.filter(f => f.geometry.type === "LineString") });
  }

  setFeeder(feeder: string) { this.feeder = feeder; this.filters(); }

  private filters() {
    if (!this.ready) return;
    const scope: maplibregl.FilterSpecification = this.feeder
      ? ["==", ["get", "feeder_id"], this.feeder] : ["literal", true];
    const lines: maplibregl.FilterSpecification = ["any", scope,
      ["in", ["get", "edge_id"], ["literal", this.upstreamIds]]];
    for (const id of lineLayers) {
      this.map.setFilter(id, id === "network-lines" ? ["all", lines, ["!", lead]]
        : id === "line-hit" ? lines : ["all", lines, lead]);
    }
    for (const [id, type] of [["poles", "pole"], ["transformers", "transformer"]]) {
      this.map.setFilter(id, ["all", scope, ["==", ["get", "node_type"], type]]);
    }
  }

  setSelection(selection: Selection, trace: Trace) {
    if (this.deferUntilReady(() => this.setSelection(selection, trace))) return;
    if (!this.network) return;
    const nodeRoles = new Map<string, number>(), edgeRoles = new Map<string, number>();
    for (const id of trace.nodeIds) {
      if (this.network.graph.nodes.get(id)?.properties.node_type === "transformer") nodeRoles.set(id, 1);
    }
    // Only rendered features need state; poles have a fixed style and logical
    // Point edges live exclusively in the graph. This keeps interactions lean.
    for (const id of trace.upstreamEdgeIds) {
      if (this.network.graph.edges.get(id)?.geometry.type === "LineString") edgeRoles.set(id, 2);
    }
    for (const id of trace.edgeIds) {
      if (this.network.graph.edges.get(id)?.geometry.type === "LineString") edgeRoles.set(id, 1);
    }
    (selection.kind === "node" ? nodeRoles : edgeRoles).set(selection.id, 3);
    const apply = (source: string, next: Map<string, number>, previous: Map<string, number>) => {
      for (const id of previous.keys()) if (!next.has(id)) this.map.removeFeatureState({ source, id });
      for (const [id, role] of next) if (role !== previous.get(id)) this.map.setFeatureState({ source, id }, { role });
    };
    apply("nodes", nodeRoles, this.nodeRoles); apply("edges", edgeRoles, this.edgeRoles);
    this.nodeRoles = nodeRoles; this.edgeRoles = edgeRoles;
    this.upstreamIds = [...trace.upstreamEdgeIds];
    this.filters();
    const selected = selection.kind === "node" ? this.network.graph.nodes.get(selection.id) : this.network.graph.edges.get(selection.id);
    this.source("selected-point", { type: "FeatureCollection", features: selected?.geometry.type === "Point" ? [selected] : [] });
  }

  inspectStation(station: Station) {
    if (this.deferUntilReady(() => this.inspectStation(station))) return;
    this.source("selected-point", { type: "FeatureCollection", features: [station] });
  }

  showPoles(show: boolean) {
    if (this.deferUntilReady(() => this.showPoles(show))) return;
    this.map.setLayerZoomRange("poles", show ? 0 : 14, 24);
  }
  showBarangays(show: boolean) {
    if (this.deferUntilReady(() => this.showBarangays(show))) return;
    for (const id of ["barangay-borders", "barangay-labels"]) this.map.setLayoutProperty(id, "visibility", show ? "visible" : "none");
  }
  fitAll() {
    if (this.metadata) this.fit(boundsFor(this.metadata.substations.features));
  }
  fitNetwork() {
    if (this.network) this.fit(boundsFor(this.network.nodes.features.filter(f => !this.feeder
      || f.properties.feeder_id === this.feeder || f.properties.node_id === this.network!.station_id)));
  }
  focusAsset(asset: Asset) {
    let point: Position | undefined;
    if (asset.geometry.type === "Point") point = asset.geometry.coordinates;
    else if (asset.geometry.type === "LineString") point = asset.geometry.coordinates[Math.floor(asset.geometry.coordinates.length / 2)];
    if (point) this.map.easeTo({ center: point as [number, number], zoom: Math.max(14, this.map.getZoom()), duration: duration() });
  }
  private fit(bounds: maplibregl.LngLatBounds) {
    if (!bounds.isEmpty()) this.map.fitBounds(bounds, { padding: 45, maxZoom: 15, duration: duration() });
  }
  destroy() { this.resize.disconnect(); this.map.remove(); }
}
