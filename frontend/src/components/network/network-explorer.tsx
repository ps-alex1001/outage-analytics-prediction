"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "next-themes";
import { ChevronDown, ChevronUp, Focus, LoaderCircle, RotateCcw } from "lucide-react";
import {
  assetName, loadBoundaries, loadMetadata, loadNetwork, loadInfrastructureContext, stationName, traceAssetInspection,
  type Asset, type LoadedNetwork, type Metadata, type Selection, type Station,
} from "@/lib/topology";
import { TopologyMap, type MapBackground } from "@/lib/topology-map";

const control = "min-h-11 rounded-md border bg-background px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-50";
const button = `${control} inline-flex items-center justify-center gap-2 hover:bg-muted disabled:cursor-not-allowed`;
type Inspection = { asset: Asset; selection?: Selection; downstream?: number; connectionPoleId?: string };

export default function NetworkExplorer() {
  const container = useRef<HTMLDivElement>(null);
  const controls = useRef<HTMLElement>(null);
  const inspector = useRef<HTMLElement>(null);
  const map = useRef<TopologyMap | null>(null);
  const network = useRef<LoadedNetwork | null>(null);
  const metadataRef = useRef<Metadata | null>(null);
  const stationRef = useRef("");
  const feederRef = useRef("");
  const pendingInspection = useRef<Asset | null>(null);
  const [ready, setReady] = useState(false);
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  const [stationId, setStationId] = useState("");
  const [feederId, setFeederId] = useState("");
  const [busy, setBusy] = useState(true);
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [boundaryError, setBoundaryError] = useState<string | null>(null);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [basemap, setBasemap] = useState<MapBackground>("street");
  const [basemapError, setBasemapError] = useState(false);
  const [controlsOpen, setControlsOpen] = useState(true);
  const { resolvedTheme } = useTheme();

  const inspect = useCallback((selection: Selection, focus = false) => {
    const current = network.current;
    if (!current) return;
    const asset = selection.kind === "node" ? current.graph.nodes.get(selection.id) : current.graph.edges.get(selection.id);
    if (!asset) return;
    try {
      const trace = traceAssetInspection(current.graph, selection);
      map.current?.setSelection(selection, trace);
      setInspection({ asset, selection, downstream: trace.transformers, connectionPoleId: trace.connectionPoleId });
      if (focus) map.current?.focusAsset(asset);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to inspect component");
    }
  }, []);

  const clearAll = useCallback(() => {
    stationRef.current = ""; feederRef.current = ""; network.current = null; pendingInspection.current = null;
    map.current?.clearNetwork(); map.current?.setFeeder("");
    setStationId(""); setFeederId(""); setInspection(null); setBusy(false); setError(null);
  }, []);

  const chooseStation = useCallback((id: string) => {
    if (!id) { clearAll(); return; }
    if (id === stationRef.current) return;
    stationRef.current = id;
    feederRef.current = "";
    network.current = null;
    map.current?.clearNetwork();
    setStationId(id); setFeederId(""); setInspection(null);
    if ((container.current?.clientWidth ?? innerWidth) < 900) setControlsOpen(false);
    setBusy(true); setError(null);
  }, [clearAll]);

  const inspectStation = useCallback((station: Station) => {
    const id = station.properties.modeled_source_node_id;
    if (id && station.properties.active_feeder_count > 0 && id !== stationRef.current) chooseStation(id);
    setInspection({ asset: station });
    map.current?.inspectStation(station);
  }, [chooseStation]);

  const inspectContext = useCallback((asset: Asset) => {
    const p = asset.properties;
    const selection: Selection = { kind: p.node_id ? "node" : "edge", id: String(p.node_id ?? p.edge_id) };
    const station = p.source_node_id;
    if ((p.node_id || p.edge_id) && typeof station === "string" && metadataRef.current?.substations.features.some(s => s.properties.modeled_source_node_id === station)) {
      if (station === stationRef.current && network.current) {
        const found = selection.kind === "node" ? network.current.graph.nodes.has(selection.id) : network.current.graph.edges.has(selection.id);
        if (found) { inspect(selection); return; }
      } else {
        chooseStation(station); pendingInspection.current = asset; return;
      }
    }
    map.current?.clearSelection(); setInspection({ asset });
  }, [chooseStation, inspect]);

  useEffect(() => {
    if (!container.current) return;
    let viewer: TopologyMap;
    try {
      viewer = new TopologyMap(container.current, {
        ready: () => setReady(true), select: inspect, station: inspectStation, error: setMapError,
        context: inspectContext,
        basemapError: () => { setBasemap("plain"); setBasemapError(true); },
      });
      map.current = viewer;
    } catch (cause) {
      // Constructor failures (for example disabled WebGL) still leave the page usable.
      queueMicrotask(() => setMapError(cause instanceof Error ? cause.message : "Map initialization failed"));
      return;
    }
    return () => { viewer.destroy(); map.current = null; };
  }, [inspect, inspectStation, inspectContext]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    loadMetadata().then(async data => {
      if (cancelled) return;
      metadataRef.current = data;
      setMetadata(data);
      map.current?.setMetadata(data);
      const context = await loadInfrastructureContext();
      if (cancelled) return;
      map.current?.setInfrastructureContext(context.infrastructure);
      if (!stationRef.current) setBusy(false);
      // Geographic context is independent of network geometry and can load concurrently.
      return loadBoundaries("municipality").then(boundaries => {
        if (!cancelled) { map.current?.setBoundaries("municipality", boundaries); setBoundaryError(null); }
      }).catch(() => { if (!cancelled) setBoundaryError("Municipality context could not load. Retry to restore borders and labels."); });
    }).catch(cause => { if (!cancelled) { setError(String(cause.message)); setBusy(false); } });
    return () => { cancelled = true; };
  }, [ready, retry, chooseStation]);

  useEffect(() => {
    if (!ready || !stationId) return;
    let cancelled = false;
    loadNetwork(stationId).then(data => {
      if (cancelled) return;
      network.current = data;
      map.current?.setNetwork(data);
      map.current?.setFeeder(feederRef.current);
      const feeder = metadataRef.current?.feeders.find(f => f.feeder_id === feederRef.current);
      const pending = pendingInspection.current; pendingInspection.current = null;
      if (pending) {
        const p = pending.properties, selection: Selection = { kind: p.node_id ? "node" : "edge", id: String(p.node_id ?? p.edge_id) };
        const found = selection.kind === "node" ? data.graph.nodes.has(selection.id) : data.graph.edges.has(selection.id);
        if (found) inspect(selection); else setInspection({ asset: pending });
      } else inspect({ kind: "node", id: feeder?.root_node_id ?? stationId });
      setBusy(false); setError(null);
    }).catch(cause => { if (!cancelled) { setBusy(false); setError(String(cause.message)); } });
    return () => { cancelled = true; };
  }, [ready, stationId, retry, inspect]);

  useEffect(() => {
    if (!ready) return;
    // Read CSS after next-themes has applied the root class/attribute.
    const frame = requestAnimationFrame(() => map.current?.setTheme());
    return () => cancelAnimationFrame(frame);
  }, [ready, resolvedTheme]);

  useEffect(() => { if (ready) map.current?.setBasemap(basemap); }, [ready, basemap]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    map.current?.showPoles(true);
    map.current?.showBarangays(true);
    loadBoundaries("barangay").then(data => {
      if (!cancelled) map.current?.setBoundaries("barangay", data);
    }).catch(() => { if (!cancelled) setBoundaryError("Barangay context could not load. Retry to restore borders and labels."); });
    return () => { cancelled = true; };
  }, [ready, retry]);

  const updateFitPadding = useCallback(() => {
    if (!container.current || !controls.current) return;
    const viewport = container.current.getBoundingClientRect();
    const left = controls.current.getBoundingClientRect();
    const right = inspector.current?.getBoundingClientRect();
    const compact = viewport.width < 900;
    map.current?.setFitPadding(compact ? {
      top: Math.min(left.height + 32, viewport.height * .35),
      bottom: right ? Math.min(right.height + 60, viewport.height * .45) : 48,
      left: 24, right: 24,
    } : {
      top: 48, bottom: 48,
      left: Math.min(left.width + 40, viewport.width * .3),
      right: right ? Math.min(right.width + 48, viewport.width * .3) : 72,
    });
  }, []);

  useEffect(() => {
    if (!ready || !container.current || !controls.current) return;
    const observer = new ResizeObserver(updateFitPadding);
    observer.observe(container.current);
    observer.observe(controls.current);
    if (inspector.current) observer.observe(inspector.current);
    updateFitPadding();
    return () => observer.disconnect();
  }, [ready, stationId, controlsOpen, updateFitPadding]);

  // Fit after React has committed the inspection panel, including cached loads.
  useEffect(() => {
    if (!ready || busy) return;
    updateFitPadding();
    if (stationId) map.current?.fitNetwork(); else map.current?.fitAll();
  }, [ready, busy, stationId, updateFitPadding]);

  const stations = useMemo(() => metadata?.substations.features
    .filter(f => f.properties.active_feeder_count > 0)
    .sort((a, b) => stationName(a).localeCompare(stationName(b))) ?? [], [metadata]);
  const feeders = metadata?.feeders.filter(f => f.substation_node_id === stationId) ?? [];
  const activeStation = stations.find(f => f.properties.modeled_source_node_id === stationId);
  const activeFeeder = feeders.find(f => f.feeder_id === feederId);

  function chooseFeeder(id: string) {
    feederRef.current = id; setFeederId(id);
    map.current?.setFeeder(id);
    inspect({ kind: "node", id: feeders.find(f => f.feeder_id === id)?.root_node_id ?? stationId });
    map.current?.fitNetwork();
  }

  function resetSelection() {
    inspect({ kind: "node", id: activeFeeder?.root_node_id ?? stationId });
    map.current?.fitNetwork();
  }

  const props = inspection?.asset.properties;
  const isStation = Boolean(props && "active_feeder_count" in props);
  const fields = props ? [
    ["Name", props.name],
    ["Data origin", props.provenance],
    ["Operator", props.operator],
    ["Recorded voltage", props.voltage ?? props.voltage_levels_v],
    ["Highlighted branch origin pole", inspection?.connectionPoleId],
    ["Modeled feeder ID", props.feeder_id],
    ["Location", [props.barangay, props.municipality].filter(Boolean).join(", ")],
    ["Connection status", props.connection_status],
    ["Geometry basis", props.geometry_basis],
    ["Data status", props.data_status],
    ["Verified connectivity", props.is_verified_connectivity === true ? "Yes" : props.is_verified_connectivity === false ? "No" : "Not specified in export"],
  ].filter(([, value]) => value !== null && value !== undefined && value !== "") : [];

  return (
    <div className="network-map network-workspace relative min-h-0 flex-1 overflow-hidden" aria-busy={busy}>
      <div ref={container} className="h-full w-full" />

      <section ref={controls} aria-label="Network controls" className="network-controls absolute left-3 top-3 z-10 w-[min(320px,calc(100%-76px))] overflow-hidden rounded-xl border bg-card shadow-lg md:left-4 md:top-4">
        <div className="flex items-center justify-between gap-2 px-4 py-2">
          <div className="min-w-0">
            <h2 className="text-base font-semibold tracking-tight">Network controls</h2>
            <p className="text-xs text-muted-foreground">Observed + modeled infrastructure</p>
          </div>
          <button type="button" className="inline-flex size-11 shrink-0 items-center justify-center rounded-md hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring" aria-label={controlsOpen ? "Collapse network controls" : "Expand network controls"} aria-expanded={controlsOpen} aria-controls="network-controls-body" onClick={() => setControlsOpen(value => !value)}>
            {controlsOpen ? <ChevronUp size={18} aria-hidden="true" /> : <ChevronDown size={18} aria-hidden="true" />}
          </button>
        </div>
        <div id="network-controls-body" hidden={!controlsOpen} className="max-h-[calc(100dvh-210px)] space-y-3 overflow-y-auto border-t p-4">
          <label className="grid min-w-0 gap-1.5 text-xs font-medium">
            Substation
            <select aria-label="Substation" className={`${control} w-full min-w-0`} value={stationId}
              disabled={!metadata} onChange={event => chooseStation(event.target.value)}>
              <option value="">{metadata ? "All infrastructure" : "Loading substations…"}</option>
              {stations.map(station => <option key={station.properties.asset_id} value={station.properties.modeled_source_node_id!}>
                {stationName(station)}
              </option>)}
            </select>
          </label>
          <label className="grid min-w-0 gap-1.5 text-xs font-medium">
            Modeled feeder
            <select aria-label="Modeled feeder" className={`${control} w-full min-w-0`} value={feederId} disabled={busy || !stationId || !!error}
              onChange={event => chooseFeeder(event.target.value)}>
              <option value="">All modeled feeders</option>
              {feeders.map(feeder => <option key={feeder.feeder_id} value={feeder.feeder_id}>
                Modeled feeder {feeder.modeled_feeder_number} · {feeder.transformer_count.toLocaleString()} transformers · {feeder.barangay}
              </option>)}
            </select>
          </label>
          {metadata && <p className="text-xs leading-5 text-muted-foreground">
            {activeStation ? `${Number(activeFeeder?.transformer_count ?? activeStation.properties.transformer_count).toLocaleString()} transformers in this view` : `${stations.length} substations · ${metadata.dataset.counts.active_feeders} modeled feeders`}
          </p>}
          <div className="grid grid-cols-2 gap-2 border-t pt-3" aria-label="Map actions">
            <button type="button" className={button} disabled={busy || !!error} onClick={() => map.current?.fitNetwork()}><Focus size={15} aria-hidden="true" /> Fit network</button>
            <button type="button" className={button} disabled={!metadata} onClick={clearAll}>Clear selection / Show all</button>
            {stationId && <button type="button" className={`${button} col-span-2`} disabled={busy || !!error} onClick={resetSelection}>
              <RotateCcw size={15} aria-hidden="true" /> Show whole {feederId ? "feeder" : "substation"}
            </button>}
          </div>
          <label className="grid gap-1.5 border-t pt-3 text-xs font-medium">Background
            <select aria-label="Network map background" className={`${control} w-full`} value={basemap} onChange={event => { setBasemap(event.target.value as MapBackground); setBasemapError(false); }}>
              <option value="street">Street map (muted)</option><option value="color">Color street map</option><option value="satellite">Satellite imagery</option><option value="plain">Plain background</option>
            </select>
          </label>
          {basemapError && <p role="status" className="text-xs leading-5">Background unavailable. Showing plain background; choose a map background to retry.</p>}
          <details className="border-t pt-1">
            <summary className="flex min-h-11 cursor-pointer items-center text-xs font-medium">Map legend</summary>
            <div className="grid gap-2 pb-1 text-xs" aria-label="Map legend">
              <Legend shape="square" color="station">Mapped station</Legend>
              <Legend shape="line" color="selected">Downstream</Legend>
              <Legend shape="dash" color="station">Inferred station link</Legend>
              <Legend shape="line" color="station">Upstream path</Legend>
              <Legend shape="dot" color="selected">Selected</Legend>
              <Legend shape="line" color="remaining">Observed and generated context</Legend>
            </div>
          </details>
        </div>
        {busy && !error && <p role="status" className="flex items-center gap-2 border-t px-4 py-3 text-xs">
          <LoaderCircle size={15} className="shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          {metadata ? stationId ? "Loading selected network…" : "Loading all infrastructure…" : "Loading substations…"}
        </p>}
        {(error || boundaryError) && <div role="alert" className="space-y-2 border-t p-3 text-xs leading-5">
          <p>{error || boundaryError}</p>
          <button className={`${button} w-full`} onClick={() => { setBusy(true); setError(null); setBoundaryError(null); setRetry(value => value + 1); }}>Retry loading</button>
        </div>}
      </section>

      {stationId && <aside ref={inspector} aria-label="Asset inspection" className="network-inspector absolute bottom-11 left-3 right-[68px] z-10 max-h-[35%] overflow-y-auto rounded-xl border bg-card p-4 shadow-lg md:bottom-auto md:left-auto md:right-4 md:top-4 md:max-h-[calc(100%-4rem)] md:w-[300px]">
        <h2 className="text-sm font-semibold">Asset inspection</h2>
        <div className="mt-3" aria-live="polite">
          {inspection ? <>
            <p className="break-words text-sm font-medium">{activeStation ? stationName(activeStation) : "Selected substation"}</p>
            {activeFeeder && <p className="mt-1 text-sm text-muted-foreground">Feeder line {activeFeeder.modeled_feeder_number}</p>}
            {!isStation && inspection.selection?.id !== stationId && inspection.selection?.id !== activeFeeder?.root_node_id && <p className="mt-2 break-words text-xs capitalize text-muted-foreground">{assetName(inspection.asset)}</p>}
            {inspection.connectionPoleId && <p className="mt-2 text-xs leading-5 text-muted-foreground">
              Orange shows the downstream branch from this transformer’s connection pole. Blue shows its upstream supply path.
            </p>}
            {inspection.downstream !== undefined && <div className="my-3 border-y py-3">
              <p className="text-2xl font-semibold tabular-nums">{inspection.downstream.toLocaleString()}</p>
              <p className="text-xs text-muted-foreground">{inspection.connectionPoleId ? "Transformers in the connection pole’s downstream branch" : "Transformers downstream in the stored tree"}</p>
            </div>}
          </> : <p className="text-sm leading-6 text-muted-foreground">{busy ? "Loading selected substation…" : "Click a line, pole, or transformer to inspect its attributes."}</p>}
        </div>
        <dl className="mt-3 space-y-3 text-xs">
          {fields.map(([label, value]) => <div key={String(label)}>
            <dt className="text-muted-foreground">{label}</dt><dd className="mt-1 break-words [overflow-wrap:anywhere]">{String(value)}</dd>
          </div>)}
        </dl>
      </aside>}
      {mapError && <p role="alert" className="absolute inset-x-3 bottom-10 z-20 rounded-md border bg-card p-3 text-sm">{mapError}</p>}
    </div>
  );
}

function Legend({ shape, color, children }: { shape: "square" | "dot" | "line" | "dash"; color: string; children: React.ReactNode }) {
  const line = shape === "line" || shape === "dash";
  return <span className="inline-flex items-center gap-1.5"><span aria-hidden="true"
    className={line ? "w-5 border-t-[3px]" : `size-3 border-2 ${shape === "dot" ? "rounded-full" : ""}`}
    style={{ borderColor: `var(--network-${color})`, borderStyle: shape === "dash" ? "dashed" : "solid", backgroundColor: !line ? `var(--network-${color})` : undefined }} />{children}</span>;
}
