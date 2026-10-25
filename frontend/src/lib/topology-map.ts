import * as maplibregl from "maplibre-gl";
import type { Position } from "geojson";
import { getVisualizationColors } from "./visualization-theme";
import { outageOriginPoint } from "./outage-origin";
import {
  emptyCollection, type Asset, type Assets, type LoadedNetwork, type Metadata,
  type Selection, type Station, type Trace, stationName,
} from "./topology";

type Callbacks = {
  ready: () => void;
  select: (selection: Selection) => void;
  station: (station: Station) => void;
  error: (message: string) => void;
  basemapError?: () => void;
  context?: (asset: Asset) => void;
};

const lineLayers = ["network-lines", "supply-casing", "supply-links", "line-hit"];
const streetTiles = process.env.NEXT_PUBLIC_STREET_MAP_TILES ?? "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const satelliteTiles = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
export type MapBackground = "street" | "color" | "satellite" | "plain";
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
  private originMarker?: maplibregl.Marker;
  private originVisible = true;
  private basemap: MapBackground = "plain";
  private failedBasemaps = new Set<string>();
  private contextData?: Assets;
  private hasSelection = false;
  private impactPreparation = 0;
  private impactShown = false;
  private impactFade = 1;
  private impactFadeFrame?: number;
  private impactVisibility?: Parameters<TopologyMap["setImpactLayers"]>[0];
  private impactBoundaryKey = "";
  private disposed = false;

  private deferUntilReady(work: () => void) {
    if (this.ready) return false;
    this.map.once("load", work);
    return true;
  }

  constructor(container: HTMLElement, callbacks: Callbacks, private impactMode = false) {
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
    this.map.addControl(new maplibregl.AttributionControl({ compact: false,
      customAttribution: 'Map data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>' }), "bottom-right");
    this.map.getCanvas().setAttribute("aria-label", "Modeled electrical network map. Use arrow keys to pan and plus or minus to zoom. Asset inspection is also available in the component search.");
    this.resize = new ResizeObserver(() => this.map.resize());
    this.resize.observe(container);
    this.map.on("error", event => {
      const sourceId = (event as maplibregl.ErrorEvent & { sourceId?: string }).sourceId;
      if (sourceId === "street-map" || sourceId === "satellite-map") {
        const active = this.basemap === "satellite" ? "satellite-map" : this.basemap === "plain" ? undefined : "street-map";
        if (sourceId === active) {
          this.basemap = "plain"; this.failedBasemaps.add(sourceId);
          if (this.map.getLayer(sourceId)) this.map.setLayoutProperty(sourceId, "visibility", "none");
          callbacks.basemapError?.();
        }
        return;
      }
      callbacks.error(`Map rendering error: ${event.error.message}`);
    });
    this.map.on("load", () => {
      this.initialize();
      this.ready = true;
      callbacks.ready();
    });
    const pick = (point: maplibregl.Point) => this.map.queryRenderedFeatures(
      [[point.x - 5, point.y - 5], [point.x + 5, point.y + 5]],
      { layers: ["stations", "transformers", "poles", "line-hit",
        ...(!this.impactMode ? ["context-transformers", "context-poles", "context-equipment", "context-line-hit", "context-facilities"] : [])] },
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
      const node = features.find(f => f.source === "nodes" && f.properties.node_id);
      const edge = features.find(f => f.source === "edges" && f.properties.edge_id);
      const foreground = features.find(f => f.source === "nodes" || f.source === "edges");
      if (!foreground) {
        const context = features.find(f => f.source === "infrastructure-context");
        if (context) { callbacks.context?.(context as Asset); return; }
      }
      if (node) callbacks.select({ kind: "node", id: node.properties.node_id });
      else if (edge) callbacks.select({ kind: "edge", id: edge.properties.edge_id });
    });
  }

  private initialize() {
    const map = this.map;
    {
      map.addSource("street-map", { type: "raster", tiles: [streetTiles], tileSize: 256, maxzoom: 19 });
      map.addLayer({ id: "street-map", type: "raster", source: "street-map",
        layout: { visibility: "none" }, paint: { "raster-saturation": -1, "raster-contrast": -.25,
          "raster-opacity": .55 } });
      map.addSource("satellite-map", { type: "raster", tiles: [satelliteTiles], tileSize: 256, maxzoom: 19,
        attribution: 'Imagery © <a href="https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9" target="_blank" rel="noopener noreferrer">Esri</a>, Vantor, Earthstar Geographics, and the GIS User Community' });
      map.addLayer({ id: "satellite-map", type: "raster", source: "satellite-map",
        layout: { visibility: "none" }, paint: { "raster-opacity": 1 } });
    }
    if (!this.impactMode) this.initializeContext();
    for (const id of ["municipalities", "municipality-labels", "barangays", "barangay-labels", "stations", "station-labels", "selected-point"]) {
      map.addSource(id, { type: "geojson", data: emptyCollection() });
    }
    map.addSource("nodes", { type: "geojson", data: emptyCollection(), promoteId: "node_id" });
    map.addSource("edges", { type: "geojson", data: emptyCollection(), promoteId: "edge_id" });
    map.addLayer({ id: "municipality-borders", type: "line", source: "municipalities",
      paint: { "line-width": 1.4, "line-opacity": 0.65 } });
    map.addLayer({ id: "barangay-borders", type: "line", source: "barangays", minzoom: 12,
      layout: { visibility: "none" }, paint: { "line-width": 0.7, "line-opacity": 0.55 } });
    if (this.impactMode) {
      map.addLayer({ id: "impact-boundaries", type: "line", source: "barangays",
        filter: ["literal", false], paint: { "line-width": 2, "line-color": "#c83036", "line-opacity": 0.75 } });
      map.addLayer({ id: "impact-origin", type: "line", source: "barangays",
        filter: ["literal", false], paint: { "line-width": 3, "line-color": "#b25c00", "line-dasharray": [3, 2] } });
    }
    if (this.impactMode) map.addLayer({ id: "impact-halo", type: "line", source: "edges",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#c83036", "line-width": ["interpolate", ["linear"], ["zoom"], 9, 4, 13, 9, 17, 22],
        "line-blur": 4, "line-opacity": ["case", ["==", ["feature-state", "role"], 1], .24, 0] } });
    map.addLayer({ id: "network-lines", type: "line", source: "edges", filter: ["!", lead],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-width": ["interpolate", ["linear"], ["zoom"],
        9, ["match", ["feature-state", "role"], 3, 2, 1, 1.5, 2, 1, .6],
        13, ["match", ["feature-state", "role"], 3, 4, 1, 2.5, 2, 2, 1],
        17, ["match", ["feature-state", "role"], 3, 7, 1, 5.5, 2, 4, 2]] } });
    map.addLayer({ id: "supply-casing", type: "line", source: "edges", filter: lead,
      paint: { "line-width": ["interpolate", ["linear"], ["zoom"], 9, 2, 17, 5] } });
    map.addLayer({ id: "supply-links", type: "line", source: "edges", filter: lead,
      paint: { "line-width": ["interpolate", ["linear"], ["zoom"],
        9, 1, 17, ["match", ["feature-state", "role"], 3, 7, 1, 5.5, 2.5]], "line-dasharray": [2, 2] } });
    map.addLayer({ id: "line-hit", type: "line", source: "edges",
      paint: { "line-width": 12, "line-opacity": 0 } });
    map.addLayer({ id: "poles", type: "circle", source: "nodes", minzoom: 15,
      filter: ["==", ["get", "node_type"], "pole"], paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 15, 1, 17, 2.2],
        "circle-opacity": ["interpolate", ["linear"], ["zoom"], 15, 0, 16, .8], "circle-stroke-width": .5 } });
    map.addLayer({ id: "transformers", type: "circle", source: "nodes",
      filter: ["==", ["get", "node_type"], "transformer"],
      paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"],
        9, ["case", ["==", ["feature-state", "role"], 1], 1.2, .8],
        13, ["case", ["==", ["feature-state", "role"], 1], 2.5, 1.5],
        15, ["case", ["==", ["feature-state", "role"], 1], 4, 2],
        17, ["case", ["==", ["feature-state", "role"], 1], 6, 3]],
        "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 9, .25, 17, 1] } });
    map.addLayer({ id: "selected-point", type: "circle", source: "selected-point",
      paint: { "circle-radius": this.impactMode ? 8 : ["interpolate", ["linear"], ["zoom"], 9, 2, 17, 8], "circle-stroke-width": 2 } });
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

  private initializeContext() {
    const map = this.map;
    map.addSource("infrastructure-context", { type: "geojson", data: emptyCollection(), promoteId: "context_id" });
    map.addLayer({ id: "context-facilities", type: "fill", source: "infrastructure-context",
      filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": "#7f899c", "fill-opacity": .12 } });
    map.addLayer({ id: "context-lines", type: "line", source: "infrastructure-context",
      filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": "#7f899c", "line-opacity": .5,
        "line-width": ["interpolate", ["linear"], ["zoom"], 9, .6, 17, 1.5] } });
    map.addLayer({ id: "context-line-hit", type: "line", source: "infrastructure-context",
      filter: ["==", ["geometry-type"], "LineString"], paint: { "line-width": 10, "line-opacity": 0 } });
    for (const [id, filter, minzoom, radius] of [
      ["context-poles", ["==", ["get", "asset_type"], "pole"], 15, 2.2],
      ["context-transformers", ["==", ["get", "asset_type"], "transformer"], 0, 3],
      ["context-equipment", ["all", ["!", ["has", "edge_id"]], ["!", ["in", ["get", "asset_type"], ["literal", ["pole", "transformer"]]]]], 0, 2.5],
    ] as const) map.addLayer({ id, type: "circle", source: "infrastructure-context", minzoom,
      filter: ["all", ["==", ["geometry-type"], "Point"], filter as unknown as maplibregl.ExpressionSpecification],
      paint: { "circle-color": "#7f899c", "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, .8, 17, radius],
        "circle-opacity": minzoom ? ["interpolate", ["linear"], ["zoom"], 15, 0, 16, .65] : .65 } });
  }

  setInfrastructureContext(data: Assets) {
    if (this.deferUntilReady(() => this.setInfrastructureContext(data))) return;
    if (this.impactMode) return;
    this.contextData = data;
    this.source("infrastructure-context", data);
  }

  clearSelection() {
    if (this.deferUntilReady(() => this.clearSelection())) return;
    for (const source of ["nodes", "edges"]) this.map.removeFeatureState({ source });
    this.nodeRoles.clear(); this.edgeRoles.clear(); this.upstreamIds = [];
    this.hasSelection = false;
    this.source("selected-point", emptyCollection());
    this.filters(); this.setTheme();
  }

  private stationIcons() {
    const palette = colors();
    for (const active of [true, false]) {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 28;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = palette.chart.background;
      ctx.fillRect(0, 0, 28, 28);
      ctx.fillStyle = !this.impactMode && !this.hasSelection ? palette.remaining : palette.station;
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
    if (this.impactMode) {
      const css = getComputedStyle(document.documentElement);
      c.selected = css.getPropertyValue("--impact-fault").trim() || "#b91c1c";
      c.downstream = css.getPropertyValue("--impact-loss").trim() || "#c83036";
      c.station = css.getPropertyValue("--impact-upstream").trim() || "#0072b2";
    } else c.downstream = c.selected;
    if (!map.getLayer("network-lines")) return;
    {
      const dark = document.documentElement.classList.contains("dark");
      const color = this.basemap === "color";
      map.setPaintProperty("street-map", "raster-saturation", color ? 0 : -1);
      map.setPaintProperty("street-map", "raster-contrast", color ? 0 : -.25);
      map.setPaintProperty("street-map", "raster-brightness-max", color ? 1 : dark ? .28 : 1);
      map.setPaintProperty("street-map", "raster-opacity", color ? 1 : dark ? .65 : .55);
    }
    map.setPaintProperty("background", "background-color", c.map.background);
    for (const id of ["municipality-borders", "barangay-borders"]) map.setPaintProperty(id, "line-color", c.boundary);
    map.setPaintProperty("network-lines", "line-color", ["match", ["feature-state", "role"],
      3, c.selected, 1, c.downstream, 2, c.station, c.remaining]);
    map.setPaintProperty("supply-casing", "line-color", c.chart.background);
    map.setPaintProperty("supply-links", "line-color", ["match", ["feature-state", "role"],
      3, c.selected, 1, c.downstream, 2, c.station, c.remaining]);
    map.setPaintProperty("poles", "circle-color", this.impactMode
      ? ["case", ["==", ["feature-state", "role"], 1], c.downstream, c.remaining]
      : ["match", ["feature-state", "role"], 3, c.selected, 1, c.downstream, 2, c.station, c.remaining]);
    map.setPaintProperty("transformers", "circle-color", ["match", ["feature-state", "role"],
      3, c.selected, 1, c.downstream, 2, c.station, c.remaining]);
    map.setPaintProperty("transformers", "circle-stroke-color", c.chart.background);
    map.setPaintProperty("selected-point", "circle-color", c.selected);
    map.setPaintProperty("selected-point", "circle-stroke-color", c.chart.background);
    this.stationIcons();
    if (!this.impactMode) {
      for (const id of ["context-lines"]) map.setPaintProperty(id, "line-color", c.remaining);
      map.setPaintProperty("context-facilities", "fill-color", c.remaining);
      for (const id of ["context-poles", "context-transformers", "context-equipment"]) map.setPaintProperty(id, "circle-color", c.remaining);
      map.setPaintProperty("poles", "circle-stroke-color", c.chart.background);
      for (const id of ["network-lines", "supply-links"]) map.setPaintProperty(id, "line-opacity",
        ["case", ["in", ["feature-state", "role"], ["literal", [1, 2, 3]]], 1, .5]);
    }
    if (this.impactMode) {
      map.setPaintProperty("impact-halo", "line-color", c.downstream);
      map.setPaintProperty("poles", "circle-stroke-color", "#ffffff");
      map.setPaintProperty("impact-boundaries", "line-color", c.downstream);
      map.setPaintProperty("impact-origin", "line-color", c.selected);
    }
    for (const [key, text] of this.labels) this.labelImage(key, text);
  }

  clearNetwork() {
    if (this.impactMode) { this.stopImpactMotion(); this.impactShown = false; ++this.impactPreparation; }
    this.originMarker?.remove(); this.originMarker = undefined;
    this.network = undefined;
    this.hasSelection = false;
    this.nodeRoles.clear(); this.edgeRoles.clear(); this.upstreamIds = [];
    if (!this.ready) return;
    for (const source of ["nodes", "edges"]) {
      this.map.removeFeatureState({ source });
      this.source(source, emptyCollection());
    }
    this.source("selected-point", emptyCollection());
    if (!this.impactMode) this.setTheme();
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

  /** Retain existing source tiles while MapLibre prepares a different feeder.
   * Same-feeder changes only touch feature state, never upload GeoJSON again. */
  async waitForImpactMap(signal: AbortSignal) {
    if (signal.aborted || this.disposed) throw new DOMException("Selection changed", "AbortError");
    if (this.ready) return;
    await new Promise<void>((resolve, reject) => {
      const cleanup = () => { this.map.off("load", loaded); signal.removeEventListener("abort", aborted); };
      const loaded = () => { cleanup(); resolve(); };
      const aborted = () => { cleanup(); reject(new DOMException("Selection changed", "AbortError")); };
      this.map.once("load", loaded); signal.addEventListener("abort", aborted, { once: true });
    });
  }

  async prepareImpactNetwork(next: LoadedNetwork, signal: AbortSignal): Promise<boolean> {
    await this.waitForImpactMap(signal);
    if (signal.aborted) throw new DOMException("Selection changed", "AbortError");
    if (!this.impactMode) { this.setNetwork(next); return false; }
    // Camera movement may still be loading tiles from the SAME GeoJSON source.
    // That never requires re-uploading or rebuilding the feeder itself.
    if (this.network === next) return true;
    const previous = this.network, token = ++this.impactPreparation;
    const lines = (network?: LoadedNetwork): Assets => ({ type: "FeatureCollection",
      features: network?.edges.features.filter(f => f.geometry.type === "LineString") ?? [] });
    try {
      await new Promise<void>((resolve, reject) => {
        const cleanup = () => {
          clearTimeout(timeout); this.map.off("sourcedata", changed); this.map.off("error", failed);
          signal.removeEventListener("abort", aborted);
        };
        const finish = (error?: Error) => { cleanup(); if (error) reject(error); else resolve(); };
        const changed = () => queueMicrotask(() => {
          if (this.disposed || signal.aborted || token !== this.impactPreparation) return;
          if (this.map.isSourceLoaded("nodes") && this.map.isSourceLoaded("edges")) finish();
        });
        const aborted = () => finish(new DOMException("Selection changed", "AbortError"));
        const failed = (event: maplibregl.ErrorEvent) => {
          if (["nodes", "edges"].includes((event as maplibregl.ErrorEvent & { sourceId?: string }).sourceId ?? "")) finish(new Error(event.error.message));
        };
        const timeout = setTimeout(() => finish(new Error("The next feeder could not finish rendering. Retry this outage.")), 30000);
        this.map.on("sourcedata", changed); this.map.on("error", failed);
        signal.addEventListener("abort", aborted, { once: true });
        this.source("nodes", next.nodes); this.source("edges", lines(next));
        changed();
      });
      if (signal.aborted || token !== this.impactPreparation) throw new DOMException("Selection changed", "AbortError");
      this.network = next;
      return false;
    } catch (error) {
      if (!this.disposed && token === this.impactPreparation) {
        this.source("nodes", previous?.nodes ?? emptyCollection()); this.source("edges", lines(previous));
      }
      throw error;
    }
  }

  setImpactGeography(data: Assets, codes: string[], origin: string) {
    const key = data.features.map(f => String(f.properties.barangay_psgc)).sort().join(",");
    if (key !== this.impactBoundaryKey) {
      this.source("barangays", data); this.impactBoundaryKey = key;
    }
    this.setImpactBoundaries(codes, origin);
  }

  stopImpactMotion() {
    if (!this.impactMode) return;
    this.map.stop();
    if (this.impactFadeFrame !== undefined) cancelAnimationFrame(this.impactFadeFrame);
    this.impactFadeFrame = undefined; this.impactFade = 1;
    if (this.impactVisibility) this.paintImpactOpacity(this.impactVisibility);
  }

  fadeInImpact() {
    this.stopImpactMotion();
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const start = performance.now();
    const tick = (now: number) => {
      this.impactFade = Math.min(1, (now - start) / 200);
      if (this.impactVisibility) this.paintImpactOpacity(this.impactVisibility);
      this.impactFadeFrame = this.impactFade < 1 ? requestAnimationFrame(tick) : undefined;
    };
    this.impactFade = 0; this.impactFadeFrame = requestAnimationFrame(tick);
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
    this.hasSelection = true;
    const nodeRoles = new Map<string, number>(), edgeRoles = new Map<string, number>();
    for (const id of trace.nodeIds) {
      nodeRoles.set(id, 1);
    }
    if (this.impactMode) for (const id of trace.upstreamNodeIds ?? []) if (!nodeRoles.has(id)) nodeRoles.set(id, 2);
    // Only rendered features need state; poles have a fixed style and logical
    // Point edges live exclusively in the graph. This keeps interactions lean.
    for (const id of trace.upstreamEdgeIds) {
      if (this.network.graph.edges.get(id)?.geometry.type === "LineString") edgeRoles.set(id, 2);
      if (!this.impactMode) for (const key of ["from_node_id", "to_node_id"]) {
        const nodeId = String(this.network.graph.edges.get(id)?.properties[key]);
        if (!nodeRoles.has(nodeId)) nodeRoles.set(nodeId, 2);
      }
    }
    for (const id of trace.edgeIds) {
      if (this.network.graph.edges.get(id)?.geometry.type === "LineString") edgeRoles.set(id, 1);
    }
    if (!this.impactMode) (selection.kind === "node" ? nodeRoles : edgeRoles).set(selection.id, 3);
    const apply = (source: string, next: Map<string, number>, previous: Map<string, number>) => {
      for (const id of previous.keys()) if (!next.has(id)) this.map.removeFeatureState({ source, id });
      for (const [id, role] of next) if (role !== previous.get(id)) this.map.setFeatureState({ source, id }, { role });
    };
    apply("nodes", nodeRoles, this.nodeRoles); apply("edges", edgeRoles, this.edgeRoles);
    this.nodeRoles = nodeRoles; this.edgeRoles = edgeRoles;
    this.upstreamIds = [...trace.upstreamEdgeIds];
    this.filters();
    const selected = selection.kind === "node" ? this.network.graph.nodes.get(selection.id) : this.network.graph.edges.get(selection.id);
    this.source("selected-point", { type: "FeatureCollection", features: !this.impactMode && selected?.geometry.type === "Point" ? [selected] : [] });
    if (this.impactMode && selected) {
      this.originMarker?.remove(); this.originMarker = undefined;
      const sourceNode = selection.kind === "edge" ? this.network.graph.nodes.get(String(selected.properties.from_node_id)) : selected;
      const point = outageOriginPoint(selected, sourceNode);
      if (point) {
        const element = document.createElement("div");
        element.className = "outage-origin-marker";
        element.setAttribute("role", "img");
        element.setAttribute("aria-label", "Approximate simulated outage origin");
        element.hidden = !this.originVisible;
        for (let i = 0; i < 3; i++) {
          const ring = document.createElement("span");
          ring.className = "outage-origin-ring";
          ring.style.animationDelay = `${i * .8}s`;
          element.append(ring);
        }
        const dot = document.createElement("span"); dot.className = "outage-origin-dot";
        element.append(dot);
        this.originMarker = new maplibregl.Marker({ element, anchor: "center" }).setLngLat(point).addTo(this.map);
      }
    }
    if (!this.impactMode) this.setTheme();
  }

  inspectStation(station: Station) {
    if (this.deferUntilReady(() => this.inspectStation(station))) return;
    this.source("selected-point", { type: "FeatureCollection", features: [station] });
  }

  showPoles(show: boolean) {
    if (this.deferUntilReady(() => this.showPoles(show))) return;
    this.map.setLayerZoomRange("poles", 15, 24);
    if (!this.impactMode) for (const id of ["poles", "context-poles"]) this.map.setLayoutProperty(id, "visibility", show ? "visible" : "none");
  }
  setBasemap(background: MapBackground) {
    if (this.deferUntilReady(() => this.setBasemap(background))) return;
    this.basemap = background;
    const active = background === "satellite" ? "satellite-map" : background === "plain" ? undefined : "street-map";
    for (const id of ["street-map", "satellite-map"]) {
      if (id === active && this.failedBasemaps.has(id)) {
        this.failedBasemaps.delete(id);
        (this.map.getSource(id) as maplibregl.RasterTileSource).setTiles([id === "satellite-map" ? satelliteTiles : streetTiles]);
      }
      this.map.setLayoutProperty(id, "visibility", id === active ? "visible" : "none");
    }
    this.setTheme();
  }
  showBarangays(show: boolean) {
    if (this.deferUntilReady(() => this.showBarangays(show))) return;
    for (const id of ["barangay-borders", "barangay-labels"]) this.map.setLayoutProperty(id, "visibility", show ? "visible" : "none");
  }
  setImpactBoundaries(codes: string[], origin: string) {
    if (this.deferUntilReady(() => this.setImpactBoundaries(codes, origin))) return;
    if (!this.impactMode) return;
    this.map.setFilter("impact-boundaries", ["in", ["get", "barangay_psgc"], ["literal", codes]]);
    this.map.setFilter("impact-origin", ["==", ["get", "barangay_psgc"], origin]);
  }
  setImpactLayers(visible: { fault: boolean; loss: boolean; upstream: boolean; unaffected: boolean; poles: boolean; transformers: boolean; boundaries: boolean; halo?: boolean }) {
    if (this.deferUntilReady(() => this.setImpactLayers(visible))) return;
    if (!this.impactMode) return;
    this.impactVisibility = visible;
    this.map.setLayoutProperty("impact-halo", "visibility", visible.loss && visible.halo !== false ? "visible" : "none");
    this.originVisible = visible.fault;
    if (this.originMarker) this.originMarker.getElement().hidden = !visible.fault;
    this.paintImpactOpacity(visible);
    this.map.setLayoutProperty("selected-point", "visibility", visible.fault ? "visible" : "none");
    this.map.setLayoutProperty("poles", "visibility", visible.poles ? "visible" : "none");
    this.map.setLayoutProperty("transformers", "visibility", visible.transformers ? "visible" : "none");
    for (const id of ["impact-origin", "impact-boundaries"]) this.map.setLayoutProperty(id, "visibility", visible.boundaries ? "visible" : "none");
  }
  private paintImpactOpacity(visible: NonNullable<TopologyMap["impactVisibility"]>) {
    const f = this.impactFade;
    const opacity: maplibregl.ExpressionSpecification = ["match", ["feature-state", "role"],
      3, visible.fault ? f : 0, 1, visible.loss ? f : 0, 2, visible.upstream ? f : 0, visible.unaffected ? .28 * f : 0];
    for (const id of ["network-lines", "supply-links", "supply-casing"]) this.map.setPaintProperty(id, "line-opacity", opacity);
    for (const id of ["poles", "transformers"]) this.map.setPaintProperty(id, "circle-opacity", opacity);
    this.map.setPaintProperty("poles", "circle-opacity", ["interpolate", ["linear"], ["zoom"], 15, 0, 16,
      ["match", ["feature-state", "role"], 1, visible.loss ? f : 0, 2, visible.upstream ? .85 * f : 0, visible.unaffected ? .7 * f : 0]]);
    this.map.setPaintProperty("poles", "circle-stroke-opacity", ["interpolate", ["linear"], ["zoom"], 15, 0, 16,
      ["match", ["feature-state", "role"], 1, visible.loss ? f : 0, 2, visible.upstream ? .7 * f : 0, visible.unaffected ? .7 * f : 0]]);
    this.map.setPaintProperty("impact-halo", "line-opacity", ["case", ["==", ["feature-state", "role"], 1], .24 * f, 0]);
  }
  fitImpact(options: { automatic?: boolean } = {}) {
    if (this.network) {
      const nodes = [...this.nodeRoles].filter(([, role]) => role === 1 || role === 3)
        .map(([id]) => this.network!.graph.nodes.get(id)!).filter(Boolean);
      const bounds = boundsFor(nodes);
      if (this.originMarker) bounds.extend(this.originMarker.getLngLat());
      this.moveImpactBounds(bounds, options.automatic ?? false);
    }
  }
  private moveImpactBounds(bounds: maplibregl.LngLatBounds, automatic: boolean) {
    if (bounds.isEmpty()) return;
    const sw = this.map.project(bounds.getSouthWest()), ne = this.map.project(bounds.getNorthEast());
    const container = this.map.getContainer();
    const fits = sw.x >= 55 && ne.x <= container.clientWidth - 55 && ne.y >= 55 && sw.y <= container.clientHeight - 55;
    if (automatic && this.impactShown && fits) return;
    const camera = this.map.cameraForBounds(bounds, { padding: 45, maxZoom: 17 });
    const far = this.map.getCenter().distanceTo(bounds.getCenter()) > this.map.getBounds().getSouthWest().distanceTo(this.map.getBounds().getNorthEast())
      || Math.abs((camera?.zoom ?? this.map.getZoom()) - this.map.getZoom()) > 2;
    this.map.stop();
    this.map.fitBounds(bounds, { padding: 45, maxZoom: 17, linear: true,
      duration: matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : far ? 1100 : 800,
      easing: t => t * t * (3 - 2 * t) });
    this.impactShown = true;
  }
  fitBoundaryFeatures(data: Assets, impactTransition = false) {
    if (this.deferUntilReady(() => this.fitBoundaryFeatures(data, impactTransition))) return;
    const bounds = new maplibregl.LngLatBounds();
    const expand = (value: unknown) => {
      if (!Array.isArray(value)) return;
      if (typeof value[0] === "number" && typeof value[1] === "number") bounds.extend([value[0], value[1]]);
      else for (const child of value) expand(child);
    };
    for (const feature of data.features) if ("coordinates" in feature.geometry) expand(feature.geometry.coordinates);
    if (impactTransition && this.impactMode) this.moveImpactBounds(bounds, true); else this.fit(bounds);
  }
  fitAll() {
    if (!this.impactMode && this.contextData) { this.fitBoundaryFeatures(this.contextData); return; }
    if (this.metadata) this.fit(boundsFor(this.metadata.substations.features));
  }
  fitNetwork() {
    if (!this.network) { this.fitAll(); return; }
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
  destroy() { this.disposed = true; ++this.impactPreparation; if (this.impactFadeFrame !== undefined) cancelAnimationFrame(this.impactFadeFrame); this.originMarker?.remove(); this.resize.disconnect(); this.map.remove(); }
}
