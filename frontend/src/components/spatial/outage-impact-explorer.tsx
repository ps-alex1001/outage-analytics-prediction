"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "next-themes";
import { ArrowDown, ArrowUp, Focus, LoaderCircle, Search } from "lucide-react";
import { useColorblindMode } from "@/components/theme-provider";
import { TopologyMap, type MapBackground } from "@/lib/topology-map";
import { assetName, loadBoundaries, loadMetadata, type Asset, type LoadedNetwork, type Selection } from "@/lib/topology";
import { fetchImpact, loadImpactDetail, loadImpactBoundaries, loadImpactFeeder, prefetchImpact, manilaTime, type ImpactDetail, type ImpactMetadata, type OutageEvent } from "@/lib/outage-impacts";

const control = "min-h-11 min-w-0 rounded-md border bg-background px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-50";
const button = `${control} inline-flex items-center justify-center gap-2 hover:bg-muted disabled:cursor-not-allowed`;
type EventPage = { events: OutageEvent[]; total: number; page: number; pages: number };
const initialFilters = { year: "", municipality_psgc: "", barangay_psgc: "", cause_category: "", status: "", q: "" };
const initialLayers = { fault: true, loss: true, upstream: true, unaffected: true, poles: true, transformers: true, boundaries: true, halo: true };

export default function OutageImpactExplorer() {
  const [metadata, setMetadata] = useState<ImpactMetadata | null>(null);
  const [filters, setFilters] = useState(initialFilters);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("start_time");
  const [direction, setDirection] = useState("desc");
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<EventPage | null>(null);
  const [tableBusy, setTableBusy] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ImpactDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [layers, setLayers] = useState(initialLayers);
  const [basemap, setBasemap] = useState<MapBackground>("street");
  const [basemapError, setBasemapError] = useState(false);
  const [inspection, setInspection] = useState<{ asset: Asset; role: string } | null>(null);
  const [assetQuery, setAssetQuery] = useState("");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [revision, setRevision] = useState(0);
  const [retry, setRetry] = useState(0);
  const container = useRef<HTMLDivElement>(null);
  const viewer = useRef<TopologyMap | null>(null);
  const network = useRef<LoadedNetwork | null>(null);
  const currentDetail = useRef<ImpactDetail | null>(null);
  const request = useRef<AbortController | null>(null);
  const prefetchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const appliedQuery = useRef("");
  const { resolvedTheme } = useTheme();
  const { colorblind } = useColorblindMode();

  function inspect(selection: Selection) {
    const graph = network.current?.graph;
    const d = currentDetail.current;
    if (!graph || !d?.impact) return;
    const asset = selection.kind === "node" ? graph.nodes.get(selection.id) : graph.edges.get(selection.id);
    if (!asset) return;
    const role = selection.id === d.assignment.fault_source_topology_id ? "Simulated fault source"
      : (selection.kind === "node" ? d.impact.affected_node_ids : d.impact.affected_edge_ids).includes(selection.id) ? "Modeled loss of supply"
      : (selection.kind === "node" ? d.impact.upstream_node_ids : d.impact.upstream_edge_ids).includes(selection.id) ? "Upstream supply context" : "Unaffected in this scenario";
    setInspection({ asset, role });
  }

  useEffect(() => {
    const abort = new AbortController();
    fetchImpact<ImpactMetadata>("metadata", abort.signal).then(setMetadata)
      .catch(e => { if (!abort.signal.aborted) setError(e.message); });
    return () => abort.abort();
  }, [retry]);

  useEffect(() => {
    if (query === appliedQuery.current) return;
    const timeout = setTimeout(() => {
      appliedQuery.current = query; request.current?.abort();
      setFilters(f => ({ ...f, q: query })); setPage(1); setSelected(null);
    }, 300);
    return () => clearTimeout(timeout);
  }, [query]);

  useEffect(() => {
    const abort = new AbortController();
    const params = new URLSearchParams({ page: String(page), page_size: "10", sort, direction });
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
    Promise.resolve().then(() => {
      if (abort.signal.aborted) return;
      setTableBusy(true); setError(null);
      return fetchImpact<EventPage>(`events?${params}`, abort.signal).then(data => {
        if (!abort.signal.aborted) { setRows(data); setTableBusy(false); }
      });
    }).catch(e => { if (!abort.signal.aborted) { setError(e.message); setRows(null); setTableBusy(false); } });
    return () => abort.abort();
  }, [filters, page, sort, direction, retry]);

  useEffect(() => {
    if (!container.current) return;
    let map: TopologyMap;
    try {
      map = new TopologyMap(container.current, {
        ready: () => { map.showPoles(true); setReady(true); }, select: inspect,
        station: station => setInspection({ asset: station, role: "Mapped station; supply paths are inferred" }),
        error: setMapError,
        basemapError: () => { setBasemap("plain"); setBasemapError(true); },
      }, true);
      viewer.current = map;
      Promise.all([loadMetadata(), loadBoundaries("municipality")]).then(([meta, boundaries]) => {
        if (viewer.current !== map) return;
        map.setMetadata(meta); map.setBoundaries("municipality", boundaries);
        if (!currentDetail.current && !request.current) map.fitAll();
      }).catch(e => { if (viewer.current === map) setMapError(e.message); });
    } catch (e) {
      queueMicrotask(() => setMapError(e instanceof Error ? e.message : "Unable to initialize the map"));
      return;
    }
    return () => { clearTimeout(prefetchTimer.current); viewer.current = null; map.destroy(); };
    // The map's callbacks read refs, so a selection never recreates the map.
  }, []);

  useEffect(() => {
    if (!ready) return;
    const abort = new AbortController();
    request.current = abort;
    Promise.resolve().then(async () => {
      if (abort.signal.aborted) return;
      setMapError(null);
      if (!selected) {
        setDetail(null); setInspection(null); setAssetQuery(""); setAssets([]);
        network.current = null; currentDetail.current = null;
        viewer.current?.clearNetwork(); viewer.current?.setImpactBoundaries([], ""); setBusy(false);
        if (request.current === abort) request.current = null;
        return;
      }
      setBusy(true);
      const started = performance.now();
      try {
        const cached = await loadImpactDetail(selected, abort.signal);
        const detailReady = performance.now();
        const [n, boundaries] = await Promise.all([
          cached.impact && cached.assignment.feeder_id ? loadImpactFeeder(cached.assignment.feeder_id) : Promise.resolve(null),
          loadImpactBoundaries([...(cached.impact?.affected_barangay_psgcs ?? []), cached.event.barangay_psgc], abort.signal),
        ]);
        if (abort.signal.aborted) return;
        const dataReady = performance.now(), map = viewer.current;
        if (!map) return;
        const d = { ...cached, boundaries };
        await map.waitForImpactMap(abort.signal);
        const reused = n ? await map.prepareImpactNetwork(n, abort.signal) : false;
        if (abort.signal.aborted) return;
        map.stopImpactMotion();
        map.setImpactGeography(boundaries, d.impact?.affected_barangay_psgcs ?? [], d.event.barangay_psgc);
        if (d.impact && n) {
          if (network.current !== n) setAssets([...n.graph.nodes.values(), ...n.graph.edges.values()]);
          map.setSelection({ kind: d.assignment.fault_source_kind!, id: d.assignment.fault_source_topology_id! }, {
            nodeIds: new Set(d.impact.affected_node_ids), edgeIds: new Set(d.impact.affected_edge_ids),
            upstreamEdgeIds: new Set(d.impact.upstream_edge_ids), upstreamNodeIds: new Set(d.impact.upstream_node_ids),
            transformers: d.impact.affected_transformer_count,
          });
          map.fadeInImpact();
          map.fitImpact({ automatic: true });
        } else {
          map.clearNetwork(); setAssets([]);
          map.fitBoundaryFeatures(boundaries, true);
        }
        network.current = n; currentDetail.current = d;
        setDetail(d); setInspection(null); setAssetQuery(""); setRevision(r => r + 1);
        setBusy(false);
        const viewReady = performance.now();
        map.map.once("render", () => {
          if (abort.signal.aborted) return;
          const timing = { event: selected, reusedFeeder: reused,
            detailMs: Math.round(detailReady - started), dataMs: Math.round(dataReady - detailReady),
            mapMs: Math.round(viewReady - dataReady), firstFrameMs: Math.round(performance.now() - started) };
          performance.measure("outage-selection:ready", { start: started, end: viewReady });
          if (performance.getEntriesByName("outage-selection:ready").length > 30) performance.clearMeasures("outage-selection:ready");
          if (process.env.NODE_ENV === "development") console.debug("[outage-selection]", JSON.stringify(timing));
        });
        map.map.triggerRepaint();
      } catch (e) {
        if (!abort.signal.aborted) {
          setMapError(`${e instanceof Error ? e.message : "Unable to load the impact"}${currentDetail.current ? " Showing the previous outage." : ""}`);
          setBusy(false);
        }
      } finally {
        if (request.current === abort) request.current = null;
      }
    });
    return () => abort.abort();
  }, [selected, ready, retry]);

  useEffect(() => {
    // Read palette variables after next-themes has applied the root class.
    const frame = requestAnimationFrame(() => viewer.current?.setTheme());
    return () => cancelAnimationFrame(frame);
  }, [resolvedTheme, colorblind, ready]);
  useEffect(() => { viewer.current?.setImpactLayers(layers); }, [layers, ready, revision]);
  useEffect(() => { viewer.current?.setBasemap(basemap); }, [basemap, ready]);

  function filter(key: keyof typeof filters, value: string) {
    request.current?.abort();
    setFilters(f => ({ ...f, [key]: value, ...(key === "municipality_psgc" ? { barangay_psgc: "" } : {}) }));
    setPage(1); setSelected(null);
  }
  function sortBy(key: string) {
    setDirection(sort === key && direction === "desc" ? "asc" : "desc"); setSort(key); setPage(1);
  }
  function chooseEvent(id: string) {
    if (selected === id) return;
    clearTimeout(prefetchTimer.current); request.current?.abort(); viewer.current?.stopImpactMotion();
    setSelected(id); setBusy(true);
  }
  function warmEvent(id: string) {
    clearTimeout(prefetchTimer.current);
    prefetchTimer.current = setTimeout(() => { void prefetchImpact(id); }, 150);
  }
  const municipalities = useMemo(() => [...new Map(metadata?.geography.map(g => [g.municipality_psgc, g]) ?? []).values()], [metadata]);
  const barangays = metadata?.geography.filter(g => !filters.municipality_psgc || g.municipality_psgc === filters.municipality_psgc) ?? [];
  const assetMatches = useMemo(() => {
    if (!assetQuery.trim()) return [];
    const q = assetQuery.toLowerCase();
    return assets
      .filter(a => `${a.properties.node_id ?? a.properties.edge_id} ${assetName(a)}`.toLowerCase().includes(q)).slice(0, 12);
  }, [assetQuery, assets]);
  const sortIcon = (key: string) => sort === key ? (direction === "asc" ? <ArrowUp size={13} /> : <ArrowDown size={13} />) : null;
  const metrics = detail?.impact;

  return <div className="impact-workspace space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4 text-sm">
      <p>{metadata ? <><strong>{metadata.total_events.toLocaleString()}</strong> V2 events · {metadata.validation.mapped_percentage.toFixed(1)}% with modeled impact assignments</> : "Loading event catalog…"}</p>
      <p className="max-w-2xl text-xs leading-5 text-muted-foreground">Synthetic downstream outage scenarios. Connectivity and fault locations are unverified. Outlines identify infrastructure locations, not whole-barangay outages.</p>
    </div>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
      <label className="space-y-1 text-xs font-medium xl:col-span-2"><span>Search events</span><span className="relative block"><Search aria-hidden className="absolute left-3 top-3.5 size-4 text-muted-foreground" /><input className={`${control} w-full pl-9`} value={query} onChange={e => setQuery(e.target.value)} placeholder="ID, place or cause" /></span></label>
      <label className="space-y-1 text-xs font-medium">Year<select className={`${control} block w-full`} value={filters.year} onChange={e => filter("year", e.target.value)}><option value="">All years</option>{metadata?.years.map(y => <option key={y}>{y}</option>)}</select></label>
      <label className="space-y-1 text-xs font-medium">Municipality<select className={`${control} block w-full`} value={filters.municipality_psgc} onChange={e => filter("municipality_psgc", e.target.value)}><option value="">All municipalities</option>{municipalities.map(m => <option key={m.municipality_psgc} value={m.municipality_psgc}>{m.municipality}</option>)}</select></label>
      <label className="space-y-1 text-xs font-medium">Barangay<select className={`${control} block w-full`} value={filters.barangay_psgc} onChange={e => filter("barangay_psgc", e.target.value)}><option value="">All barangays</option>{barangays.map(b => <option key={b.barangay_psgc} value={b.barangay_psgc}>{b.barangay}{!filters.municipality_psgc ? `, ${b.municipality}` : ""}</option>)}</select></label>
      <label className="space-y-1 text-xs font-medium">Synthetic cause<select className={`${control} block w-full`} value={filters.cause_category} onChange={e => filter("cause_category", e.target.value)}><option value="">All causes</option>{metadata?.causes.map(c => <option key={c}>{c}</option>)}</select></label>
      <label className="space-y-1 text-xs font-medium">Assignment<select className={`${control} block w-full`} value={filters.status} onChange={e => filter("status", e.target.value)}><option value="">All assignments</option><option value="resolved_modeled">Mapped scenario</option><option value="unresolved">Unresolved</option></select></label>
    </div>
    {error && <div role="alert" className="rounded-lg border border-destructive/40 p-4 text-sm">{error} <button className={button} onClick={() => setRetry(x => x + 1)}>Retry</button></div>}
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(390px,0.9fr)_minmax(0,1.1fr)]">
      <section className="min-w-0 overflow-hidden rounded-lg border bg-card" aria-label="Synthetic outage event table">
        <div className="flex items-center justify-between border-b p-4"><h2 className="font-semibold">Outage events</h2><span className="text-xs text-muted-foreground" role="status">{tableBusy ? "Updating…" : `${rows?.total.toLocaleString() ?? 0} matches`}</span></div>
        <div className="overflow-x-auto" aria-busy={tableBusy}>
          <table className="w-full text-left text-sm"><caption className="sr-only">Select an outage to inspect its simulated infrastructure impact. All times are Asia/Manila.</caption>
            <thead className="border-b bg-muted/40 text-xs"><tr>{[["start_time", "Start (Manila)"], ["barangay", "Location"], ["duration_minutes", "Duration"]].map(([key, label]) => <th key={key} scope="col" aria-sort={sort === key ? (direction === "asc" ? "ascending" : "descending") : "none"}><button className="flex min-h-11 w-full items-center gap-1 px-3 font-medium focus-visible:outline-2" onClick={() => sortBy(key)}>{label}{sortIcon(key)}</button></th>)}<th scope="col" className="p-3">Cause / impact</th></tr></thead>
            <tbody className={tableBusy ? "opacity-50" : ""}>{rows?.events.map(event => <tr key={event.outage_id} aria-selected={selected === event.outage_id} onMouseEnter={() => warmEvent(event.outage_id)} onMouseLeave={() => clearTimeout(prefetchTimer.current)} onClick={() => { if (!tableBusy) chooseEvent(event.outage_id); }} className={`cursor-pointer border-b last:border-0 hover:bg-muted/50 ${selected === event.outage_id ? "bg-accent outline-2 -outline-offset-2 outline-ring" : ""}`}>
              <td className="px-3 py-2"><button disabled={tableBusy} className="min-h-11 text-left focus-visible:outline-2" onFocus={() => warmEvent(event.outage_id)} onBlur={() => clearTimeout(prefetchTimer.current)} onClick={e => { e.stopPropagation(); chooseEvent(event.outage_id); }} aria-label={`Inspect ${event.outage_id}, ${event.barangay}, ${event.municipality}`} aria-pressed={selected === event.outage_id}><span className="block text-xs">{manilaTime(event.start_time)}</span><span className="block text-[11px] text-muted-foreground">{event.outage_id}</span></button></td>
              <td className="px-3 py-2"><span className="block font-medium">{event.barangay}</span><span className="block text-xs text-muted-foreground">{event.municipality}</span></td>
              <td className="whitespace-nowrap px-3 py-2 tabular-nums">{event.duration_minutes.toFixed(1)} min</td>
              <td className="px-3 py-2"><span className="block text-xs">{event.cause_category}</span><span className="text-[11px] text-muted-foreground">{event.resolution_status === "resolved_modeled" ? "Mapped scenario" : "Unresolved"}</span></td>
            </tr>)}</tbody>
          </table>
          {!tableBusy && !error && !rows?.events.length && <p className="p-8 text-center text-sm text-muted-foreground">No events match these filters.</p>}
        </div>
        <div className="flex items-center justify-between gap-2 border-t p-3 text-xs"><button className={button} disabled={tableBusy || page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button><span>Page {page} of {rows?.pages ?? 1}</span><button className={button} disabled={tableBusy || page >= (rows?.pages ?? 1)} onClick={() => setPage(p => p + 1)}>Next</button></div>
      </section>
      <section className="min-w-0 space-y-3" aria-label="Selected outage infrastructure impact" aria-busy={busy}>
        {mapError && <button className={button} onClick={() => setRetry(x => x + 1)}>Reload selected impact</button>}
        <div className="rounded-lg border bg-card p-4">
          <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">{detail ? `${detail.event.barangay}, ${detail.event.municipality}` : "Infrastructure impact"}</h2><span className="flex items-center gap-2 text-xs text-muted-foreground" role="status">{busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}{busy ? (detail ? "Loading next outage…" : "Loading impact…") : detail ? (metrics ? "Mapped synthetic scenario" : "Unresolved assignment") : "Select an outage row"}</span></div>
          {busy && detail && <p className="mt-2 text-xs text-muted-foreground" role="status">Showing {detail.event.outage_id} while the next outage loads.</p>}
          {detail && <div className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground"><p className="break-all font-mono text-foreground">{detail.event.outage_id}</p><p>{manilaTime(detail.event.start_time)} · {detail.event.duration_minutes.toFixed(1)} minutes · {detail.event.cause_category}</p>
            {metrics ? <><p className="text-foreground">{detail.assignment.source_promoted_upstream ? "Upstream branch source (simulated)" : detail.assignment.impact_scenario?.replaceAll("_", " ")}</p><p className="break-all">Simulated source: {detail.assignment.fault_source_asset_type} · {detail.assignment.fault_source_asset_id}</p><p className="break-all">{detail.assignment.substation_name ?? "Modeled station"} · {detail.assignment.feeder_id}</p>
              {detail.assignment.source_promoted_upstream && <p className="text-foreground">The source was selected higher in the active topology to create a larger outage. The ripple marks the start of the red branch.</p>}
              {detail.assignment.source_target_met === false && <p>Source selection target limited by {detail.assignment.source_selection_limit_reason?.replaceAll("_", " ")}. No disconnected assets are added.</p>}</>
              : <p className="text-foreground">{detail.event.unresolved_reason?.replaceAll("_", " ")}. Impact is unknown; no zero-loss assumption or substitute asset is used.</p>}
          </div>}
          {metrics && <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">{[["Distribution lines", `${metrics.affected_line_length_km.toFixed(2)} km`], ["Line segments", metrics.affected_line_segment_count.toLocaleString()], ["Poles losing supply", metrics.affected_pole_count.toLocaleString()], ["Transformers", metrics.affected_transformer_count.toLocaleString()]].map(([label, value]) => <div key={label}><dt className="text-[11px] text-muted-foreground">{label}</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{value}</dd></div>)}</dl>}
          {metrics?.large_modeled_impact && <p className="mt-3 text-xs">Large modeled impact flagged for review. This may reflect feeder-wide loss in the simplified radial model.</p>}
          {metrics?.no_downstream_transformers && <p className="mt-3 text-xs">This valid isolated branch has no downstream modeled transformers.</p>}
        </div>
        <div className="overflow-hidden rounded-lg border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2"><p className="text-sm font-medium">Modeled loss of supply</p><button className={button} disabled={!metrics || busy || !ready} onClick={() => viewer.current?.fitImpact()}><Focus size={16} aria-hidden />Fit to impact</button></div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-3 py-2 text-xs">
            <label className="flex items-center gap-2">Background<select className={control} value={basemap} onChange={e => { setBasemap(e.target.value as MapBackground); setBasemapError(false); }}><option value="street">Street map (muted)</option><option value="color">Color street map</option><option value="satellite">Satellite imagery</option><option value="plain">Plain background</option></select></label>
            <span className="text-muted-foreground">Poles appear as you zoom in.</span>
            {basemapError && <p role="status" className="w-full text-muted-foreground">Background unavailable. Showing plain background; select a map background to retry.</p>}
          </div>
          <div className="relative"><div ref={container} className="network-map h-[480px] w-full sm:h-[560px]" />
            {(!selected || busy || (detail && !metrics) || mapError) && <div className="pointer-events-none absolute left-3 right-14 top-3 rounded-md border bg-card/95 p-3 text-sm shadow-sm" role={mapError ? "alert" : "status"}>{mapError ?? (busy ? (detail ? "Loading next outage… The previous outage remains visible." : "Loading the selected event and connected feeder…") : detail ? "No defensible connected fault source. The dashed outline shows the original event barangay only." : "Select an outage in the event table to show its modeled fault source and affected network.")}</div>}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-2 border-t p-3 text-xs">{([['fault', 'Outage origin', 'var(--impact-fault, #b91c1c)'], ['loss', 'Losing supply', 'var(--impact-loss, #c83036)'], ['upstream', 'Upstream path', 'var(--impact-upstream, #0072b2)'], ['unaffected', 'Unaffected', 'var(--network-remaining, #7f899c)']] as const).map(([key, label, color]) => <label key={key} className="flex min-h-8 items-center gap-2"><input type="checkbox" checked={layers[key]} onChange={e => setLayers(l => ({ ...l, [key]: e.target.checked }))} /><span className={key === "fault" ? "size-3 rounded-full border-2" : "h-1 w-5 rounded"} style={key === "fault" ? { borderColor: color } : { backgroundColor: color }} />{label}</label>)}</div>
          <div className="flex flex-wrap gap-4 px-3 pb-3 text-xs">{([['poles', 'Poles'], ['transformers', 'Transformers'], ['boundaries', 'Barangay outlines']] as const).map(([key, label]) => <label key={key} className="flex min-h-8 items-center gap-2"><input type="checkbox" checked={layers[key]} onChange={e => setLayers(l => ({ ...l, [key]: e.target.checked }))} />{label}</label>)}<span className="self-center text-muted-foreground">Ripple: approximate simulated source. Dashed outline: origin barangay.</span></div>
          <div className="border-t px-3 py-2 text-xs leading-5">
            <label className="flex min-h-8 items-center gap-2"><input type="checkbox" checked={layers.halo} onChange={e=>setLayers(l=>({...l,halo:e.target.checked}))} /><span className="size-2.5 border-2" style={{borderColor:"var(--impact-loss, #c83036)", boxShadow:"0 0 5px var(--impact-loss, #c83036)"}} />Impact halo</label>
            <p className="text-muted-foreground">The soft red halo follows affected lines to make the outage easier to see. It does not represent a geographic coverage area. The ripple marks the source; blue shows its upstream supply path.</p>
          </div>
        </div>
        {metrics && <div className="rounded-lg border bg-card p-4 text-xs"><p className="font-medium">Barangays containing affected infrastructure ({metrics.affected_barangay_count})</p><p className="mt-1 leading-5 text-muted-foreground">{detail.affected_barangays.map(b => `${b.barangay}, ${b.municipality}${b.is_origin_barangay ? " (origin)" : ""}`).join("; ")}. These are infrastructure intersections, not complete outage coverage.</p></div>}
        {metrics && <div className="rounded-lg border bg-card p-4"><label className="text-xs font-medium">Inspect a component<input className={`${control} mt-2 block w-full`} value={assetQuery} onChange={e => setAssetQuery(e.target.value)} placeholder="Search component ID, type or barangay" /></label>
          {assetMatches.length > 0 && <ul className="mt-2 max-h-48 overflow-y-auto">{assetMatches.map(a => { const id = String(a.properties.node_id ?? a.properties.edge_id); return <li key={id}><button className="min-h-11 w-full break-all rounded px-2 text-left text-xs hover:bg-muted focus-visible:outline-2" onClick={() => { inspect({ kind: a.properties.node_id ? "node" : "edge", id }); viewer.current?.focusAsset(a); }}>{assetName(a)} · {id}</button></li>; })}</ul>}
          {inspection && <div className="mt-3 space-y-1 break-all text-xs"><p className="font-medium">{inspection.role}</p><p>{assetName(inspection.asset)}</p><p className="font-mono text-muted-foreground">{String(inspection.asset.properties.node_id ?? inspection.asset.properties.edge_id ?? inspection.asset.properties.asset_id)}</p><p className="text-muted-foreground">Connected components may lose supply without physically failing.</p></div>}
        </div>}
      </section>
    </div>
  </div>;
}
