"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ECharts } from "echarts";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { fetchImpact, manilaTime } from "@/lib/outage-impacts";
import { getVisualizationColors } from "@/lib/visualization-theme";
import { WEATHER_TRACKS, weatherChartOption, weatherValue, type WeatherContextData } from "@/lib/weather-context";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false,
  loading: () => <div className="h-[154px] animate-pulse bg-muted/50 motion-reduce:animate-none" role="status" aria-label="Preparing weather chart" /> });

export default function WeatherContext({ outageId }: { outageId: string | null }) {
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
    if (!outageId) return;
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
  const loading = !!outageId && !current;
  const fields = WEATHER_TRACKS.flatMap(track => track.fields.map((field, i) => ({ field, name: track.names[i] })));

  return <section className="min-w-0 overflow-hidden rounded-lg border bg-card" aria-labelledby="weather-context-title" aria-busy={loading}>
    <div className="flex h-20 flex-col justify-center gap-2 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="weather-context-title" className="font-semibold">Weather Context</h2>
        <span className="text-xs text-muted-foreground">Hourly · Asia/Manila (UTC+8)</span>
      </div>
      {!outageId && <p className="text-xs text-muted-foreground">Select an outage to view weather.</p>}
    </div>
    {current?.error && <div role="alert" className="p-4 text-sm"><p>Unable to load weather. {current.error}</p><button className="mt-3 min-h-11 rounded-md border px-4 hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring" onClick={() => { setResult(null); setRetry(r => r + 1); }}>Retry weather</button></div>}
    {data?.status === "missing" && <p role="status" className="p-6 text-sm text-muted-foreground">No weather measurements are available for this location and time window. Missing values have not been filled.</p>}
    {<>

      <div className="grid min-w-0 grid-cols-1 gap-3 p-3 md:grid-cols-2">
        {WEATHER_TRACKS.map(track => {
          const option = charts.find(chart => chart.track === track)?.option;
          const missing = !!data && track.fields.every(field => data.points.every(point => point[field] === null));

          return <figure key={track.fields[0]} className="min-w-0 rounded-md border">
            <figcaption className="min-h-9 px-3 pb-1 pt-3">
              <div className="flex flex-wrap items-baseline gap-x-2"><h3 className="text-xs font-medium">{track.title}</h3>{track.fields.length === 1 && <span className="text-xs text-muted-foreground">{track.note}</span>}</div>
            </figcaption>
            <div className="relative h-[154px]">
              {option ? <ReactECharts option={option} notMerge onChartReady={connectChart} style={{ height: 154, width: "100%" }} opts={{ renderer: "svg" }} /> : <div className="h-[154px] animate-pulse px-10 pb-8 pt-2 motion-reduce:animate-none" role="status" aria-label={loading ? `Loading ${track.title}` : `${track.title} awaiting weather data`}><div className="h-full rounded bg-muted/60" /></div>}
              {missing && <p role="status" className="pointer-events-none absolute inset-x-16 top-12 text-center text-xs text-muted-foreground">No measurements available</p>}
            </div>
          </figure>;
        })}
      </div>
      {data && <details className="border-t px-4 py-2">
        <summary className="min-h-8 cursor-pointer text-xs font-medium focus-visible:outline-2">Hourly data and units</summary>
        <div className="max-h-64 overflow-auto pb-2">
          <table className="w-full whitespace-nowrap text-left text-xs tabular-nums">
            <caption className="sr-only">Selected outage historical weather. All timestamps are Asia/Manila. No data indicates a missing source measurement.</caption>
            <thead><tr><th scope="col" className="p-2">Time (Manila)</th>{fields.map(({ field, name }) => <th key={field} scope="col" className="p-2">{name} ({data.measurements[field].unit})</th>)}</tr></thead>
            <tbody>{data.points.map(point => <tr key={point.time} className="border-t"><th scope="row" className="p-2 font-normal">{manilaTime(point.time)}</th>{fields.map(({ field }) => <td key={field} className="p-2">{weatherValue(point[field])}</td>)}</tr>)}</tbody>
          </table>
        </div>
      </details>}
    </>}
  </section>;
}
