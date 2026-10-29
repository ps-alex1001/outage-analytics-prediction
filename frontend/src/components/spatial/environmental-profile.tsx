"use client";
import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import type { Environment, RasterKind } from "@/lib/environment";
import { getVisualizationColors } from "@/lib/visualization-theme";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Info } from "lucide-react";
const EnvironmentalRasterMap = dynamic(() => import("@/components/spatial/environmental-raster-map"), { ssr: false, loading: () => <div className="min-h-80 flex-1" role="status">Loading map…</div> });
const Chart = dynamic(() => import("echarts-for-react"), { ssr: false });
const API = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
type Filters = { year: string; municipality_psgc: string; barangay_psgc: string; cause_category: string };
type Causes = { total_events: number; causes: { name: string; value: number }[] };
// Keep each cause consistent across selections, with six distinct categorical colors.
const causeColors: Record<string, string> = {
  Weather: "#0072b2", "Equipment Failure": "#d55e00", "Tree contact": "#009e73",
  Supply: "#cc79a7", Other: "#797037", Animal: "#56b4e9",
};
function CombinedLegend({ palette }: { palette: string[][] }) {
  const vegetation = ["Low vegetation", "Moderate vegetation", "High vegetation"];
  const ndviRanges = ["NDVI below 0.3", "NDVI 0.3 to below 0.6", "NDVI 0.6 or higher"];
  const slopes = ["Gentle terrain", "Moderate terrain", "Steep terrain"];
  const slopeRanges = ["slope below 5°", "slope 5° to below 15°", "slope 15° or higher"];
  return <div className="text-xs">
    <div className="flex items-center gap-2">
      <span className="rotate-180 font-medium [writing-mode:vertical-rl]">Slope →</span>
      <div className="grid grid-cols-3 gap-px" aria-label="Vegetation increases to the right; slope increases upward">
        {[...palette].reverse().flatMap((row, r) => row.map((color, c) => {
          const slopeIndex = palette.length - 1 - r;
          const label = `${vegetation[c]}, ${slopes[slopeIndex].toLowerCase()}`;
          return <Tooltip key={`${r}-${c}`}><TooltipTrigger aria-label={label} className="size-8 cursor-help focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-offset-2" style={{ backgroundColor: color }} /><TooltipContent side="left"><span>{label}. {ndviRanges[c]}; {slopeRanges[slopeIndex]}.</span></TooltipContent></Tooltip>;
        }))}
      </div>
    </div>
    <p className="mt-2 pl-6 text-center font-medium">NDVI →</p>
  </div>;
}
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
  const card = "h-[560px] min-h-0 min-w-0 overflow-auto rounded-lg border bg-card p-4 flex flex-col gap-3 xl:h-full";
  const state = (resource: { error?: string; retry: () => void }) => resource.error
    ? <div role="alert" className="text-sm">{resource.error}<button className="ml-2 min-h-11 rounded border px-3" onClick={resource.retry}>Retry</button></div>
    : <p role="status" className="text-sm text-muted-foreground">Loading…</p>;
  const ramp = (name: "ndvi" | "slope") => {
    const stops = environment.data?.legend[name].color_stops;
    if (!stops) return null;
    const low = stops[0][0], high = stops.at(-1)![0];
    return <div className="w-44 max-w-full"><p className="text-xs">{name === "ndvi" ? `NDVI ${environment.data?.ndvi_year} (unitless)` : "Slope (degrees)"}</p><div className="my-1 h-2 rounded" style={{ background: `linear-gradient(90deg, ${stops.map(([v, c]) => `${c} ${(v-low)/(high-low)*100}%`).join(",")})` }} /><p className="flex justify-between text-xs"><span>{low}</span><span>{high}{name === "slope" ? "°+" : ""}</span></p></div>;
  };
  const total = causes.data?.total_events ?? 0;
  const slices = (causes.data?.causes ?? []).map(cause => ({ ...cause,
    itemStyle: { color: causeColors[cause.name] ?? "#737373", opacity: filters.cause_category && filters.cause_category !== cause.name ? 0.25 : 1 } }));
  const formatStat = (key: string, digits = 1, unit = "") => {
    const value = environment.data?.statistics[key];
    return value == null ? "Unavailable" : `${value.toFixed(digits)}${unit}`;
  };
  return <section aria-label="Environmental context and outage profile" className="grid items-stretch gap-4 xl:h-[560px] xl:grid-cols-[minmax(0,45fr)_minmax(0,25fr)_minmax(0,30fr)]">
    <article className={card}>
      <EnvironmentalRasterMap profile={environment.data} profileError={environment.error} retryProfile={environment.retry} kind={layer} api={API} area={area}
        controls={<div className="flex flex-wrap gap-1 rounded-md border bg-background p-1 shadow-sm" role="group" aria-label="Environmental raster layer">{([['ndvi', 'NDVI'], ['slope', 'Slope'], ['combined', 'Combined']] as const).map(([kind, label]) => <button type="button" key={kind} aria-pressed={layer === kind} disabled={!environment.data} onClick={() => setLayer(kind)} className={`min-h-11 rounded px-2 text-xs focus-visible:outline-2 focus-visible:outline-offset-2 disabled:opacity-50 ${layer === kind ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'}`}>{label}</button>)}</div>}
        legend={environment.data && <div aria-label={`${layer === "ndvi" ? "NDVI" : layer === "slope" ? "Slope" : "Combined"} legend`}>{layer !== "combined" ? ramp(layer) : <CombinedLegend palette={environment.data.legend.combined.palette_rows_low_to_high_slope_columns_low_to_high_ndvi} />}</div>}
      />
    </article>
    <article className={card}><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">Outage Causes</h2>{filters.cause_category && <button type="button" className="min-h-9 rounded-md border px-2 text-xs hover:bg-muted focus-visible:outline-2" onClick={() => onCauseChange("")}>Clear cause</button>}</div><p className="text-xs text-muted-foreground">{area} · {filters.year || "All historical years"} · Simulated events</p>
      {!causes.data ? state(causes) : <><div className="relative h-[250px] shrink-0">{total > 0 && colors && <Chart option={{ animation: false, color: colors.chart.series, tooltip: { trigger: "item", confine: true, formatter: "{b}: {c} events ({d}%)" }, series: [{ type: "pie", radius: ["55%", "80%"], center: ["50%", "50%"], padAngle: 2, itemStyle: { borderRadius: 5, borderColor: colors.chart.background, borderWidth: 2 }, label: { show: false }, labelLine: { show: false }, emphasis: { scale: true, scaleSize: 6 }, data: slices }], aria: { enabled: true } }} style={{ height: "100%" }} onEvents={{ click: (event: { name?: string }) => { if (event.name) onCauseChange(filters.cause_category === event.name ? "" : event.name); } }} notMerge />}
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center"><strong className="text-2xl tabular-nums">{total.toLocaleString()}</strong><span className="text-xs text-muted-foreground">{total ? "outages" : "No outages"}</span></div></div>
      <ul aria-label="Outage cause legend">{slices.map(cause => <li key={cause.name}>
        <button type="button" aria-pressed={filters.cause_category === cause.name} onClick={() => onCauseChange(filters.cause_category === cause.name ? "" : cause.name)} className={`flex min-h-7 w-full items-center gap-2 rounded-md px-2 text-left text-xs hover:bg-muted focus-visible:outline-2 ${filters.cause_category === cause.name ? "bg-muted ring-1 ring-border" : ""}`}>
          <span aria-hidden="true" className="size-2.5 shrink-0 rounded-sm" style={{ backgroundColor: cause.itemStyle.color }} />
          <span className="min-w-0 flex-1">{cause.name}</span>
          <span className="tabular-nums text-muted-foreground">{cause.value.toLocaleString()} · {total ? (cause.value / total * 100).toFixed(1) : "0.0"}%</span>
        </button>
      </li>)}</ul></>}
    </article>
    <article className={card}><h2 className="font-semibold">Environmental Statistics</h2>
      {!environment.data ? state(environment) : <div className="flex flex-1 flex-col gap-4">
        <dl className="grid grid-cols-1 gap-3">
          <div className="rounded-md border bg-muted/30 p-3"><dt className="text-xs text-muted-foreground"><Tooltip><TooltipTrigger className="inline-flex cursor-help items-center gap-1 rounded text-left focus-visible:outline-2 focus-visible:outline-offset-2">Mean NDVI ({environment.data.ndvi_year})<Info aria-hidden="true" className="size-3.5 shrink-0" /></TooltipTrigger><TooltipContent><span>NDVI (Normalized Difference Vegetation Index) measures vegetation greenness from satellite imagery. Values range from −1 to 1; higher values usually indicate denser, greener vegetation. This is the mean across measured pixels in the selected area.</span></TooltipContent></Tooltip></dt><dd className="mt-2 text-2xl font-semibold tabular-nums">{formatStat("mean_ndvi_2025", 3)}</dd><dd className="mt-1 text-xs text-muted-foreground">Vegetation index · −1 to 1</dd></div>
          <div className="rounded-md border bg-muted/30 p-3"><dt className="text-xs text-muted-foreground">Mean slope</dt><dd className="mt-2 text-2xl font-semibold tabular-nums">{formatStat("mean_slope_deg", 1, "°")}</dd><dd className="mt-1 text-xs text-muted-foreground">Surface terrain</dd></div>
          <div className="rounded-md border p-3"><dt className="text-xs text-muted-foreground">Elevation range</dt><dd className="mt-2 text-xl font-semibold tabular-nums">{environment.data.statistics.elevation_min_m == null || environment.data.statistics.elevation_max_m == null ? "Unavailable" : <>{formatStat("elevation_min_m")}<span className="mx-2 text-muted-foreground">to</span>{formatStat("elevation_max_m", 1, " m")}</>}</dd><dd className="mt-1 text-xs text-muted-foreground">Minimum to maximum · DSM-derived</dd></div>
          <div className="rounded-md border p-3"><dt className="text-xs text-muted-foreground">High vegetation + steep terrain</dt><dd className="mt-2 text-2xl font-semibold tabular-nums">{formatStat("high_ndvi_steep_pct", 1, "%")}</dd><dd className="mt-1 text-xs text-muted-foreground">Of jointly measured area · NDVI ≥0.6 and slope ≥15°</dd></div>
        </dl>
      </div>}
    </article>
  </section>;
}
