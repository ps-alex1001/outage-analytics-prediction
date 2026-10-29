import type { ExpressionSpecification } from "maplibre-gl";

export const controls = {
  precipitation: { label: "Precipitation", unit: "mm/h", min: -50, max: 50, step: 1 },
  wind_speed: { label: "Wind speed", unit: "km/h", min: -100, max: 100, step: 2 },
  wind_gusts: { label: "Wind gusts", unit: "km/h", min: -150, max: 150, step: 2 },
  temperature: { label: "Temperature", unit: "°C", min: -15, max: 15, step: .5 },
  humidity: { label: "Relative humidity", unit: "percentage points", min: -50, max: 50, step: 1 },
  soil_moisture: { label: "Soil moisture · 7–28 cm", unit: "m³/m³", min: -.4, max: .4, step: .01 },
  cape: { label: "CAPE", unit: "J/kg", min: -3000, max: 3000, step: 100 },
} as const;
export type WeatherKey = keyof typeof controls;
export type Adjustments = Record<WeatherKey, number>;
export const zeroAdjustments: Adjustments = { precipitation: 0, wind_speed: 0, wind_gusts: 0, temperature: 0, humidity: 0, soil_moisture: 0, cape: 0 };
export type ScenarioMode = "constant" | "storm";
export type DisplayMode = "baseline" | "scenario" | "difference";
/** Playback only changes a local frame; it has no inference dependency. */
export function nextPlaybackFrame(hour: number) {
  const next = Math.min(hour + 1, 23);
  return { hour: next, playing: next < 23 };
}
export type ScenarioPoint = {
  barangay_psgc: string; barangay: string; municipality: string; municipality_psgc: string;
  weather_location_id: string; cape_location_id: string; prediction_cutoff: string;
  target_start: string; target_end: string; last_weather_label: string; lead_hours: number;
  baseline_score: number | null; scenario_score: number | null; difference: number | null;
  baseline_status: string; scenario_status: string;
  baseline_missing_features: string[]; scenario_missing_features: string[];
};
export type ScenarioResult = {
  issued_at: string; model_id: string; hours: number; barangay_count: number; mode: ScenarioMode;
  adjustments: Adjustments; notice: string; timing_note: string; adjustment_start: string;
  intensity_profile: number[]; clipping_counts: Record<string, Record<string, number>>;
  weather: { snapshot_id: string; retrieved_at: string; stale: boolean; attribution: string;
    model: string; run_initialized_at: string | null; published_at: string | null };
  points: ScenarioPoint[];
  weather_hourly: { location_id: string; prediction_cutoff: string; weather_label: string;
    baseline: Record<WeatherKey, number | null>; scenario: Record<WeatherKey, number | null> }[];
};

export function scenarioMapScores(points: ScenarioPoint[], display: DisplayMode) {
  const scores = new Map<string, number>();
  points.forEach(p => {
    const value = display === "difference" ? p.difference : p[`${display}_score`];
    const valid = display === "difference"
      ? p.baseline_status === "experimental" && p.scenario_status === "experimental"
      : p[`${display}_status`] === "experimental";
    if (valid && value !== null && Number.isFinite(value)) scores.set(p.barangay_psgc, value);
  });
  return scores;
}

/** Entire batch only: the extent must not change with the playback frame. */
export function differenceSummary(points: ScenarioPoint[]) {
  const values = points.filter(p => p.baseline_status === "experimental" && p.scenario_status === "experimental")
    .map(p => p.difference).filter((v): v is number => v !== null && Number.isFinite(v)).sort((a, b) => a - b);
  const min = values[0] ?? null, max = values.at(-1) ?? null;
  const mid = Math.floor(values.length / 2);
  return { extent: min === null || max === null ? null : Math.max(Math.abs(min), Math.abs(max)),
    min, max, median: values.length ? (values[mid] + values[Math.floor((values.length - 1) / 2)]) / 2 : null,
    positive: values.filter(v => v > 0).length, negative: values.filter(v => v < 0).length,
    unchanged: values.filter(v => v === 0).length, missing: points.length - values.length };
}

export function differenceText(value: number | null) {
  if (value === null) return "Unavailable";
  return value !== 0 && Math.abs(value) < .000001 ? value.toExponential(4) : Number(value.toPrecision(6)).toString();
}

export function scenarioFillColor(difference: boolean, extent: number | null, ramp: [string, string, string], missing: string): ExpressionSpecification {
  // A zero-only batch has no range. Show neutral without inventing a nonzero extent.
  const available: ExpressionSpecification | string = difference && !extent ? ramp[1]
    : ["interpolate", ["linear"], ["number", ["feature-state", "score"], 0],
      difference ? -extent! : 0, ramp[0], difference ? 0 : .5, ramp[1], difference ? extent! : 1, ramp[2]];
  return ["case", ["==", ["feature-state", "available"], true], available, missing];
}
export async function runScenario(mode: ScenarioMode, adjustments: Adjustments): Promise<ScenarioResult> {
  const api = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const response = await fetch(`${api}/api/scenario/simulate`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, adjustments }), cache: "no-store",
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `Simulation failed (${response.status}).`);
  if (!body || body.hours !== 24 || !Array.isArray(body.points) || body.weather?.stale
      || new Set(body.points.map((p: ScenarioPoint) => p.prediction_cutoff)).size !== 24
      || body.points.length !== body.barangay_count * 24
      || body.points.some((p: ScenarioPoint) => typeof p.barangay_psgc !== "string" || !/^\d{10}$/.test(p.barangay_psgc)
        || !Number.isFinite(Date.parse(p.target_start)) || p.target_start !== p.prediction_cutoff
        || Date.parse(p.target_end) - Date.parse(p.target_start) !== 3600000
        || [p.baseline_score, p.scenario_score].some(s => s !== null && (!Number.isFinite(s) || s < 0 || s > 1))
        || (p.difference !== null && (!Number.isFinite(p.difference) || p.difference < -1 || p.difference > 1)))) {
    throw new Error("The backend returned an incompatible simulation result.");
  }
  return body;
}

