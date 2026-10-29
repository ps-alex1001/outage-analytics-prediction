"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { getVisualizationColors } from "@/lib/visualization-theme";
import { manilaTime, scoreText } from "@/lib/forecast";
import { controls, differenceSummary, differenceText, nextPlaybackFrame, runScenario, scenarioMapScores, zeroAdjustments } from "@/lib/scenario";
import type { Adjustments, DisplayMode, ScenarioMode, ScenarioResult, WeatherKey } from "@/lib/scenario";
import { relativeScoreScale } from "@/lib/relative-score";
import type { ScenarioPalette } from "./scenario-map";

const Map = dynamic(() => import("./scenario-map"), { ssr: false, loading: () => <p role="status">Loading scenario map…</p> });
const Chart = dynamic(() => import("echarts-for-react"), { ssr: false, loading: () => <p role="status">Loading weather timelines…</p> });
const panel = "min-w-0 rounded-xl border bg-card p-4 sm:p-5";
const button = "min-h-11 rounded-md border px-3 text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-40";

export default function ScenarioExplorer() {
  const [adjustments, setAdjustments] = useState<Adjustments>({ ...zeroAdjustments });
  const [mode, setMode] = useState<ScenarioMode>("constant");
  const [display, setDisplay] = useState<DisplayMode>("scenario");
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hour, setHour] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [selection, setSelection] = useState<string | null>(null);
  const [palette, setPalette] = useState<ScenarioPalette | null>(null);
  const [colorblind, setColorblind] = useState(false);
  const [expired, setExpired] = useState(false);
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    const update = () => {
      const c = getVisualizationColors();
      setPalette({ ramp: display === "difference"
        ? ["#2166ac", resolvedTheme === "dark" ? "#cbd5e1" : "#f7f7f7", "#b2182b"]
        : colorblind ? ["#deebf7", "#6baed6", "#08519c"] : [c.map.low, c.map.medium, c.map.high],
        missing: c.map.noData, text: c.chart.text, grid: c.chart.grid,
        background: c.map.background, boundary: c.map.boundary });
    };
    const frame = requestAnimationFrame(update);
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [resolvedTheme, colorblind, display]);

  useEffect(() => {
    if (!playing || dirty || expired || !result) return;
    const timer = window.setTimeout(() => {
      const next = nextPlaybackFrame(hour);
      setHour(next.hour); setPlaying(next.playing);
    }, 1000 / speed);
    return () => clearTimeout(timer);
  }, [playing, hour, speed, dirty, expired, result]);

  useEffect(() => {
    if (!result) return;
    const timeout = window.setTimeout(() => { setExpired(true); setPlaying(false); },
      Math.max(0, Date.parse(result.weather.retrieved_at) + 3600000 - Date.now()));
    return () => clearTimeout(timeout);
  }, [result]);

  const batchDifferences = useMemo(() => result ? differenceSummary(result.points) : null, [result]);
  const scoreScale = useMemo(() => relativeScoreScale(result?.points.flatMap(p => [p.baseline_status === "experimental" ? p.baseline_score : null, p.scenario_status === "experimental" ? p.scenario_score : null]) ?? []), [result]);
  const view = useMemo(() => {
    if (!result) return null;
    const intervals = [...new Set(result.points.map(p => p.prediction_cutoff))].sort();
    const current = result.points.filter(p => p.prediction_cutoff === intervals[hour]);
    const scores = scenarioMapScores(expired ? [] : current, display);
    const selected = current.find(p => p.barangay_psgc === selection) ?? current[0];
    const summaries = (["baseline", "scenario", "difference"] as const).map(key => {
      const values = expired ? [] : current.map(p => key === "difference" ? p.difference : p[`${key}_score`]).filter((v): v is number => v !== null && Number.isFinite(v));
      return { key, mean: values.length ? values.reduce((a, b) => a + b, 0) / values.length : null, count: values.length };
    });
    // Means of distinct source locations, not a population-weighted province forecast.
    const weather = (Object.keys(controls) as WeatherKey[]).map(key => ({ key, rows: intervals.map(t => {
      const rows = result.weather_hourly.filter(w => w.prediction_cutoff === t && w.location_id.startsWith(key === "cape" ? "cape:" : "weather:"));
      const mean = (kind: "baseline" | "scenario") => {
        const values = rows.map(w => w[kind][key]).filter((v): v is number => v !== null && Number.isFinite(v));
        return { value: values.length ? values.reduce((a, b) => a + b, 0) / values.length : null, count: values.length };
      };
      return { baseline: mean("baseline"), scenario: mean("scenario") };
    }) }));
    return { intervals, current, scores, selected, summaries, weather };
  }, [result, hour, selection, display, expired]);

  function changed() { setPlaying(false); setDirty(true); }
  async function run() {
    setPlaying(false); setLoading(true); setError(null); setDirty(true);
    try {
      const response = await runScenario(mode, adjustments);
      setResult(response); setHour(0); setExpired(false); setDirty(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to run simulation."); }
    finally { setLoading(false); }
  }
  function slider(key: WeatherKey) {
    const c = controls[key];
    return <label key={key} className="block space-y-2 text-sm"><span className="flex justify-between gap-3"><span>{c.label}</span><output className="tabular-nums">{adjustments[key] > 0 ? "+" : ""}{adjustments[key]} {c.unit}</output></span>
      <input type="range" aria-label={`${c.label} adjustment`} min={c.min} max={c.max} step={c.step} value={adjustments[key]} onChange={e => { changed(); setAdjustments(a => ({ ...a, [key]: Number(e.target.value) })); }} className="min-h-8 w-full accent-primary" /></label>;
  }

  return <div className="space-y-5">
    <p className="max-w-4xl text-sm leading-6 text-muted-foreground">Explore model sensitivity to hypothetical weather across all 237 barangays. Outputs are experimental synthetic-outage onset model scores for (t, t+1h], not validated operational outage probabilities. Score differences are not established causal effects.</p>
    <section className={panel}><h2 className="mb-3 font-semibold">Province-wide weather adjustments</h2>
      <fieldset disabled={loading} className="space-y-4"><legend className="sr-only">Scenario settings</legend>
        <label className="flex flex-wrap items-center gap-3 text-sm">Simulation mode<select value={mode} onChange={e => { changed(); setMode(e.target.value as ScenarioMode); }} className="min-h-11 rounded-md border bg-background px-3"><option value="constant">Constant Adjustment</option><option value="storm">Storm Evolution</option></select></label>
        <p className="text-xs text-muted-foreground">Additive changes to each location’s existing forecast. Storm values are peak adjustments with a deterministic midpoint peak. Past weather stays unchanged. Wind is converted from km/h to the model’s m/s inputs.</p>
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">{(Object.keys(controls) as WeatherKey[]).filter(k => k !== "cape").map(slider)}</div>
        <details><summary className="cursor-pointer py-2 text-sm">Advanced · CAPE</summary><div className="max-w-md py-2">{slider("cape")}</div></details>
        <p className="text-xs text-muted-foreground">Values are bounded at zero for rain, wind, gusts and CAPE, 0–100% humidity, 0–1 m³/m³ soil moisture and −90 to 60°C. Modified gusts cannot fall below wind speed. Missing values remain missing; experiment ranges do not guarantee support in the training data.</p>
        <div className="flex gap-2"><button className={button} onClick={() => { changed(); setAdjustments({ ...zeroAdjustments }); setMode("constant"); }}>Reset</button><button className={`${button} bg-primary text-primary-foreground`} onClick={run}>{loading ? "Running simulation…" : "Run Simulation"}</button></div>
      </fieldset>
    </section>
    {error && <p role="alert" className={`${panel} text-destructive`}>{error} Check model readiness and weather availability, then try again.</p>}
    {dirty && result && <p role="status" className={panel}>Previous results are outdated for the current settings. Run Simulation to update them. Playback is paused.</p>}
    {expired && <p role="alert" className={panel}>This snapshot is now over one hour old. Scores are unavailable; run a new simulation with fresh weather.</p>}
    {!result && !loading && <p className={panel}>Set weather adjustments and run a simulation to compare all 24 hourly intervals.</p>}
    {result && view && <>
      <section className={panel}>
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold">Target interval {hour + 1} of 24</h2><p className="mt-1 text-sm">({manilaTime(view.current[0].target_start)}, {manilaTime(view.current[0].target_end)}] PHT</p></div>
          <div className="flex flex-wrap items-center gap-2"><button className={button} disabled={hour === 0} onClick={() => { setPlaying(false); setHour(h => h - 1); }}>Previous hour</button><button className={button} disabled={dirty || expired || loading} onClick={() => { if (!playing && hour === 23) setHour(0); setPlaying(p => !p); }}>{playing ? "Pause" : hour === 23 ? "Replay" : "Play"}</button><button className={button} disabled={hour === 23} onClick={() => { setPlaying(false); setHour(h => h + 1); }}>Next hour</button><label className="text-sm">Speed <select aria-label="Playback speed" className="min-h-11 rounded border bg-background px-2" value={speed} onChange={e => setSpeed(Number(e.target.value))}><option value={.5}>0.5×</option><option value={1}>1×</option><option value={2}>2×</option></select></label></div></div>
        <input aria-label="Simulation hour" aria-valuetext={`Hour ${hour + 1}, ${manilaTime(view.intervals[hour])} PHT`} type="range" min={0} max={23} step={1} value={hour} onChange={e => { setPlaying(false); setHour(Number(e.target.value)); }} className="my-3 min-h-8 w-full accent-primary" />
        <p className="text-xs text-muted-foreground">Start is exclusive; end is inclusive. All times Asia/Manila (UTC+08:00). {result.timing_note}</p>
      </section>
      <div className="grid gap-3 sm:grid-cols-3">{view.summaries.map(s => <section key={s.key} className={panel}><h3 className="text-sm capitalize">{s.key} · mean available score</h3><p className="my-2 text-2xl tabular-nums">{scoreText(s.mean)}</p><p className="text-xs text-muted-foreground">{s.count} available / {view.current.length - s.count} unavailable</p></section>)}</div>
      <section className={panel}><div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">Province-wide scenario map</h2><div className="flex flex-wrap items-center gap-3"><label className="text-sm">Display <select aria-label="Map display" className="min-h-11 rounded border bg-background px-2" value={display} onChange={e => setDisplay(e.target.value as DisplayMode)}><option value="baseline">Baseline</option><option value="scenario">Scenario</option><option value="difference">Difference</option></select></label><label className="flex min-h-11 items-center gap-2 text-sm"><input type="checkbox" checked={colorblind} onChange={e => setColorblind(e.target.checked)} />Colorblind-friendly score scale</label></div></div>
        {palette && <Map relativeScale={scoreScale} scores={view.scores} selected={view.selected?.barangay_psgc ?? null} onSelect={setSelection} palette={palette} difference={display === "difference"} differenceExtent={batchDifferences?.extent ?? null} />}
        {!view.scores.size && <p role="status" className="mt-2 text-sm">No available scores for this frame.</p>}
        {display === "difference" && view.scores.size > 0 && [...view.scores.values()].every(v => v === 0) && <p role="status" className="mt-2 text-sm">All available differences in this selected interval are zero. Use the timeline to inspect other hours. The first interval uses unchanged weather inputs.</p>}
        <p className="mt-2 text-xs text-muted-foreground">Baseline and Scenario share one 0–100% relative color scale across both full 24-hour batches, with a 5% range margin. Raw scores remain unchanged. Difference uses symmetric limits from the largest absolute valid change across this entire 24-hour batch, fixed during playback: blue indicates a decrease, red an increase. Colors show relative changes; inspect the numeric limits for their magnitude.</p>
        {batchDifferences && !expired && <div className="mt-3 space-y-1 text-xs tabular-nums"><p>Full-batch differences · Minimum {differenceText(batchDifferences.min)} · Median {differenceText(batchDifferences.median)} · Maximum {differenceText(batchDifferences.max)}</p><p>{batchDifferences.positive} positive · {batchDifferences.negative} negative · {batchDifferences.unchanged} unchanged · {batchDifferences.missing} unavailable</p>{batchDifferences.extent === 0 && <p role="status">All valid score differences are exactly zero. Difference colors remain neutral.</p>}{batchDifferences.extent !== null && batchDifferences.extent > 0 && batchDifferences.extent < .000001 && <p role="status">All changes are smaller than 0.000001 score units; the color scale magnifies these tiny differences for inspection.</p>}</div>}
      </section>
      <section className={panel}><h2 className="mb-3 font-semibold">Inspect a barangay</h2><label className="sr-only" htmlFor="scenario-barangay">Barangay detail</label><select id="scenario-barangay" className="min-h-11 w-full rounded border bg-background px-3 sm:max-w-md" value={view.selected?.barangay_psgc ?? ""} onChange={e => setSelection(e.target.value)}>{view.current.map(p => <option key={p.barangay_psgc} value={p.barangay_psgc}>{p.barangay} · {p.municipality}</option>)}</select>
        {view.selected && <div className="mt-3 space-y-2 text-sm"><p>PSGC {view.selected.barangay_psgc} · Baseline {scoreText(expired ? null : view.selected.baseline_score)} · Scenario {scoreText(expired ? null : view.selected.scenario_score)} · Difference {scoreText(expired ? null : view.selected.difference)}</p><p className="text-xs text-muted-foreground">Cutoff {view.selected.prediction_cutoff} · Last input label {view.selected.last_weather_label} · Lead {view.selected.lead_hours.toFixed(2)} h</p><p className="text-xs text-muted-foreground">Missing baseline features: {view.selected.baseline_missing_features.join(", ") || "none"}. Missing scenario features: {view.selected.scenario_missing_features.join(", ") || "none"}.</p></div>}
      </section>
      <section className={panel}><h2 className="font-semibold">Weather input evolution</h2><p className="my-2 text-xs text-muted-foreground">Solid: scenario; dashed: baseline. Mean across available distinct source locations (CAPE uses its own cells), not a barangay-weighted average. The vertical line follows the selected target interval; values belong to its last input weather hour.</p>
        <div className="grid gap-4 md:grid-cols-2">{view.weather.map(w => <div key={w.key} className="min-w-0"><h3 className="text-sm font-medium">{controls[w.key].label} · {w.key === "humidity" ? "%" : controls[w.key].unit}</h3><Chart style={{ height: 180 }} notMerge option={{ animation: false, backgroundColor: "transparent", textStyle: { color: palette?.text }, grid: { top: 20, bottom: 40, left: 55, right: 15 }, tooltip: { trigger: "axis" }, xAxis: { type: "category", data: view.intervals.map((_, i) => `H${i + 1}`), axisLabel: { color: palette?.text } }, yAxis: { type: "value", axisLabel: { color: palette?.text }, splitLine: { lineStyle: { color: palette?.grid } } }, series: (["baseline", "scenario"] as const).map(kind => ({ name: kind, type: "line", connectNulls: false, showSymbol: false, data: w.rows.map(r => r[kind].value), lineStyle: { type: kind === "baseline" ? "dashed" : "solid" }, itemStyle: { color: kind === "baseline" ? "#6b9dc2" : "#e98538" }, markLine: kind === "scenario" ? { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: palette?.text }, data: [{ xAxis: hour }] } : undefined })) }} />
          <p className="text-xs text-muted-foreground">Selected input hour: {manilaTime(new Date(Date.parse(view.intervals[hour]) - 3600000).toISOString())} PHT · {w.rows[hour].scenario.count} available locations</p>
          <details className="text-xs"><summary className="cursor-pointer py-2">Hourly values</summary><div className="max-h-48 overflow-auto"><table className="w-full text-left"><caption className="sr-only">{controls[w.key].label} by target interval</caption><thead><tr><th>Hour</th><th>Baseline</th><th>Scenario</th><th>Locations</th></tr></thead><tbody>{w.rows.map((row, i) => <tr key={i} aria-current={hour === i ? "true" : undefined}><td>H{i + 1}</td><td>{row.baseline.value?.toFixed(3) ?? "Unavailable"}</td><td>{row.scenario.value?.toFixed(3) ?? "Unavailable"}</td><td>{row.baseline.count} / {row.scenario.count}</td></tr>)}</tbody></table></div></details>
        </div>)}</div>
      </section>
      <details className={`${panel} text-xs`}><summary className="cursor-pointer py-2 font-medium">Snapshot and experiment provenance</summary><div className="mt-3 space-y-2 break-all"><p>Issued {result.issued_at} · Retrieved {result.weather.retrieved_at}</p><p>Snapshot {result.weather.snapshot_id} · Weather model {result.weather.model}</p><p>Frozen model {result.model_id}</p><p>Mode {result.mode} · Adjustment start {result.adjustment_start}</p><p>Exact model run initialization/publication timestamps are unavailable from this weather product. Forecast-driven inputs differ from historical model evaluation inputs. No live active-outage feed is available.</p><p>{result.weather.attribution}</p><pre className="max-h-64 overflow-auto whitespace-pre-wrap">{JSON.stringify({ adjustments: result.adjustments, intensity_profile: result.intensity_profile, clipping_counts: result.clipping_counts }, null, 2)}</pre></div></details>
    </>}
  </div>;
}

