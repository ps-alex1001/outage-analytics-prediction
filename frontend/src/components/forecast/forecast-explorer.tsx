"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { getVisualizationColors } from "@/lib/visualization-theme";
import { intervalText, loadForecast, manilaTime, scoreAvailable, scoreText } from "@/lib/forecast";
import type { ForecastPalette } from "./forecast-map";

const ForecastMap = dynamic(() => import("./forecast-map"), { ssr: false, loading: () => <div className="h-[480px] animate-pulse rounded bg-muted" role="status">Loading map…</div> });
const Chart = dynamic(() => import("echarts-for-react"), { ssr: false, loading: () => <p role="status">Loading score timeline…</p> });
type Loaded = Awaited<ReturnType<typeof loadForecast>>;
const panel = "min-w-0 rounded-xl border bg-card p-4 sm:p-5";
const button = "min-h-11 rounded-md border px-3 text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-40";

export default function ForecastExplorer() {
  const pending = useRef<Promise<Loaded> | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [hour, setHour] = useState(0);
  const [selection, setSelection] = useState<string | null>(null);
  const [mapLevel, setMapLevel] = useState<"municipality" | "barangay">("barangay");
  const [palette, setPalette] = useState<ForecastPalette | null>(null);
  const [expired, setExpired] = useState(false);
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    let cancelled = false;
    // The same promise survives Strict Mode's effect replay: one inference request.
    pending.current ??= loadForecast();
    pending.current.then(value => { if (!cancelled) setLoaded(value); })
      .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Unable to load forecast."); });
    return () => { cancelled = true; };
  }, [attempt]);

  useEffect(() => {
    const update = () => {
      const c = getVisualizationColors();
      setPalette({ ramp: [c.map.low, c.map.medium, c.map.high],
        missing: c.map.noData, text: c.chart.text, grid: c.chart.grid,
        background: c.map.background, boundary: c.map.boundary });
    };
    const frame = requestAnimationFrame(update);
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [resolvedTheme]);

  const batch = loaded?.batch;
  useEffect(() => {
    if (!batch) return;
    // Stop treating an open tab's weather as current after the backend's 1-hour maximum age.
    const delay = Math.max(0, Date.parse(batch.weather.retrieved_at) + 3600000 - Date.now());
    const timer = setTimeout(() => setExpired(true), delay);
    return () => clearTimeout(timer);
  }, [batch]);

  const view = useMemo(() => {
    if (!batch) return null;
    const intervals = [...new Map(batch.points.map(p => [p.prediction_cutoff, p])).values()]
      .sort((a, b) => Date.parse(a.prediction_cutoff) - Date.parse(b.prediction_cutoff));
    const available = (p: typeof batch.points[number]) => !expired && scoreAvailable(batch, p);
    const allAvailable = batch.points.filter(available);
    const sortedScores = allAvailable.map(p => p.score!).sort((a,b) => a-b);
    const lowCut = sortedScores[Math.floor((sortedScores.length - 1) / 3)] ?? 0;
    const highCut = sortedScores[Math.floor((sortedScores.length - 1) * 2 / 3)] ?? 0;
    const maximum = Math.max(0, ...allAvailable.map(p => p.score!));
    const current = batch.points.filter(p => p.prediction_cutoff === intervals[hour]?.prediction_cutoff);
    const ranking = current.filter(available).sort((a, b) => b.score! - a.score! || a.barangay_psgc.localeCompare(b.barangay_psgc));
    const id = selection ?? batch.points[0]?.barangay_psgc;
    const series = intervals.map(t => batch.points.find(p => p.barangay_psgc === id && p.prediction_cutoff === t.prediction_cutoff));
    const point = series[hour];
    return { intervals, maximum, allAvailable, current, ranking, id, series, point, lowCut, highCut,
      scores: new Map(ranking.map(p => [p.barangay_psgc, p.score!])),
      chartScores: series.map(p => p && available(p) ? p.score : null) };
  }, [batch, hour, selection, expired]);

  const municipalityScores = new Map<string, number>();
  const municipalitySelections = new Map<string, string>();
  const groups = new Map<string, number[]>();
  for (const point of view?.ranking ?? []) {
    const values = groups.get(point.municipality_psgc) ?? [];
    values.push(point.score!); groups.set(point.municipality_psgc, values);
    if (!municipalitySelections.has(point.municipality_psgc)) municipalitySelections.set(point.municipality_psgc, point.barangay_psgc);
  }
  groups.forEach((values, id) => municipalityScores.set(id, values.reduce((sum, value) => sum + value, 0) / values.length));

  function retry() {
    pending.current = null; setLoaded(null); setError(null); setExpired(false); setHour(0); setSelection(null); setAttempt(n => n + 1);
  }

  if (error) return <section className={panel} role="alert"><h2 className="font-semibold">Forecast request failed</h2><p className="my-3 text-sm text-muted-foreground">{error}</p><button className={button} onClick={retry}>Retry forecast request</button></section>;
  if (!loaded) return <section className={`${panel} space-y-4`} role="status" aria-busy="true"><p>Checking model readiness and loading the current forecast…</p><div className="h-24 animate-pulse rounded bg-muted" /><div className="h-96 animate-pulse rounded bg-muted" /></section>;
  if (!batch || !view) return <section className={panel} role="status"><h2 className="font-semibold">Forecast backend is not ready</h2><p className="my-3 text-sm text-muted-foreground">{loaded.metadata.error ?? "The evaluated model or its required inputs are unavailable."}</p><button className={button} onClick={retry}>Check readiness again</button></section>;

  const interval = view.intervals[hour];
  const stale = expired || batch.weather.stale || batch.status === "stale_weather";
  return <div className="space-y-5">
    <p className="max-w-4xl text-sm text-muted-foreground">Research use only. These experimental scores concern synthetic outage onset during (t, t+1h]. They are not validated operational outage probabilities.</p>
    {stale && <div role="alert" className={`${panel} border-destructive`}><p className="font-semibold">Weather snapshot is stale — no valid forecast scores are displayed.</p><button className={`${button} mt-3`} onClick={retry}>Request a current forecast</button></div>}
    <section aria-label="Forecast batch summary" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <div className={panel}><p className="text-xs text-muted-foreground">Forecast issued · Asia/Manila</p><p className="mt-2 text-sm font-medium">{manilaTime(batch.issued_at)} PHT</p><p className="mt-1 text-xs text-muted-foreground">Weather retrieved {manilaTime(batch.weather.retrieved_at)} PHT</p></div>
      <div className={panel}><p className="text-xs text-muted-foreground">Available hourly scores · full batch</p><p className="mt-2 text-2xl font-semibold tabular-nums">{view.allAvailable.length.toLocaleString()}</p></div>
      <div className={panel}><p className="text-xs text-muted-foreground">Unavailable hourly scores · full batch</p><p className="mt-2 text-2xl font-semibold tabular-nums">{(batch.points.length - view.allAvailable.length).toLocaleString()}</p></div>
      <div className={panel}><p className="text-xs text-muted-foreground">Highest experimental model score · full batch</p><p className="mt-2 text-2xl font-semibold tabular-nums">{scoreText(view.allAvailable.length ? view.maximum : null)}</p></div>
    </section>

    <section className={`${panel} space-y-3`} aria-label="Forecast target interval">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><label htmlFor="forecast-hour" className="font-semibold">Target interval {hour + 1} of {view.intervals.length}</label><p className="mt-1 text-sm" aria-live="polite">{intervalText(interval)}</p></div><div className="flex gap-2"><button className={button} disabled={hour === 0} onClick={() => setHour(h => h - 1)}>Previous hour</button><button className={button} disabled={hour === view.intervals.length - 1} onClick={() => setHour(h => h + 1)}>Next hour</button></div></div>
      <input id="forecast-hour" type="range" min={0} max={view.intervals.length - 1} step={1} value={hour} onChange={e => setHour(Number(e.target.value))} aria-valuetext={intervalText(interval)} className="h-11 w-full cursor-pointer accent-current focus-visible:outline-2 focus-visible:outline-ring" />
      <p className="text-xs text-muted-foreground">Start is exclusive; end is inclusive. All times are Asia/Manila (UTC+08:00). {view.ranking.length} of {view.current.length} barangays have an available score for this interval.</p>
    </section>

    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(280px,1fr)]">
      <section className={panel}><div className="mb-4 flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">{mapLevel === "municipality" ? "Municipality mean score map" : "Barangay score map"}</h2><div role="group" aria-label="Map geography" className="flex gap-1 rounded-md border p-1">{(["municipality", "barangay"] as const).map(level => <button key={level} type="button" aria-pressed={mapLevel === level} onClick={() => setMapLevel(level)} className={`min-h-9 rounded px-3 text-xs focus-visible:outline-2 ${mapLevel === level ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}>{level === "municipality" ? "Municipality" : "Barangay"}</button>)}</div></div>{palette && <ForecastMap scores={mapLevel === "barangay" ? view.scores : municipalityScores} selected={mapLevel === "barangay" ? view.id ?? null : view.point?.municipality_psgc ?? null} onSelect={setSelection} palette={palette} level={mapLevel} municipalitySelections={municipalitySelections} />}</section>
      <section className={panel} aria-label="Barangay score ranking"><h2 className="font-semibold">Barangay ranking</h2><p className="mb-3 mt-1 text-xs text-muted-foreground">Selected interval · descending experimental score</p><p className="mb-3 text-xs text-muted-foreground">Relative score categories across this forecast batch: Low ≤{scoreText(view.lowCut)}, Medium ≤{scoreText(view.highCut)}, High &gt;{scoreText(view.highCut)}. These are not calibrated risk levels.</p>
        <div className="max-h-[490px] overflow-y-auto"><table className="w-full text-left text-sm"><caption className="sr-only">Available model scores. Select a barangay to inspect its hourly series.</caption><thead className="sticky top-0 bg-card"><tr><th scope="col" className="py-2">Barangay</th><th scope="col" className="py-2">Category</th><th scope="col" className="py-2 text-right">Score</th></tr></thead><tbody>{view.ranking.map((p, index) => <tr key={p.barangay_psgc} className={view.id === p.barangay_psgc ? "bg-muted" : ""}><td><button className="min-h-11 w-full rounded px-2 py-2 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring" aria-pressed={view.id === p.barangay_psgc} onClick={() => setSelection(p.barangay_psgc)}><span className="text-muted-foreground">{index + 1}. </span>{p.barangay}<span className="block text-xs text-muted-foreground">{p.municipality}</span></button></td><td className="text-xs"><span className="rounded border px-2 py-1">{p.score! <= view.lowCut ? "Low" : p.score! <= view.highCut ? "Medium" : "High"}</span></td><td className="pr-2 text-right font-mono text-xs">{scoreText(p.score)}</td></tr>)}</tbody></table>
          {!view.ranking.length && <p role="status" className="py-5 text-sm text-muted-foreground">No available scores for this interval.</p>}
        </div>
        <label className="mt-4 block text-xs font-medium" htmlFor="forecast-place">Inspect any barangay, including unavailable scores</label><select id="forecast-place" className="mt-2 min-h-11 w-full rounded border bg-card px-2 text-sm" value={view.id ?? ""} onChange={e => setSelection(e.target.value)}>{batch.points.filter(p => p.prediction_cutoff === view.intervals[0].prediction_cutoff).map(p => <option key={p.barangay_psgc} value={p.barangay_psgc}>{p.barangay} · {p.municipality}</option>)}</select>
      </section>
    </div>

    <section className={panel} aria-label="Selected barangay detail">
      <h2 className="font-semibold">{view.point?.barangay ?? "Selected barangay"} · 24-hour score timeline</h2>
      <p className="mt-1 text-sm text-muted-foreground">{view.point?.municipality} · PSGC {view.id} · selected score: {scoreText(view.point && view.scores.has(view.point.barangay_psgc) ? view.point.score : null)}</p>
      <p className="mt-3 text-xs text-muted-foreground">Experimental model score · uncalibrated, 0–1 scale</p>
      {palette && <Chart style={{ height: 280 }} option={{ animation: false, backgroundColor: "transparent", aria: { enabled: true },
        grid: { left: 20, right: 24, top: 35, bottom: 45, containLabel: true },
        tooltip: { trigger: "axis", renderMode: "richText", confine: true, formatter: (params: { dataIndex: number }[]) => {
          const index = params[0]?.dataIndex ?? 0; return `${intervalText(view.intervals[index])}\nExperimental model score: ${scoreText(view.chartScores[index])}`;
        } },
        xAxis: { type: "category", data: view.intervals.map(p => new Intl.DateTimeFormat("en-PH", { timeZone: "Asia/Manila", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(p.target_start))), axisLabel: { color: palette.text }, axisLine: { lineStyle: { color: palette.grid } } },
        yAxis: { type: "value", min: 0, axisLabel: { color: palette.text, formatter: (value: number) => value.toFixed(4) }, splitLine: { lineStyle: { color: palette.grid } } },
        series: [{ type: "line", name: "Experimental model score", data: view.chartScores, connectNulls: false, showSymbol: true, symbolSize: 5, lineStyle: { color: palette.text }, itemStyle: { color: palette.text }, markLine: { silent: true, symbol: "none", label: { show: false }, data: [{ xAxis: hour }] } }],
      }} notMerge />}
      <p className="text-xs text-muted-foreground">Gaps are unavailable scores, not zero. Chart labels show interval starts in PHT.</p>
      {view.point && <div className="mt-4 space-y-2 text-xs text-muted-foreground"><p>Cutoff: {view.point.prediction_cutoff} · Lead from issue: {view.point.lead_hours.toFixed(2)} hours · Last weather label: {view.point.last_weather_label}</p><p>Source status: {stale ? "stale_weather" : view.point.status} · Feature coverage: {view.point.feature_coverage * 100}%{view.point.missing_features.length ? ` · Missing: ${view.point.missing_features.join(", ")}` : ""}</p></div>}
      <details className="mt-4 text-xs"><summary className="cursor-pointer py-2 font-medium">Accessible hourly values and exact target timestamps</summary><div className="max-h-64 overflow-auto"><table className="w-full text-left"><thead><tr><th scope="col">Target interval (Asia/Manila)</th><th scope="col">Score</th></tr></thead><tbody>{view.intervals.map((p, i) => <tr key={p.prediction_cutoff}><td className="py-2">{intervalText(p)}</td><td>{scoreText(view.chartScores[i])}</td></tr>)}</tbody></table></div></details>
    </section>
    <details className={`${panel} text-xs text-muted-foreground`}><summary className="cursor-pointer font-medium text-foreground">Model and weather provenance</summary><div className="mt-3 space-y-2 break-words"><p>Model {batch.model_id} · {loaded.metadata.model?.training_rows.toLocaleString()} training rows · {loaded.metadata.model?.parity_rows_verified} reference predictions verified</p><p>{batch.weather.attribution} · {batch.weather.model} · {batch.weather.product}</p><p>Snapshot: {batch.weather.snapshot_id} · Retrieved: {batch.weather.retrieved_at} · Issued: {batch.issued_at}</p><p>Weather run initialized: {batch.weather.run_initialized_at ?? "Not exposed by API"} · Published: {batch.weather.published_at ?? "Not exposed by API"}</p><p>{batch.weather.source_shift}</p><p>Barangay weather mapping: {view.point?.weather_location_id} · CAPE mapping: {view.point?.cape_location_id}</p><p>Scores assume no active outage at the target cutoff. Forecast-weather compatibility remains unvalidated. {batch.notice}</p></div></details>
  </div>;
}



