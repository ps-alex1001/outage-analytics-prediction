"use client";
import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { getVisualizationColors } from "@/lib/visualization-theme";
const Chart = dynamic(() => import("echarts-for-react"), { ssr: false });
const API = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
type Filters = { year: string; municipality_psgc: string; barangay_psgc: string; cause_category: string };
type Ramp = { color_stops: [number, string][] };
type Environment = { area: string; basis: string; missing_note: string; terrain: string; statistics: Record<string, number | null>; legend: { ndvi: Ramp; slope: Ramp; combined: { palette_rows_low_to_high_slope_columns_low_to_high_ndvi: string[][] } } };
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
const categories = ["Equipment / Technical", "Other / Unknown", "Vegetation / Tree Contact", "Weather / Storm"];
export default function EnvironmentalProfile({ filters, area }: { filters: Filters; area: string }) {
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
  const slices = categories.map((name, i) => ({ name, value: causes.data?.causes.find(c => c.name === name)?.value ?? 0,
    itemStyle: { color: colors?.chart.series[i], opacity: filters.cause_category && filters.cause_category !== name ? 0.3 : 1, decal: { symbol: ["circle", "rect", "triangle", "diamond"][i], dashArrayX: [1, 3], dashArrayY: [2, 5], color: colors?.chart.background } } }));
  return <section aria-label="Environmental context and outage profile" className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,45fr)_minmax(0,25fr)_minmax(0,30fr)]">
    <article className={card}><h2 className="font-semibold">Environmental Raster Viewer</h2><p className="text-xs text-muted-foreground">{area} · NDVI 2025 · Static terrain</p>
      <div className="flex gap-2" aria-label="Raster layers unavailable">{["NDVI", "Slope", "Combined"].map(layer => <button key={layer} disabled className="min-h-9 rounded border px-3 text-xs opacity-50">{layer}</button>)}</div>
      <div className="flex min-h-40 flex-1 items-center justify-center rounded border border-dashed bg-muted/30 p-4 text-center text-sm text-muted-foreground">Environmental raster map placeholder<br />Raster layers are not connected. Controls are disabled.</div>
      {environment.data ? <div className="grid gap-3 sm:grid-cols-2"><div className="space-y-2">{ramp("ndvi")}{ramp("slope")}</div><div className="text-xs"><p>Combined: slope ↑ · NDVI →</p><div className="my-1 grid w-24 grid-cols-3">{[...environment.data.legend.combined.palette_rows_low_to_high_slope_columns_low_to_high_ndvi].reverse().flatMap((row, r) => row.map((color, c) => <span key={`${r}-${c}`} className="flex h-6 items-center justify-center border border-background text-[10px]" style={{ background: color, color: r === 0 ? "white" : "#171717" }}>{`N${c+1}S${3-r}`}</span>))}</div><p>NDVI: &lt;0.3 · 0.3–&lt;0.6 · ≥0.6</p><p>Slope: &lt;5° · 5–&lt;15° · ≥15°</p><p className="mt-1 text-muted-foreground">Descriptive classes, not outage risk.</p></div></div> : state(environment)}
    </article>
    <article className={card}><h2 className="font-semibold">Outage Causes</h2><p className="text-xs text-muted-foreground">{area} · {filters.year || "All historical years"} · Simulated events</p>
      {!causes.data ? state(causes) : <><div className="relative h-52">{total > 0 && colors && <Chart option={{ animation: false, tooltip: { trigger: "item", confine: true, formatter: "{b}: {c} events ({d}%)" }, series: [{ type: "pie", radius: ["58%", "82%"], label: { show: false }, data: slices }], aria: { enabled: true } }} style={{ height: "100%" }} notMerge />}
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center"><strong className="text-2xl tabular-nums">{total.toLocaleString()}</strong><span className="text-xs text-muted-foreground">{total ? "simulated events" : "No events"}</span></div></div><ul className="space-y-2 text-xs">{slices.map(slice => <li key={slice.name} className="flex justify-between gap-2"><span>{slice.name}</span><span className="tabular-nums">{slice.value.toLocaleString()}</span></li>)}</ul><p className="text-xs text-muted-foreground">All four causes shown; the selected cause is emphasized.</p></>}
    </article>
    <article className={card}><h2 className="font-semibold">Environmental Statistics</h2><p className="text-xs text-muted-foreground">{area} · Independent of outage year</p>
      {!environment.data ? state(environment) : <><dl className="divide-y">{[["Mean NDVI (2025)", "mean_ndvi_2025", ""], ["Mean slope", "mean_slope_deg", "°"], ["Maximum elevation", "elevation_max_m", " m"], ["High NDVI + steep slope", "high_ndvi_steep_pct", "%"]].map(([label, key, unit]) => <div key={key} className="flex justify-between gap-3 py-3 text-sm"><dt>{label}</dt><dd className="tabular-nums">{environment.data!.statistics[key] == null ? "Unavailable" : `${environment.data!.statistics[key]!.toFixed(key === "mean_ndvi_2025" ? 3 : 1)}${unit}`}</dd></div>)}<div className="flex justify-between gap-3 py-3 text-sm"><dt>Elevation range</dt><dd>{environment.data.statistics.elevation_min_m == null ? "Unavailable" : `${environment.data.statistics.elevation_min_m.toFixed(1)}–${environment.data.statistics.elevation_max_m?.toFixed(1)} m`}</dd></div></dl><p className="text-xs text-muted-foreground">{environment.data.terrain}. {environment.data.basis}</p><p className="text-xs text-muted-foreground">{environment.data.missing_note} High/steep: NDVI ≥0.6 and slope ≥15°; percentage of jointly valid area.</p></>}
    </article>
  </section>;
}
