"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "next-themes";
import { Focus, LoaderCircle, RotateCcw, Search } from "lucide-react";
import { useColorblindMode } from "@/components/theme-provider";
import {
  assetName, loadBoundaries, loadMetadata, loadNetwork, stationName, traceAssetInspection,
  type Asset, type LoadedNetwork, type Metadata, type Selection, type Station,
} from "@/lib/topology";
import { TopologyMap } from "@/lib/topology-map";

const control = "min-h-11 rounded-md border bg-background px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-50";
const button = `${control} inline-flex items-center justify-center gap-2 hover:bg-muted disabled:cursor-not-allowed`;
type Inspection = { asset: Asset; selection?: Selection; downstream?: number; connectionPoleId?: string };

export default function NetworkExplorer() {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<TopologyMap | null>(null);
  const network = useRef<LoadedNetwork | null>(null);
  const metadataRef = useRef<Metadata | null>(null);
  const stationRef = useRef("");
  const feederRef = useRef("");
  const [ready, setReady] = useState(false);
  const [metadata, setMetadata] = useState<Metadata | null>(null);
  const [stationId, setStationId] = useState("");
  const [feederId, setFeederId] = useState("");
  const [busy, setBusy] = useState(true);
  const [revision, setRevision] = useState(0);
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [boundaryError, setBoundaryError] = useState<string | null>(null);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [showPoles, setShowPoles] = useState(false);
  const [showBarangays, setShowBarangays] = useState(false);
  const [query, setQuery] = useState("");
  const [assetType, setAssetType] = useState("transformer");
  const [searchOpen, setSearchOpen] = useState(false);
  const { resolvedTheme } = useTheme();
  const { colorblind } = useColorblindMode();

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

  const chooseStation = useCallback((id: string) => {
    if (id === stationRef.current) return;
    stationRef.current = id;
    feederRef.current = "";
    network.current = null;
    map.current?.clearNetwork();
    setStationId(id); setFeederId(""); setInspection(null);
    setBusy(true); setError(null); setQuery("");
  }, []);

  const inspectStation = useCallback((station: Station) => {
    const id = station.properties.modeled_source_node_id;
    if (id && station.properties.active_feeder_count > 0 && id !== stationRef.current) chooseStation(id);
    setInspection({ asset: station });
    map.current?.inspectStation(station);
  }, [chooseStation]);

  useEffect(() => {
    if (!container.current) return;
    let viewer: TopologyMap;
    try {
      viewer = new TopologyMap(container.current, {
        ready: () => setReady(true), select: inspect, station: inspectStation, error: setMapError,
      });
      map.current = viewer;
    } catch (cause) {
      // Constructor failures (for example disabled WebGL) still leave the page usable.
      queueMicrotask(() => setMapError(cause instanceof Error ? cause.message : "Map initialization failed"));
      return;
    }
    return () => { viewer.destroy(); map.current = null; };
  }, [inspect, inspectStation]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    loadMetadata().then(data => {
      if (cancelled) return;
      metadataRef.current = data;
      setMetadata(data);
      map.current?.setMetadata(data);
      const initial = data.substations.features
        .filter(f => f.properties.active_feeder_count > 0)
        .sort((a, b) => b.properties.transformer_count - a.properties.transformer_count)[0];
      if (!stationRef.current && initial?.properties.modeled_source_node_id) chooseStation(initial.properties.modeled_source_node_id);
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
      inspect({ kind: "node", id: feeder?.root_node_id ?? stationId });
      map.current?.fitNetwork();
      setBusy(false); setError(null); setRevision(value => value + 1);
    }).catch(cause => { if (!cancelled) { setBusy(false); setError(String(cause.message)); } });
    return () => { cancelled = true; };
  }, [ready, stationId, retry, inspect]);

  useEffect(() => {
    if (!ready) return;
    // Read CSS after next-themes has applied the root class/attribute.
    const frame = requestAnimationFrame(() => map.current?.setTheme());
    return () => cancelAnimationFrame(frame);
  }, [ready, resolvedTheme, colorblind]);

  useEffect(() => {
    if (!ready) return;
    map.current?.showPoles(showPoles);
  }, [ready, showPoles]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    map.current?.showBarangays(showBarangays);
    if (showBarangays) loadBoundaries("barangay").then(data => {
      if (!cancelled) map.current?.setBoundaries("barangay", data);
    }).catch(() => { if (!cancelled) setBoundaryError("Barangay context could not load. Toggle it off and on to retry."); });
    return () => { cancelled = true; };
  }, [ready, showBarangays]);

  const stations = useMemo(() => metadata?.substations.features
    .filter(f => f.properties.active_feeder_count > 0)
    .sort((a, b) => stationName(a).localeCompare(stationName(b))) ?? [], [metadata]);
  const feeders = metadata?.feeders.filter(f => f.substation_node_id === stationId) ?? [];
  const activeStation = stations.find(f => f.properties.modeled_source_node_id === stationId);
  const activeFeeder = feeders.find(f => f.feeder_id === feederId);

  function chooseFeeder(id: string) {
    feederRef.current = id; setFeederId(id); setQuery("");
    map.current?.setFeeder(id);
    inspect({ kind: "node", id: feeders.find(f => f.feeder_id === id)?.root_node_id ?? stationId });
    map.current?.fitNetwork();
  }

  function resetSelection() {
    inspect({ kind: "node", id: activeFeeder?.root_node_id ?? stationId });
  }

  // Only the first 50 matches enter React state/rendering. Large geometry and the
  // connectivity index stay in refs/the bounded cache, never in component state.
  const [matches, setMatches] = useState<Asset[]>([]);
  useEffect(() => {
    const timer = setTimeout(() => {
      const current = network.current;
      if ((!current && assetType !== "station") || busy || !searchOpen) { setMatches([]); return; }
      const candidates = assetType === "station" ? metadataRef.current?.substations.features ?? []
        : assetType === "edge" ? current!.graph.edges.values() : current!.graph.nodes.values();
      const found: Asset[] = [], needle = query.trim().toLowerCase();
      for (const asset of candidates) {
        const p = asset.properties;
        if (assetType !== "station" && assetType !== "edge" && p.node_type !== assetType) continue;
        if (assetType !== "station" && feederId && p.feeder_id !== feederId) continue;
        if (needle && !`${p.node_id ?? p.edge_id ?? p.asset_id} ${p.name ?? ""} ${p.barangay ?? ""} ${p.municipality ?? ""}`.toLowerCase().includes(needle)) continue;
        found.push(asset);
        if (found.length === 50) break;
      }
      setMatches(found);
    }, 150);
    return () => clearTimeout(timer);
  }, [query, assetType, feederId, revision, busy, searchOpen]);

  const props = inspection?.asset.properties;
  const isStation = Boolean(props && "active_feeder_count" in props);
  const fields = props ? [
    ["Identifier", props.node_id ?? props.edge_id ?? props.asset_id],
    ["Highlighted branch origin pole", inspection?.connectionPoleId],
    ["Modeled feeder ID", props.feeder_id],
    ["Location", [props.barangay, props.municipality].filter(Boolean).join(", ")],
    ["Connection status", props.connection_status],
    ["Geometry basis", props.geometry_basis],
    ["Data status", props.data_status],
    ["Synthetic asset", typeof props.is_synthetic === "boolean" ? (props.is_synthetic ? "Yes" : "No — observed geometry") : "Not specified in export"],
    ["Verified connectivity", props.is_verified_connectivity === true ? "Yes" : props.is_verified_connectivity === false ? "No" : "Not specified in export"],
  ].filter(([, value]) => value !== null && value !== undefined && value !== "") : [];

  return (
    <div className="space-y-4">
      <div className="rounded-xl border bg-card p-4">
        <div className="grid items-end gap-3 md:grid-cols-2 xl:grid-cols-[1fr_1fr_auto]">
          <label className="grid min-w-0 gap-1.5 text-sm font-medium">
            Substation
            <select aria-label="Substation" className={`${control} w-full min-w-0`} value={stationId}
              disabled={!metadata} onChange={event => chooseStation(event.target.value)}>
              {!metadata && <option value="">Loading mapped substations…</option>}
              {stations.map(station => <option key={station.properties.asset_id} value={station.properties.modeled_source_node_id!}>
                {stationName(station)} · {station.properties.transformer_count.toLocaleString()} transformers · OSM {station.properties.asset_id.split("/").at(-1)}
              </option>)}
            </select>
          </label>
          <label className="grid min-w-0 gap-1.5 text-sm font-medium">
            Modeled feeder
            <select aria-label="Modeled feeder" className={`${control} w-full min-w-0`} value={feederId} disabled={busy || !stationId || !!error}
              onChange={event => chooseFeeder(event.target.value)}>
              <option value="">All modeled feeders</option>
              {feeders.map(feeder => <option key={feeder.feeder_id} value={feeder.feeder_id}>
                Modeled feeder {feeder.modeled_feeder_number} · {feeder.transformer_count.toLocaleString()} transformers · {feeder.barangay}
              </option>)}
            </select>
          </label>
          <button type="button" className={button} disabled={busy || !!error} onClick={resetSelection}>
            <RotateCcw size={15} aria-hidden="true" /> Show whole {feederId ? "feeder" : "substation"}
          </button>
        </div>
        <p className="mt-3 text-xs leading-5 text-muted-foreground">
          {metadata ? `${metadata.substations.features.length} connected substations · ${metadata.dataset.counts.active_feeders} modeled feeders` : "Loading station catalog…"}
          {activeStation && ` · ${Number(activeFeeder?.transformer_count ?? activeStation.properties.transformer_count).toLocaleString()} transformers in this view`}
        </p>
      </div>

      {(error || boundaryError) && <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-3 text-sm">
        <p>{error || boundaryError}</p>
        <button className={button} onClick={() => { setBusy(true); setError(null); setRetry(value => value + 1); }}>Retry loading</button>
      </div>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 overflow-hidden rounded-xl border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
            <h2 className="text-sm font-semibold">Modeled network</h2>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={button} disabled={busy || !!error} onClick={() => map.current?.fitNetwork()}><Focus size={15} aria-hidden="true" /> Fit network</button>
              <button type="button" className={button} disabled={!metadata} onClick={() => map.current?.fitAll()}>Fit all substations</button>
            </div>
          </div>
          <div className="network-map relative h-[480px] sm:h-[650px]" aria-busy={busy}>
            <div ref={container} className="h-full w-full" />
            {!busy && inspection?.connectionPoleId && <div className="pointer-events-none absolute left-3 top-3 max-w-[calc(100%-4.5rem)] rounded-lg border bg-card/95 px-3 py-2 text-xs shadow-sm">
              Downstream from the transformer’s connection pole
            </div>}
            {busy && !error && <div role="status" className="pointer-events-none absolute left-3 top-3 flex max-w-[calc(100%-4.5rem)] items-center gap-2 rounded-lg border bg-card/95 px-3 py-2 text-sm shadow-sm">
              <LoaderCircle size={16} className="shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              {metadata ? "Loading selected network…" : "Loading mapped substations…"}
            </div>}
            {mapError && <p role="alert" className="absolute inset-x-3 bottom-10 rounded-md border bg-card p-3 text-sm">{mapError}</p>}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-2 border-t px-4 py-3 text-xs" aria-label="Map legend">
            <Legend shape="square" color="station">Mapped station</Legend>
            <Legend shape="line" color="downstream">{inspection?.connectionPoleId ? "Connection-pole branch" : "Downstream"}</Legend>
            <Legend shape="dash" color="station">Inferred station link</Legend>
            <Legend shape="line" color="station">Upstream path</Legend>
            <Legend shape="dot" color="selected">Selected</Legend>
            <Legend shape="line" color="remaining">Remaining network</Legend>
          </div>
        </div>

        <aside className="min-w-0 space-y-4" aria-label="Network inspection and display settings">
          <section className="rounded-xl border bg-card p-4">
            <h2 className="font-semibold">Asset inspection</h2>
            <div className="mt-3" aria-live="polite">
              {inspection ? <>
                <p className="break-words text-sm font-medium capitalize">{isStation ? stationName(inspection.asset as Station) : assetName(inspection.asset)}</p>
                {inspection.connectionPoleId && <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  This transformer is an endpoint. Purple shows the downstream branch from its recorded connection pole;
                  blue shows that pole’s upstream supply path. Orange marks the selected transformer.
                </p>}
                {inspection.downstream !== undefined && <div className="my-3 border-y py-3">
                  <p className="text-2xl font-semibold tabular-nums">{inspection.downstream.toLocaleString()}</p>
                  <p className="text-xs text-muted-foreground">{inspection.connectionPoleId ? "Transformers in the connection pole’s downstream branch" : "Transformers downstream in the stored tree"}</p>
                </div>}
                {inspection.connectionPoleId && <p className="text-xs text-muted-foreground">No assets are modeled downstream of the transformer itself.</p>}
              </> : <p className="text-sm leading-6 text-muted-foreground">Click a station, line, pole, or transformer to inspect its recorded attributes.</p>}
            </div>
            <dl className="mt-3 space-y-3 text-xs">
              {fields.map(([label, value]) => <div key={String(label)}>
                <dt className="text-muted-foreground">{label}</dt><dd className="mt-1 break-words [overflow-wrap:anywhere]">{String(value)}</dd>
              </div>)}
            </dl>
          </section>
          <section className="rounded-xl border bg-card p-4">
            <h2 className="font-semibold">Map display</h2>
            <label className="mt-2 flex min-h-11 cursor-pointer items-center gap-2 text-sm"><input type="checkbox" checked={showPoles} onChange={event => setShowPoles(event.target.checked)} className="size-4 accent-primary" /> Show poles at all zooms</label>
            <p className="text-xs leading-5 text-muted-foreground">Poles appear automatically at zoom 14+. Transformers remain visible at wider zooms.</p>
            <label className="mt-2 flex min-h-11 cursor-pointer items-center gap-2 text-sm"><input type="checkbox" checked={showBarangays} onChange={event => setShowBarangays(event.target.checked)} className="size-4 accent-primary" /> Barangay borders and labels</label>
            <p className="text-xs leading-5 text-muted-foreground">Barangay detail appears at close zoom. Municipality borders and labels remain available.</p>
          </section>
        </aside>
      </div>

      <details className="rounded-xl border bg-card" onToggle={event => setSearchOpen(event.currentTarget.open)}>
        <summary className="min-h-11 cursor-pointer px-4 py-3 text-sm font-medium">Choose a component without clicking the map</summary>
        <div className="space-y-3 border-t p-4">
          <div className="grid gap-3 sm:grid-cols-[200px_1fr]">
            <label className="grid gap-1.5 text-sm">Component type<select className={control} value={assetType} onChange={event => setAssetType(event.target.value)}>
              <option value="transformer">Transformer</option><option value="edge">Line / logical connection</option><option value="pole">Pole</option><option value="station">Mapped substation ({metadata?.substations.features.length ?? 0})</option>
            </select></label>
            <label className="grid gap-1.5 text-sm">Find by component ID or location<div className="relative"><Search className="absolute left-3 top-3.5 size-4 text-muted-foreground" aria-hidden="true" /><input className={`${control} w-full pl-9`} value={query} onChange={event => setQuery(event.target.value)} placeholder="Component ID, barangay, or municipality" /></div></label>
          </div>
          <p className="text-xs text-muted-foreground">First 50 matches in the selected network, or all {metadata?.substations.features.length ?? 0} connected station records. Search to narrow the list. Logical Point connections are inspectable here.</p>
          <div className="grid max-h-64 gap-2 overflow-y-auto sm:grid-cols-2" aria-label="Matching components">
            {!busy && matches.length === 0 && <p className="text-sm text-muted-foreground">No matching components.</p>}
            {matches.map(asset => {
              const id = String(asset.properties.node_id ?? asset.properties.edge_id ?? asset.properties.asset_id);
              const stationAsset = "active_feeder_count" in asset.properties;
              return <button type="button" key={id} disabled={busy} className="min-h-11 rounded-md border p-3 text-left text-xs hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring" onClick={() => {
                if (stationAsset) { inspectStation(asset as Station); map.current?.focusAsset(asset); }
                else inspect({ kind: asset.properties.node_id ? "node" : "edge", id }, true);
              }}>
                <span className="block capitalize">{stationAsset ? stationName(asset as Station) : assetName(asset)}</span><span className="mt-1 block break-all text-muted-foreground">{id}</span>
              </button>;
            })}
          </div>
        </div>
      </details>
      <p className="max-w-5xl text-xs leading-5 text-muted-foreground">
        Observed substation positions are distinct from modeled electrical connectivity. Lines, feeder assignments,
        and highlighted paths are inferred infrastructure, not verified official PENELCO routes or service areas.
        Modeled feeder numbers are local identifiers. {metadata?.dataset.counts.unresolved_transformers ?? 81} transformers
        remain unresolved province-wide and are not assigned a connection here. This view does not estimate outages, customers interrupted, or electrical load.
      </p>
    </div>
  );
}

function Legend({ shape, color, children }: { shape: "square" | "dot" | "line" | "dash"; color: string; children: React.ReactNode }) {
  const line = shape === "line" || shape === "dash";
  return <span className="inline-flex items-center gap-1.5"><span aria-hidden="true"
    className={line ? "w-5 border-t-[3px]" : `size-3 border-2 ${shape === "dot" ? "rounded-full" : ""}`}
    style={{ borderColor: `var(--network-${color})`, borderStyle: shape === "dash" ? "dashed" : "solid", backgroundColor: !line ? `var(--network-${color})` : undefined }} />{children}</span>;
}
