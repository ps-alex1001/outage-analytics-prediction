"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { manilaTime, scoreText } from "@/lib/forecast";
import { relativeScoreScale } from "@/lib/relative-score";
import { nextPlaybackFrame } from "@/lib/scenario";
import { loadStormCatalog, loadStormReplay, municipalityReplayScores, replayScores, weatherMaximum } from "@/lib/storm-replay";
import type { ReplayBatch, StormCatalog } from "@/lib/storm-replay";
import { getVisualizationColors } from "@/lib/visualization-theme";
import type { ScenarioPalette, WeatherMapFrame } from "./scenario-map";

const Map = dynamic(() => import("./scenario-map"), { ssr: false, loading: () => <p role="status">Loading Bataan map…</p> });
const Chart = dynamic(() => import("echarts-for-react"), { ssr: false });
const Advanced = dynamic(() => import("./scenario-explorer"), { ssr: false });
const panel = "min-w-0 rounded-xl border bg-card p-4 sm:p-5";
const button = "min-h-11 rounded-md border px-3 text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-40";

export default function StormReplayExplorer() {
  const pending = useRef<Promise<StormCatalog> | null>(null);
  const [catalog, setCatalog] = useState<StormCatalog | null>(null);
  const [batch, setBatch] = useState<ReplayBatch | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hour, setHour] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [mode, setMode] = useState<"weather" | "score">("score");
  const [level, setLevel] = useState<"barangay" | "municipality">("municipality");
  const [metric, setMetric] = useState<"rainfall" | "wind">("rainfall");
  const [selection, setSelection] = useState<string | null>(null);
  const [colorblind, setColorblind] = useState(false);
  const [palette, setPalette] = useState<ScenarioPalette | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [expired, setExpired] = useState(false);
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    let cancelled = false;
    pending.current ??= loadStormCatalog();
    pending.current.then(value => { if (!cancelled) setCatalog(value); })
      .catch(reason => { if (!cancelled) setError(String(reason)); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const update = () => {
      const c = getVisualizationColors();
      setPalette({ ramp: colorblind ? resolvedTheme === "dark" ? ["#17344a", "#4388b5", "#c6e8ff"] : ["#deebf7", "#6baed6", "#08519c"] : [c.map.low, c.map.medium, c.map.high],
        missing: c.map.noData, text: c.chart.text, grid: c.chart.grid, background: c.map.background, boundary: c.map.boundary });
    };
    const frame = requestAnimationFrame(update);
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [colorblind, resolvedTheme]);

  useEffect(() => {
    if (!playing || !batch || expired) return;
    const timer = window.setTimeout(() => {
      const next = nextPlaybackFrame(hour); setHour(next.hour); setPlaying(next.playing);
    }, 1000 / speed);
    return () => clearTimeout(timer);
  }, [playing, hour, speed, batch, expired]);

  useEffect(() => {
    if (batch?.kind !== "synthetic_forecast" || !batch.provenance.weather) return;
    const timeout = window.setTimeout(() => { setExpired(true); setPlaying(false); },
      Math.max(0, Date.parse(batch.provenance.weather.retrieved_at) + 3600000 - Date.now()));
    return () => clearTimeout(timeout);
  }, [batch]);

  async function load(id: string) {
    setLoading(true); setPlaying(false); setError(null);
    try { const value = await loadStormReplay(id); setBatch(value); setHour(0); setExpired(false); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Replay could not load."); }
    finally { setLoading(false); }
  }

  const scoreScale = useMemo(() => relativeScoreScale(batch?.points.map(p => p.status === "experimental" ? p.score : null) ?? []), [batch]);
  const view = useMemo(() => {
    if (!batch) return null;
    const intervals = [...new Set(batch.points.map(p => p.prediction_cutoff))].sort();
    const rows = batch.points.filter(p => p.prediction_cutoff === intervals[hour]);
    const current = rows.find(p => p.barangay_psgc === selection) ?? rows[0];
    const scores = replayScores(rows, expired);
    const municipal = municipalityReplayScores(rows, expired);
    const weather = batch.weather_hourly.filter(w => w.prediction_cutoff === intervals[hour]);
    const weatherMap: WeatherMapFrame = { label: metric === "rainfall" ? "Archived input rainfall · mm/h" : "Archived input wind · km/h",
      maximum: weatherMaximum(batch, metric), points: batch.weather_locations.map(loc => {
        const row = weather.find(w => w.location_id === loc.id);
        const partial = row?.available_counts && row.municipal_series_count && row.available_counts[metric] < row.municipal_series_count;
        return { ...loc, name: `${loc.municipalities.join(" / ")}${partial ? ` · partial data (${row.available_counts![metric]}/${row.municipal_series_count} series)` : ""}`, value: row?.[metric] ?? null };
      }) };
    if (batch.kind === "synthetic_forecast") weatherMap.label = metric === "rainfall" ? "Synthetic forecast rainfall · mm/h" : "Synthetic forecast wind · km/h";
    const charts = (["rainfall", "wind", "gusts"] as const).map(key => ({ key, rows: intervals.map(t => {
      const values = batch.weather_hourly.filter(w => w.prediction_cutoff === t).map(w => w[key]).filter((v): v is number => v !== null && Number.isFinite(v));
      return { value: values.length ? values.reduce((a, b) => a + b, 0) / values.length : null, count: values.length };
    }) }));
    const track = batch.track.find(p => p.time === intervals[hour]);
    return { intervals, rows, current, scores, municipal, weatherMap, charts, track, weather,
      maximum: scores.size ? Math.max(...scores.values()) : null };
  }, [batch, hour, selection, metric, expired]);

  const cards = catalog?.events.filter(e => `${e.title} ${e.start}`.toLowerCase().includes(query.toLowerCase())) ?? [];
  return <div className="space-y-5">
    <div><h1 className="text-xl font-semibold">Storm Replay Simulator</h1><p className="mt-2 max-w-4xl text-sm leading-6 text-muted-foreground">Replay supplied 2025–2026 storm tracks with archived Bataan weather. Scores are retrospective experimental synthetic-outage onset scores for (t, t+1h], not validated historical forecasts or operational outage probabilities.</p></div>
    <section className={panel}><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">Choose a storm · 2025–2026</h2><label className="text-sm">Search storm ID or date <input className="ml-2 min-h-11 rounded border bg-background px-3" value={query} onChange={e => setQuery(e.target.value)} /></label></div>
      {!catalog && !error && <p role="status">Loading storm catalog…</p>}
      {catalog && <><p className="mb-3 text-xs text-muted-foreground">{catalog.notice} Cards are sorted by closest track approach to the Bataan reference (14.67°N, 120.47°E). Weather coverage ends June 2026.</p>
        <div className="grid max-h-[390px] gap-3 overflow-y-auto p-1 sm:grid-cols-2 lg:grid-cols-3">
          <button disabled={loading} aria-pressed={batch?.event.id === "forecasted"} onClick={() => load("forecasted")} className={`${panel} text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${batch?.event.id === "forecasted" ? "ring-2 ring-primary" : ""}`}><h3 className="font-semibold">Forecasted</h3><p className="my-1 text-xs font-medium">Synthetic storm scenario · next 24 intervals</p><p className="text-xs text-muted-foreground">Current forecast weather plus a deterministic storm-evolution preset. This is not a real named storm forecast.</p></button>
          {cards.map(e => <button key={e.id} disabled={loading || !e.available} aria-pressed={batch?.event.id === e.id} onClick={() => load(e.id)} className={`${panel} text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50 ${batch?.event.id === e.id ? "ring-2 ring-primary" : ""}`}><h3 className="text-sm font-semibold">{e.title}</h3><p className="mt-1 text-xs">{manilaTime(e.start)} PHT · {e.duration_hours.toFixed(0)} h track span</p><p className="mt-1 text-xs">{e.closest_reference_km.toFixed(1)} km closest to reference</p><p className="mt-2 text-xs text-muted-foreground">{e.weather_info}</p>{!e.available && <p className="mt-1 text-xs">{e.unavailable_reason}</p>}</button>)}
        </div><p className="mt-2 text-xs text-muted-foreground">{cards.length} historical storms match. Names were not supplied, so original storm IDs are preserved.</p></>}
    </section>
    {loading && <p role="status" className={panel}>Loading the weather window and its 74-hour lookback; computing the complete replay batch…</p>}
    {error && <div role="alert" className={`${panel} text-destructive`}><p>{error}</p>{!catalog && <button className={button} onClick={() => window.location.reload()}>Retry catalog</button>}</div>}
    {!batch && !loading && <p className={panel}>Select a storm card to load its 24-hour replay centered around the closest supplied track approach.</p>}
    {batch && view && <>
      <section className={panel}><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">{batch.event.title}</h2><p className="mt-1 text-sm">{batch.kind === "historical" ? "Historical replay · archived weather estimates" : "Forecasted · synthetic storm demonstration"}</p><p className="mt-2 max-w-4xl text-xs text-muted-foreground">{batch.notice}</p></div><span className="rounded border px-2 py-1 text-xs">24 target intervals · {batch.barangay_count} barangays</span></div>
        <p className="mt-3 text-sm">Window: {manilaTime(batch.event.window_start)} to {manilaTime(batch.event.window_end)} PHT</p>
        {view.track && <p className="mt-1 text-xs text-muted-foreground">Supplied storm center at target cutoff: {view.track.latitude?.toFixed(3)}°N, {view.track.longitude?.toFixed(3)}°E. These track values are separate from local weather inputs.</p>}
      </section>
      {expired && <p role="alert" className={panel}>The Forecasted weather snapshot is over one hour old; scores and synthetic weather are unavailable. Select Forecasted again to refresh.</p>}
      <section className={panel}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">Bataan storm progression</h2><div className="flex flex-wrap items-center gap-3"><label className="text-sm">Boundaries <select aria-label="Replay boundary level" value={level} onChange={e => setLevel(e.target.value as "barangay" | "municipality")} className="min-h-11 rounded border bg-background px-2"><option value="municipality">Municipality</option><option value="barangay">Barangay</option></select></label><label className="text-sm">Map <select aria-label="Replay map mode" value={mode} onChange={e => setMode(e.target.value as "weather" | "score")} className="min-h-11 rounded border bg-background px-2"><option value="weather">Weather Intensity</option><option value="score">Model Score</option></select></label>{mode === "weather" && <label className="text-sm">Measurement <select aria-label="Weather map measurement" value={metric} onChange={e => setMetric(e.target.value as "rainfall" | "wind")} className="min-h-11 rounded border bg-background px-2"><option value="rainfall">Rainfall</option><option value="wind">Wind speed</option></select></label>}<label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={colorblind} onChange={e => setColorblind(e.target.checked)} />Colorblind-friendly scale</label></div></div>
        {palette && <Map relativeScale={scoreScale} level={level} scores={level === "municipality" ? view.municipal.scores : view.scores} selected={(level === "municipality" ? view.current?.municipality_psgc : view.current?.barangay_psgc) ?? null} onSelect={id => {
          const point = view.rows.find(p => level === "municipality" ? p.municipality_psgc === id : p.barangay_psgc === id);
          if (point) setSelection(point.barangay_psgc);
        }} palette={palette} difference={false} differenceExtent={null} weather={mode === "weather" ? { ...view.weatherMap, points: view.weatherMap.points.map(p => ({ ...p, value: expired ? null : p.value })) } : null} />}
        {mode === "score" && level === "municipality" && view.current && <p className="mt-2 text-sm">{view.current.municipality} · Mean available barangay score {scoreText(view.municipal.scores.get(view.current.municipality_psgc))} · {view.municipal.groups.get(view.current.municipality_psgc)?.available} / {view.municipal.groups.get(view.current.municipality_psgc)?.total} barangays available. This mean is a display summary, not the probability of any municipal outage. Missing barangays are excluded; municipalities with no available scores remain unavailable.</p>}
        <p className="mt-2 text-xs text-muted-foreground">{mode === "weather" ? "Circles mark seven source coordinates shared by 12 municipalities. Historical display values average available co-located municipal series; model inference retains all 12 separately. No barangay weather is invented. Limits stay fixed over the full replay." : "Colors show relative model score (0–100%), scaled across all barangays and 24 hours with a 5% range margin. The same bounds apply to municipality means. Raw 0–1 scores remain unchanged; these percentages are not outage probabilities."}</p>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3"><div><p className="font-medium">Hour {hour + 1} of 24</p><p className="mt-1 text-sm">({manilaTime(view.rows[0].target_start)}, {manilaTime(view.rows[0].target_end)}] PHT</p></div><div className="flex flex-wrap gap-2"><button className={button} disabled={hour === 0 || loading} onClick={() => { setPlaying(false); setHour(h => h-1); }}>Previous hour</button><button className={button} disabled={loading || expired} onClick={() => { if (!playing && hour === 23) setHour(0); setPlaying(v => !v); }}>{playing ? "Pause" : hour === 23 ? "Replay" : "Play"}</button><button className={button} disabled={hour === 23 || loading} onClick={() => { setPlaying(false); setHour(h => h+1); }}>Next hour</button><label className="text-sm">Speed <select aria-label="Replay speed" className="min-h-11 rounded border bg-background px-2" value={speed} onChange={e => setSpeed(Number(e.target.value))}><option value={.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option></select></label></div></div>
        <input aria-label="Storm replay hour" aria-valuetext={`Hour ${hour+1}: ${manilaTime(view.intervals[hour])} PHT`} className="my-3 min-h-8 w-full accent-primary" type="range" min={0} max={23} step={1} value={hour} disabled={loading} onChange={e => { setPlaying(false); setHour(Number(e.target.value)); }} />
        <p className="text-xs text-muted-foreground">All times Asia/Manila. Target start is exclusive; end is inclusive. Map weather and charts use the last input hour: {manilaTime(view.rows[0].last_weather_label)} PHT, before cutoff. Playback reuses this loaded batch.</p>
      </section>
      <section className={panel}><h2 className="font-semibold">Hourly weather progression</h2><p className="my-2 text-xs text-muted-foreground">Means over available unique municipal source points; not station observations or population-weighted values. Gaps remain missing. Vertical indicators follow the selected interval.</p><div className="grid gap-4 lg:grid-cols-3">{view.charts.map(c => <div key={c.key} className="min-w-0"><h3 className="text-sm capitalize">{c.key} · {c.key === "rainfall" ? "mm/h" : "km/h"}</h3><Chart style={{ height: 200 }} notMerge option={{ animation: false, backgroundColor: "transparent", textStyle: { color: palette?.text }, grid: { top: 20, bottom: 35, left: 50, right: 15 }, tooltip: { trigger: "axis" }, xAxis: { type: "category", data: view.intervals.map((_, i) => `H${i+1}`), axisLabel: { color: palette?.text } }, yAxis: { type: "value", axisLabel: { color: palette?.text }, splitLine: { lineStyle: { color: palette?.grid } } }, series: [{ type: "line", connectNulls: false, data: c.rows.map(r => expired ? null : r.value), itemStyle: { color: palette?.ramp[1] }, markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: palette?.text }, data: [{ xAxis: hour }] } }] }} /><p className="text-xs text-muted-foreground">Selected hour: {expired ? "Unavailable" : c.rows[hour].value?.toFixed(2) ?? "Unavailable"} · {expired ? 0 : c.rows[hour].count} available source points</p><details className="text-xs"><summary className="cursor-pointer py-2">Hourly values</summary><div className="max-h-40 overflow-auto"><table className="w-full text-left"><thead><tr><th>Hour</th><th>Mean</th><th>Points</th></tr></thead><tbody>{c.rows.map((r,i) => <tr key={i}><td>H{i+1}</td><td>{expired ? "Unavailable" : r.value?.toFixed(3) ?? "Unavailable"}</td><td>{expired ? 0 : r.count}</td></tr>)}</tbody></table></div></details></div>)}</div></section>
      <section className={panel}><div className="flex flex-wrap justify-between gap-3"><h2 className="font-semibold">Barangay model-score details</h2><p className="text-sm tabular-nums">{view.scores.size} available · {view.rows.length-view.scores.size} unavailable · Maximum {scoreText(view.maximum)}</p></div><label className="mt-3 block text-sm">Barangay <select aria-label="Replay barangay" className="ml-2 min-h-11 max-w-full rounded border bg-background px-3" value={view.current?.barangay_psgc ?? ""} onChange={e => setSelection(e.target.value)}>{view.rows.map(p => <option key={p.barangay_psgc} value={p.barangay_psgc}>{p.barangay} · {p.municipality}</option>)}</select></label>{view.current && <div className="mt-3 space-y-2 text-sm"><p>PSGC {view.current.barangay_psgc} · Score {scoreText(expired ? null : view.current.score)}</p><p className="text-xs text-muted-foreground">Missing features: {view.current.missing_features.join(", ") || "none"}. Assumes no active outage at cutoff; no live outage feed is available.</p><p className="break-all text-xs text-muted-foreground">Cutoff {view.current.prediction_cutoff} · Last input {view.current.last_weather_label} · Weather {view.current.weather_location_id} · CAPE {view.current.cape_location_id}</p></div>}</section>
      <details className={`${panel} text-xs`}><summary className="cursor-pointer py-2 font-medium">Replay provenance and limitations</summary><p className="my-3 break-all">Frozen model: {batch.model_id}</p><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(batch.provenance, null, 2)}</pre></details>
    </>}
    <details className={panel} onToggle={e => { const open = e.currentTarget.open; setAdvanced(open); if (open) setPlaying(false); }}><summary className="cursor-pointer py-2 font-medium">Advanced · weather-adjustment experiments</summary>{advanced && <div className="mt-4"><Advanced /></div>}</details>
  </div>;
}


