"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ECharts } from "echarts";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { LoaderCircle } from "lucide-react";
import { fetchImpact, manilaTime } from "@/lib/outage-impacts";
import { getVisualizationColors } from "@/lib/visualization-theme";
import { WEATHER_TRACKS, weatherChartOption, weatherValue, type WeatherContextData } from "@/lib/weather-context";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false,
  loading: () => <p className="p-4 text-sm text-muted-foreground" role="status">Preparing weather charts…</p> });

export default function WeatherContext({ outageId }: { outageId: string }) {
  const group = useId();
  const disconnectCharts = useRef<(() => void) | null>(null);
  const { resolvedTheme } = useTheme();
  const [result, setResult] = useState<{ id: string; data?: WeatherContextData; error?: string } | null>(null);
  const [retry, setRetry] = useState(0);
  const [visual, setVisual] = useState<{ chart: ReturnType<typeof getVisualizationColors>["chart"]; outage: string } | null>(null);

  const connectChart = useCallback((chart: ECharts) => {
    void import("echarts").then(({ connect, disconnect }) => {
      if (chart.isDisposed()) return;
      chart.group = group;
      connect(group);
      disconnectCharts.current = () => disconnect(group);
    });
  }, [group]);
  useEffect(() => () => { disconnectCharts.current?.(); }, []);

  useEffect(() => {
    const frame = requestAnimationFrame(() => setVisual({
      chart: getVisualizationColors().chart,
      outage: getComputedStyle(document.documentElement).getPropertyValue("--impact-loss").trim(),
    }));
    return () => cancelAnimationFrame(frame);
  }, [resolvedTheme]);

  useEffect(() => {
    const abort = new AbortController();
    fetchImpact<WeatherContextData>(`events/${encodeURIComponent(outageId)}/weather`, abort.signal)
      .then(data => { if (!abort.signal.aborted) setResult({ id: outageId, data }); })
      .catch(error => { if (!abort.signal.aborted) setResult({ id: outageId, error: error instanceof Error ? error.message : "Weather request failed" }); });
    return () => abort.abort();
  }, [outageId, retry]);

  // Key the result as well as the mounted component: old data cannot flash on a
  // new selection, even before the old request's cleanup runs.
  const current = result?.id === outageId ? result : null;
  const data = current?.data;
  const charts = useMemo(() => data && visual ? WEATHER_TRACKS.map(track => ({
    track, option: weatherChartOption(data, visual.chart, visual.outage, track),
  })) : [], [data, visual]);
  const loading = !current;
  const fields = WEATHER_TRACKS.flatMap(track => track.fields.map((field, i) => ({ field, name: track.names[i] })));

  return <section className="min-w-0 overflow-hidden rounded-lg border bg-card" aria-labelledby="weather-context-title" aria-busy={loading}>
    <div className="space-y-2 border-b p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="weather-context-title" className="font-semibold">Weather Context</h2>
        <span className="text-xs text-muted-foreground">Hourly · Asia/Manila (UTC+8)</span>
      </div>
      <p className="break-all text-xs text-muted-foreground">{outageId} · 6 hours before start → outage duration → 3 hours after end</p>
      {data && <>
        <p className="text-xs text-muted-foreground">{data.source.match === "municipality"
          ? `Historical municipal weather: ${data.source.municipality} (${data.source.latitude?.toFixed(5)}, ${data.source.longitude?.toFixed(5)}). Municipalities may share weather coordinates.`
          : "No matching municipality in the historical weather source."}</p>
        <p className="text-xs text-muted-foreground">{data.era5.latitude !== null
          ? `ERA5: nearest ${data.era5.grid_degrees}° grid cell (${data.era5.latitude.toFixed(2)}, ${data.era5.longitude?.toFixed(2)}) to the municipal weather point. Coarser grid values are not barangay measurements.`
          : `ERA5: ${data.era5.reason ?? "No matching grid cell available"}.`}</p>
        {data.era5.unavailable_variables.length > 0 && <p className="text-xs text-muted-foreground">Absent from this ERA5 export: {data.era5.unavailable_variables.join(", ")}. {data.measurements.surface_pressure_hpa.available && "The pressure chart uses the existing municipal CSV (hPa)."}</p>}
        {data.era5.reason && data.era5.latitude !== null && <p role="status" className="text-xs text-muted-foreground">{data.era5.reason}.</p>}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-2"><span aria-hidden className="h-3 w-7 rounded-sm" style={{ background: "linear-gradient(90deg, rgba(220,38,38,.025), rgba(220,38,38,.18), rgba(220,38,38,.025))" }} />Outage period</span>
          <span>Start: {manilaTime(data.outage.start)}</span><span>End: {manilaTime(data.outage.end)}</span>
        </div>
      </>}
    </div>
    {loading && <div role="status" className="flex min-h-40 items-center justify-center gap-2 p-6 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />Loading selected event’s weather…</div>}
    {current?.error && <div role="alert" className="p-4 text-sm"><p>Unable to load weather. {current.error}</p><button className="mt-3 min-h-11 rounded-md border px-4 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring" onClick={() => { setResult(null); setRetry(r => r + 1); }}>Retry weather</button></div>}
    {data?.status === "missing" && <p role="status" className="p-6 text-sm text-muted-foreground">No weather measurements are available for this location and time window. Missing values have not been filled.</p>}
    {data && data.status !== "missing" && <>
      {data.status === "partial" && <p role="status" className="px-4 pt-3 text-xs text-muted-foreground">Some hours or measurements are unavailable. Gaps remain empty; missing soil layers are not substituted.</p>}
      <div className="grid min-w-0 grid-cols-1 gap-3 p-3 md:grid-cols-2">
        {charts.map(({ track, option }) => {
          const missing = track.fields.every(field => data.points.every(point => point[field] === null));
          const source = data.measurements[track.fields[0]].source === "era5" ? "ERA5 grid" : "Municipal CSV";
          return <figure key={track.fields[0]} className="min-w-0 rounded-md border">
            <figcaption className="h-20 space-y-0.5 px-3 pt-3">
              <h3 className="text-xs font-medium">{track.title}</h3>
              <p className="text-xs text-muted-foreground">{track.note}</p>
              <p className="text-[11px] text-muted-foreground">{source} · Hourly</p>
            </figcaption>
            <div className="relative h-[154px]">
              <ReactECharts option={option} notMerge onChartReady={connectChart} style={{ height: 154, width: "100%" }} opts={{ renderer: "svg" }} />
              {missing && <p role="status" className="pointer-events-none absolute inset-x-16 top-12 text-center text-xs text-muted-foreground">No measurements available</p>}
            </div>
          </figure>;
        })}
      </div>
      <details className="border-t px-4 py-2">
        <summary className="min-h-8 cursor-pointer text-xs font-medium focus-visible:outline-2">Hourly data and units</summary>
        <div className="max-h-64 overflow-auto pb-2">
          <table className="w-full whitespace-nowrap text-left text-xs tabular-nums">
            <caption className="sr-only">Selected outage historical weather. All timestamps are Asia/Manila. No data indicates a missing source measurement.</caption>
            <thead><tr><th scope="col" className="p-2">Time (Manila)</th>{fields.map(({ field, name }) => <th key={field} scope="col" className="p-2">{name} ({data.measurements[field].unit})</th>)}</tr></thead>
            <tbody>{data.points.map(point => <tr key={point.time} className="border-t"><th scope="row" className="p-2 font-normal">{manilaTime(point.time)}</th>{fields.map(({ field }) => <td key={field} className="p-2">{weatherValue(point[field])}</td>)}</tr>)}</tbody>
          </table>
        </div>
      </details>
    </>}
  </section>;
}
