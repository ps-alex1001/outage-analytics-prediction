"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useTheme } from "next-themes";
import { SpatialHeaderFilters } from "@/components/page-header";
import EnvironmentalProfile from "@/components/spatial/environmental-profile";
import WeatherContext from "@/components/spatial/weather-context";
import { ArrowDown, ArrowUp, ChevronDown, Focus, LoaderCircle } from "lucide-react";
import { TopologyMap, type MapBackground } from "@/lib/topology-map";
import { loadBoundaries, loadMetadata } from "@/lib/topology";
import { fetchImpact, loadImpactDetail, loadImpactBoundaries, loadImpactFeeder, prefetchImpact, manilaTime, type ImpactDetail, type ImpactMetadata, type OutageEvent } from "@/lib/outage-impacts";

const control = "min-h-11 min-w-0 rounded-md border bg-background px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-50";
const button = `${control} inline-flex items-center justify-center gap-2 hover:bg-muted disabled:cursor-not-allowed`;
type EventPage = { events: OutageEvent[]; total: number; page: number; pages: number };
const initialFilters = { year: "", municipality_psgc: "", barangay_psgc: "", cause_category: "" };
const initialLayers = { fault: true, loss: true, upstream: true, unaffected: true, poles: true, transformers: true, boundaries: true, halo: true };

export default function OutageImpactExplorer() {
  const [metadata, setMetadata] = useState<ImpactMetadata | null>(null);
  const [filters, setFilters] = useState(initialFilters);
  const [sort, setSort] = useState("start_time");
  const [direction, setDirection] = useState("desc");
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<EventPage | null>(null);
  const [tableBusy, setTableBusy] = useState(true);
  const [eventsOpen, setEventsOpen] = useState(true);
  const eventsContentId = useId();
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ImpactDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [basemap, setBasemap] = useState<MapBackground>("street");
  const [basemapError, setBasemapError] = useState(false);
  const [retry, setRetry] = useState(0);
  const container = useRef<HTMLDivElement>(null);
  const viewer = useRef<TopologyMap | null>(null);
  const currentDetail = useRef<ImpactDetail | null>(null);
  const request = useRef<AbortController | null>(null);
  const prefetchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    const abort = new AbortController();
    fetchImpact<ImpactMetadata>("metadata", abort.signal).then(setMetadata)
      .catch(e => { if (!abort.signal.aborted) setError(e.message); });
    return () => abort.abort();
  }, [retry]);

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
        ready: () => { map.showPoles(true); setReady(true); }, select: () => {},
        station: () => {},
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
    const map = viewer.current;
    const element = container.current;
    if (!map || !element || !ready) return;
    // Fit actions use the space released by the collapsed table without moving
    // the camera or recreating the map when the header is toggled.
    const updatePadding = () => {
      const floating = matchMedia("(min-width: 1280px)").matches;
      const width = element.clientWidth;
      map.setFitPadding({ top: floating && !eventsOpen ? 90 : 45, bottom: 110,
        left: floating && eventsOpen ? Math.min(width * 0.42, 560) + 32 : 45,
        right: floating ? Math.min(width * 0.30, 400) + 32 : 45 });
    };
    updatePadding();
    const resize = new ResizeObserver(updatePadding);
    resize.observe(element);
    return () => resize.disconnect();
  }, [eventsOpen, ready]);

  useEffect(() => {
    if (!ready) return;
    const abort = new AbortController();
    request.current = abort;
    Promise.resolve().then(async () => {
      if (abort.signal.aborted) return;
      setMapError(null);
      if (!selected) {
        setDetail(null);
        currentDetail.current = null;
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
          map.setSelection({ kind: d.assignment.fault_source_kind!, id: d.assignment.fault_source_topology_id! }, {
            nodeIds: new Set(d.impact.affected_node_ids), edgeIds: new Set(d.impact.affected_edge_ids),
            upstreamEdgeIds: new Set(d.impact.upstream_edge_ids), upstreamNodeIds: new Set(d.impact.upstream_node_ids),
            transformers: d.impact.affected_transformer_count,
          });
          map.fadeInImpact();
          map.fitImpact({ automatic: true });
        } else {
          map.clearNetwork();
          map.fitBoundaryFeatures(boundaries, true);
        }
        currentDetail.current = d;
        setDetail(d);
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
  }, [resolvedTheme, ready]);
  useEffect(() => { viewer.current?.setImpactLayers(initialLayers); }, [ready]);
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
  const place = metadata?.geography.find(g => filters.barangay_psgc ? g.barangay_psgc === filters.barangay_psgc : g.municipality_psgc === filters.municipality_psgc);
  const selectedArea = place ? (filters.barangay_psgc ? `${place.barangay}, ${place.municipality}` : place.municipality) : "Bataan";
  const sortIcon = (key: string) => sort === key ? (direction === "asc" ? <ArrowUp size={13} /> : <ArrowDown size={13} />) : null;
  const metrics = detail?.impact;

  return <div className="impact-workspace space-y-4">
    <SpatialHeaderFilters>
      <label className="space-y-1 text-xs font-medium">Year<select className={`${control} block w-full`} value={filters.year} onChange={e => filter("year", e.target.value)}><option value="">All years</option>{metadata?.years.map(y => <option key={y}>{y}</option>)}</select></label>
      <label className="space-y-1 text-xs font-medium">Municipality<select className={`${control} block w-full`} value={filters.municipality_psgc} onChange={e => filter("municipality_psgc", e.target.value)}><option value="">All municipalities</option>{municipalities.map(m => <option key={m.municipality_psgc} value={m.municipality_psgc}>{m.municipality}</option>)}</select></label>
      <label className="space-y-1 text-xs font-medium">Barangay<select className={`${control} block w-full`} value={filters.barangay_psgc} onChange={e => filter("barangay_psgc", e.target.value)}><option value="">All barangays</option>{barangays.map(b => <option key={b.barangay_psgc} value={b.barangay_psgc}>{b.barangay}{!filters.municipality_psgc ? `, ${b.municipality}` : ""}</option>)}</select></label>
      <label className="space-y-1 text-xs font-medium">Synthetic cause<select className={`${control} block w-full`} value={filters.cause_category} onChange={e => filter("cause_category", e.target.value)}><option value="">All causes</option>{metadata?.causes.map(c => <option key={c}>{c}</option>)}</select></label>
    </SpatialHeaderFilters>
    <EnvironmentalProfile onCauseChange={cause => filter("cause_category", cause)} filters={filters} area={selectedArea} />
    {error && <div role="alert" className="rounded-lg border border-destructive/40 p-4 text-sm">{error} <button className={button} onClick={() => setRetry(x => x + 1)}>Retry</button></div>}
    <section className="min-w-0 space-y-3" aria-label="Outage map workspace">
        <div className="overflow-hidden rounded-lg border bg-card">
          {(!selected || busy || (detail && !metrics) || mapError) && <div className="border-b bg-muted/30 px-4 py-2 text-xs" role={mapError ? "alert" : "status"}>{mapError ?? (busy ? (detail ? "Loading next outage… The previous outage remains visible." : "Loading the selected event and connected feeder…") : detail ? "No defensible connected fault source. The dashed outline shows the original event barangay only." : "Select an outage in the event table to show its modeled fault source and affected network.")}{mapError && <button className={button} onClick={() => setRetry(x => x + 1)}>Reload selected impact</button>}</div>}
          <div className="relative isolate xl:h-[820px]">
            <div className="relative h-[480px] sm:h-[560px] xl:absolute xl:inset-0 xl:h-full"><div ref={container} className="network-map h-full w-full" />
              <div className="absolute bottom-7 right-16 z-10 flex max-w-[calc(100%-5rem)] flex-wrap items-center gap-1 rounded-md border bg-card/95 p-1 shadow-sm">
                <label className="flex min-w-0 items-center text-xs"><span className="sr-only">Background</span><select className={"h-9 min-w-0 w-44 rounded border bg-background px-2 text-xs focus-visible:outline-2"} value={basemap} onChange={e => { setBasemap(e.target.value as MapBackground); setBasemapError(false); }}><option value="street">Street map (muted)</option><option value="color">Color street map</option><option value="satellite">Satellite imagery</option><option value="plain">Plain background</option></select></label>
                <button className={"inline-flex h-9 items-center justify-center gap-1 rounded border bg-background px-2 text-xs hover:bg-muted focus-visible:outline-2 disabled:opacity-50"} disabled={!metrics || busy || !ready} onClick={() => viewer.current?.fitImpact()}><Focus size={16} aria-hidden />Fit to impact</button>
                {basemapError && <p role="status" className="text-xs text-muted-foreground">Background unavailable. Showing plain background; select a background to retry.</p>}
              </div>
            </div>
            <div className="pointer-events-none space-y-3 p-3 xl:static xl:space-y-0 xl:p-0">
              <section className={`impact-event-panel pointer-events-auto flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border bg-card/95 shadow-lg xl:absolute xl:left-3 xl:top-3 xl:w-[min(42%,560px)] ${eventsOpen ? "xl:max-h-[calc(100%-4rem)]" : ""}`} aria-label="Synthetic outage event table">
                <h2 className={eventsOpen ? "border-b" : ""}><button type="button" aria-label={eventsOpen ? "Collapse outage events" : "Expand outage events"} aria-expanded={eventsOpen} aria-controls={eventsContentId} onClick={() => setEventsOpen(open => !open)} className="flex min-h-9 w-full items-center justify-between gap-3 px-2 py-1 text-left hover:bg-muted/50 focus-visible:outline-2 focus-visible:-outline-offset-2"><span className="font-semibold">Outage events</span><span className="ml-auto text-xs font-normal text-muted-foreground" role="status">{tableBusy ? "Updating…" : `${rows?.total.toLocaleString() ?? 0} matches`}</span><ChevronDown aria-hidden="true" className={`size-4 shrink-0 ${eventsOpen ? "" : "-rotate-90"}`} /></button></h2>
                <div id={eventsContentId} hidden={!eventsOpen} className={eventsOpen ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
                <div className="min-h-0 flex-1 overflow-auto overscroll-contain" aria-busy={tableBusy}>
                  <table className="w-full min-w-[500px] text-left text-xs leading-4"><caption className="sr-only">Select an outage to inspect its simulated infrastructure impact. All times are Asia/Manila.</caption>
                    <thead className="sticky top-0 z-10 border-b bg-card text-xs"><tr>{[["start_time", "Outage Start"], ["barangay", "Location"], ["duration_minutes", "Duration"]].map(([key, label]) => <th key={key} scope="col" aria-sort={sort === key ? (direction === "asc" ? "ascending" : "descending") : "none"}><button className="flex min-h-8 w-full items-center gap-1 px-2 font-medium focus-visible:outline-2" onClick={() => sortBy(key)}>{label}{sortIcon(key)}</button></th>)}<th scope="col" className="px-2 py-1">Cause</th></tr></thead>
                    <tbody className={tableBusy ? "opacity-50" : ""}>{rows?.events.map(event => <tr key={event.outage_id} aria-selected={selected === event.outage_id} onMouseEnter={() => warmEvent(event.outage_id)} onMouseLeave={() => clearTimeout(prefetchTimer.current)} onClick={() => { if (!tableBusy) chooseEvent(event.outage_id); }} className={`cursor-pointer border-b last:border-0 hover:bg-muted/50 ${selected === event.outage_id ? "bg-accent outline-2 -outline-offset-2 outline-ring" : ""}`}>
                      <td className="px-2 py-1"><button disabled={tableBusy} className="min-h-8 text-left focus-visible:outline-2" onFocus={() => warmEvent(event.outage_id)} onBlur={() => clearTimeout(prefetchTimer.current)} onClick={e => { e.stopPropagation(); chooseEvent(event.outage_id); }} aria-label={`Inspect outage starting ${manilaTime(event.start_time)}, ${event.barangay}, ${event.municipality}`} aria-pressed={selected === event.outage_id}><span className="block text-xs">{manilaTime(event.start_time)}</span></button></td>
                      <td className="px-2 py-1"><span className="block font-medium">{event.barangay}</span><span className="block text-xs text-muted-foreground">{event.municipality}</span></td>
                      <td className="whitespace-nowrap px-2 py-1 tabular-nums">{event.duration_minutes.toFixed(1)} min</td>
                      <td className="px-2 py-1"><span className="block text-xs">{event.cause_category}</span></td>
                    </tr>)}</tbody>
                  </table>
                  {!tableBusy && !error && !rows?.events.length && <p className="p-8 text-center text-sm text-muted-foreground">No events match these filters.</p>}
                </div>
                <div className="flex items-center justify-between gap-2 border-t px-2 py-1 text-xs"><button className="inline-flex min-h-8 items-center justify-center rounded-md border bg-background px-2 text-xs hover:bg-muted focus-visible:outline-2 disabled:opacity-50" disabled={tableBusy || page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button><span>Page {page} of {rows?.pages ?? 1}</span><button className="inline-flex min-h-8 items-center justify-center rounded-md border bg-background px-2 text-xs hover:bg-muted focus-visible:outline-2 disabled:opacity-50" disabled={tableBusy || page >= (rows?.pages ?? 1)} onClick={() => setPage(p => p + 1)}>Next</button></div>
                </div>
              </section>
              <div className="impact-detail-panel pointer-events-auto overflow-auto overscroll-contain rounded-lg border bg-card/95 p-4 shadow-lg xl:absolute xl:right-3 xl:top-40 2xl:top-3 xl:max-h-[calc(100%-8rem)] xl:w-[min(30%,400px)]" role="region" aria-label="Selected outage infrastructure impact" aria-busy={busy}>
          <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">{detail ? `${detail.event.barangay}, ${detail.event.municipality}` : "Infrastructure impact"}</h2><span className="flex items-center gap-2 text-xs text-muted-foreground" role="status">{busy && <LoaderCircle className="size-4 animate-spin" aria-hidden />}{busy ? (detail ? "Loading next outage…" : "Loading impact…") : detail ? (metrics ? "Mapped synthetic scenario" : "Unresolved assignment") : "Select an outage row"}</span></div>
          {busy && detail && <p className="mt-2 text-xs text-muted-foreground" role="status">Showing the previous outage while the next outage loads.</p>}
          {detail && <dl className="mt-4 grid grid-cols-2 gap-3">
            <div className="col-span-2"><dt className="text-[11px] text-muted-foreground">Outage Start (Manila)</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{manilaTime(detail.event.start_time)}</dd></div>
            <div><dt className="text-[11px] text-muted-foreground">Duration</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{detail.event.duration_minutes.toFixed(1)} min</dd></div>
            <div><dt className="text-[11px] text-muted-foreground">Cause</dt><dd className="mt-1 text-lg font-semibold">{detail.event.cause_category}</dd></div>
          </dl>}
          {metrics && <dl className="mt-4 grid grid-cols-2 gap-3">{[["Distribution lines", `${metrics.affected_line_length_km.toFixed(2)} km`], ["Line segments", metrics.affected_line_segment_count.toLocaleString()], ["Poles losing supply", metrics.affected_pole_count.toLocaleString()], ["Transformers", metrics.affected_transformer_count.toLocaleString()]].map(([label, value]) => <div key={label}><dt className="text-[11px] text-muted-foreground">{label}</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{value}</dd></div>)}</dl>}
          {metrics?.large_modeled_impact && <p className="mt-3 text-xs">Large modeled impact flagged for review. This may reflect feeder-wide loss in the simplified radial model.</p>}
          {metrics?.no_downstream_transformers && <p className="mt-3 text-xs">This valid isolated branch has no downstream modeled transformers.</p>}
        </div>
            </div>
          </div>
        </div>
    </section>
    <WeatherContext key={selected ?? "unselected"} outageId={selected} />
  </div>;
}
