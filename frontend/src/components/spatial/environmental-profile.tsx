"use client";
import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import type { Environment, RasterKind } from "@/lib/environment";
import { getVisualizationColors } from "@/lib/visualization-theme";
const EnvironmentalRasterMap = dynamic(() => import("@/components/spatial/environmental-raster-map"), { ssr: false, loading: () => <div className="min-h-80 flex-1" role="status">Loading map…</div> });
const Chart = dynamic(() => import("echarts-for-react"), { ssr: false });
const API = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
type Filters = { year: string; municipality_psgc: string; barangay_psgc: string; cause_category: string };
type Causes = { total_events: number; causes: { name: string; value: number }[] };
function useResource<T>(url: string) {
  const [result, setResult] = useState<{ url: string; data?: T; error?: string } | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    fetch(url, { signal: abort.signal }).then(async response => {
      if (!response.ok) throw new Error("Unable to load this summary.");
      return response.json() as Promise<T>;
    }).then(data => { if (!abort.signal.aborted) setResult({ url, data }); })
      .catch(error => { if (!abort.signal.aborted) setResult({ url, error: error.message }); });
    return () => abort.abort();
  }, [url, retry]);
  return { ...(result?.url === url ? result : {}), retry: () => { setResult(null); setRetry(n => n + 1); } };
}

export default function EnvironmentalProfile({ filters, area, onCauseChange }: { filters: Filters; area: string; onCauseChange: (cause: string) => void }) {
  const [layer, setLayer] = useState<RasterKind>("ndvi");
  const geo = new URLSearchParams();
  if (filters.municipality_psgc) geo.set("municipality_psgc", filters.municipality_psgc);
  if (filters.barangay_psgc) geo.set("barangay_psgc", filters.barangay_psgc);
  const params = new URLSearchParams(geo);
  if (filters.year) params.set("year", filters.year);
  const environment = useResource<Environment>(`${API}/api/spatial/environment?${geo}`);
  const causes = useResource<Causes>(`${API}/api/overview/causes?${params}`);
  const { resolvedTheme } = useTheme();
  const [colors, setColors] = useState<ReturnType<typeof getVisualizationColors> | null>(null);
  useEffect(() => { const frame = requestAnimationFrame(() => setColors(getVisualizationColors())); return () => cancelAnimationFrame(frame); }, [resolvedTheme]);
  const card = "min-w-0 rounded-lg border bg-card p-4 flex flex-col gap-3";
  const state = (resource: { error?: string; retry: () => void }) => resource.error
    ? <div role="alert" className="text-sm">{resource.error}<button className="ml-2 min-h-11 rounded border px-3" onClick={resource.retry}>Retry</button></div>
    : <p role="status" className="text-sm text-muted-foreground">Loading…</p>;
  const ramp = (name: "ndvi" | "slope") => {
    const stops = environment.data?.legend[name].color_stops;
    if (!stops) return null;
    const low = stops[0][0], high = stops.at(-1)![0];
    return <div><p className="text-xs">{name === "ndvi" ? "NDVI 2025 (unitless)" : "Slope (degrees)"}</p><div className="my-1 h-2 rounded" style={{ background: `linear-gradient(90deg, ${stops.map(([v, c]) => `${c} ${(v-low)/(high-low)*100}%`).join(",")})` }} /><p className="flex justify-between text-xs"><span>{low}</span><span>{high}{name === "slope" ? "°+" : ""}</span></p></div>;
  };
  const total = causes.data?.total_events ?? 0;
  const slices = (causes.data?.causes ?? []).map(cause => ({ ...cause,
    itemStyle: { opacity: filters.cause_category && filters.cause_category !== cause.name ? 0.25 : 1 } }));
  return <section aria-label="Environmental context and outage profile" className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,45fr)_minmax(0,25fr)_minmax(0,30fr)]">
    <article className={card}><h2 className="font-semibold">Environmental Raster Viewer</h2>
      <div className="flex flex-wrap gap-2" role="group" aria-label="Environmental raster layer">{([['ndvi', 'NDVI'], ['slope', 'Slope'], ['combined', 'Combined']] as const).map(([kind, label]) => <button type="button" key={kind} aria-pressed={layer === kind} disabled={!environment.data} onClick={() => setLayer(kind)} className={`min-h-11 rounded-md border px-3 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50 ${layer === kind ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'}`}>{label}</button>)}</div>
      <EnvironmentalRasterMap profile={environment.data} profileError={environment.error} retryProfile={environment.retry} kind={layer} api={API} />
      {environment.data && <p className="text-xs text-muted-foreground">Joint measurement coverage: {environment.data.coverage.joint_valid_30m_pct?.toFixed(2) ?? 'Unavailable'}%. Missing cells and boundary edge gaps are transparent.</p>}
      {environment.data ? <div aria-label={`${layer === "ndvi" ? "NDVI" : layer === "slope" ? "Slope" : "Combined"} legend`}>{layer !== "combined" ? ramp(layer) : <div className="text-xs"><p>Combined: slope ↑ · NDVI →</p><div className="my-1 grid w-24 grid-cols-3">{[...environment.data.legend.combined.palette_rows_low_to_high_slope_columns_low_to_high_ndvi].reverse().flatMap((row, r) => row.map((color, c) => <span key={`${r}-${c}`} className="flex h-6 items-center justify-center border border-background text-[10px]" style={{ background: color, color: r === 0 ? "white" : "#171717" }}>{`N${c+1}S${3-r}`}</span>))}</div><p>NDVI: &lt;0.3 · 0.3–&lt;0.6 · ≥0.6</p><p>Slope: &lt;5° · 5–&lt;15° · ≥15°</p><p className="mt-1 text-muted-foreground">Descriptive classes, not outage risk.</p></div>}</div> : state(environment)}
    </article>
    <article className={card}><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">Outage Causes</h2>{filters.cause_category && <button type="button" className="min-h-9 rounded-md border px-2 text-xs hover:bg-muted focus-visible:outline-2" onClick={() => onCauseChange("")}>Clear cause</button>}</div><p className="text-xs text-muted-foreground">{area} · {filters.year || "All historical years"} · Simulated events</p>
      {!causes.data ? state(causes) : <><div className="relative h-[340px]">{total > 0 && colors && <Chart option={{ animation: false, color: colors.chart.series, tooltip: { trigger: "item", confine: true, formatter: "{b}: {c} events ({d}%)" }, series: [{ type: "pie", radius: ["34%", "60%"], center: ["50%", "50%"], padAngle: 2, itemStyle: { borderRadius: 5, borderColor: colors.chart.background, borderWidth: 2 }, avoidLabelOverlap: true, label: { show: true, position: "outside", alignTo: "edge", edgeDistance: 2, distanceToLabelLine: 5, color: colors.chart.text, fontSize: 11, lineHeight: 15, overflow: "break", formatter: "{b}\n{d}%" }, labelLine: { show: true, length: 10, length2: 6, lineStyle: { width: 1.2 } }, labelLayout: { moveOverlap: "shiftY", hideOverlap: true }, emphasis: { scale: true, scaleSize: 6 }, data: slices }], aria: { enabled: true } }} style={{ height: "100%" }} onEvents={{ click: (event: { name?: string }) => { if (event.name) onCauseChange(filters.cause_category === event.name ? "" : event.name); } }} notMerge />}
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center"><strong className="text-2xl tabular-nums">{total.toLocaleString()}</strong><span className="text-xs text-muted-foreground">{total ? "simulated events" : "No events"}</span></div></div></>}
    </article>
    <article className={card}><h2 className="font-semibold">Environmental Statistics</h2><p className="text-xs text-muted-foreground">{area} · Independent of outage year</p>
      {!environment.data ? state(environment) : <><dl className="divide-y">{[["Mean NDVI (2025)", "mean_ndvi_2025", ""], ["Mean slope", "mean_slope_deg", "°"], ["Maximum elevation", "elevation_max_m", " m"], ["High NDVI + steep slope", "high_ndvi_steep_pct", "%"]].map(([label, key, unit]) => <div key={key} className="flex justify-between gap-3 py-3 text-sm"><dt>{label}</dt><dd className="tabular-nums">{environment.data!.statistics[key] == null ? "Unavailable" : `${environment.data!.statistics[key]!.toFixed(key === "mean_ndvi_2025" ? 3 : 1)}${unit}`}</dd></div>)}<div className="flex justify-between gap-3 py-3 text-sm"><dt>Elevation range</dt><dd>{environment.data.statistics.elevation_min_m == null ? "Unavailable" : `${environment.data.statistics.elevation_min_m.toFixed(1)}–${environment.data.statistics.elevation_max_m?.toFixed(1)} m`}</dd></div></dl></>}
    </article>
  </section>;
}
