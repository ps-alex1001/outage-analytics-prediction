export type StormEvent = { id: string; title: string; name: string | null; kind: "historical";
  start: string; end: string; duration_hours: number; closest_reference_km: number;
  window_start: string; window_end: string; available: boolean; unavailable_reason: string | null;
  weather_info: string; track_hours: number; track_wind_present: boolean; track_pressure_present: boolean };
export type StormCatalog = { events: StormEvent[]; years: number[]; notice: string; archive_end: string;
  synthetic: { id: string; title: string; kind: "synthetic_forecast" } };
export type ReplayPoint = { barangay_psgc: string; barangay: string; municipality: string;
  municipality_psgc: string; prediction_cutoff: string; target_start: string; target_end: string;
  last_weather_label: string; score: number | null; status: string; missing_features: string[];
  weather_location_id: string; source_weather_location_id?: string; cape_location_id: string };
export type ReplayWeather = { location_id: string; prediction_cutoff: string; weather_label: string;
  rainfall: number | null; wind: number | null; gusts: number | null;
  municipal_series_count?: number; available_counts?: { rainfall: number; wind: number; gusts: number } };
export type ReplayBatch = { kind: "historical" | "synthetic_forecast"; hours: number; barangay_count: number;
  model_id: string; notice: string; event: { id: string; title: string; window_start: string; window_end: string };
  points: ReplayPoint[]; weather_hourly: ReplayWeather[];
  weather_locations: { id: string; latitude: number; longitude: number; municipalities: string[] }[];
  track: { time: string; latitude: number | null; longitude: number | null; source_wind: number | null; source_pressure: number | null }[];
  provenance: { weather?: { retrieved_at: string }; [key: string]: unknown } };

const api = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${api}/api/storm-replays${path}`, { cache: "no-store" });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `Replay request failed (${response.status}).`);
  return body;
}
export function loadStormCatalog() { return request<StormCatalog>(""); }
export async function loadStormReplay(id: string) {
  const batch = await request<ReplayBatch>(`/${encodeURIComponent(id)}`);
  if (!batch || batch.hours !== 24 || !Array.isArray(batch.points) || batch.points.length !== batch.barangay_count * 24
    || new Set(batch.points.map(p => p.prediction_cutoff)).size !== 24
    || !Array.isArray(batch.weather_hourly) || !Array.isArray(batch.weather_locations)
    || batch.points.some(p => typeof p.barangay_psgc !== "string" || !/^\d{10}$/.test(p.barangay_psgc)
      || p.prediction_cutoff !== p.target_start || Date.parse(p.target_end) - Date.parse(p.target_start) !== 3600000
      || (p.score !== null && (!Number.isFinite(p.score) || p.score < 0 || p.score > 1)))) {
    throw new Error("Replay response does not match the 24-hour model contract.");
  }
  return batch;
}
export function replayScores(points: ReplayPoint[], expired = false) {
  return new Map(points.filter(p => !expired && p.status === "experimental" && p.score !== null && Number.isFinite(p.score)).map(p => [p.barangay_psgc, p.score!]));
}
/** Display summary only; not the probability of any outage in a municipality. */
export function municipalityReplayScores(points: ReplayPoint[], expired = false) {
  const groups = new Map<string, { sum: number; available: number; total: number }>();
  for (const p of points) {
    const group = groups.get(p.municipality_psgc) ?? { sum: 0, available: 0, total: 0 };
    group.total++;
    if (!expired && p.status === "experimental" && p.score !== null && Number.isFinite(p.score)) {
      group.sum += p.score; group.available++;
    }
    groups.set(p.municipality_psgc, group);
  }
  return { scores: new Map([...groups].filter(([, g]) => g.available > 0).map(([id, g]) => [id, g.sum / g.available])), groups };
}
export function weatherMaximum(batch: ReplayBatch, metric: "rainfall" | "wind") {
  return batch.weather_hourly.reduce((max, p) => p[metric] === null ? max : Math.max(max, p[metric]!), 0);
}
