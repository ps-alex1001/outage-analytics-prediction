/** Backend forecast contract. PSGCs and all source timestamps remain unchanged. */
export type ForecastPoint = {
  barangay_psgc: string; barangay: string; municipality_psgc: string; municipality: string;
  prediction_cutoff: string; target_start: string; target_end: string; last_weather_label: string;
  lead_hours: number; score: number | null; status: string; missing_features: string[];
  feature_coverage: number; weather_location_id: string; cape_location_id: string;
  assumption: string; current_outage_status: string; weather_summary: Record<string, number | null>;
};
export type ForecastBatch = {
  status: string; notice: string; issued_at: string; model_id: string; hours: number;
  barangay_count: number; target_interval: string; forecast_weather_validated: boolean;
  automatic_alerts: boolean;
  weather: { provider: string; model: string; snapshot_id: string; retrieved_at: string;
    age_seconds: number; stale: boolean; refresh_failed: boolean; product: string;
    run_initialized_at: string | null; published_at: string | null; run_availability_status: string;
    source_shift: string; attribution: string; locations: unknown[] };
  points: ForecastPoint[];
};
export type ForecastMetadata = {
  status: string; error: string | null; notice: string; timezone: string;
  model: { model_id: string; training_rows: number; parity_rows_verified: number } | null;
  weather_model: string; barangay_count: number;
};
const API = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API}/api/forecast/${path}`, { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `Forecast request failed (${response.status}).`);
  }
  return response.json();
}

export async function loadForecast() {
  const metadata = await request<ForecastMetadata>("metadata");
  if (metadata.status !== "ready") return { metadata, batch: null };
  const batch = await request<ForecastBatch>("latest?hours=24");
  if (!Array.isArray(batch.points) || !batch.weather || !Number.isFinite(Date.parse(batch.issued_at))) {
    throw new Error("The backend returned an invalid forecast batch.");
  }
  const keys = new Set<string>();
  const intervals = new Set<string>();
  for (const p of batch.points) {
    const key = `${p.barangay_psgc}:${p.prediction_cutoff}`;
    if (typeof p.barangay_psgc !== "string" || typeof p.municipality_psgc !== "string"
        || !/^\d{10}$/.test(p.barangay_psgc) || keys.has(key)
        || !Number.isFinite(Date.parse(p.target_start)) || !Number.isFinite(Date.parse(p.target_end))
        || p.target_start !== p.prediction_cutoff
        || Date.parse(p.target_end) - Date.parse(p.target_start) !== 3600000
        || (p.score !== null && (!Number.isFinite(p.score) || p.score < 0 || p.score > 1))) {
      throw new Error("The backend returned incompatible hourly forecast records.");
    }
    keys.add(key); intervals.add(p.prediction_cutoff);
  }
  if (intervals.size !== 24) throw new Error("The backend did not return 24 target intervals.");
  return { metadata, batch };
}

export function scoreAvailable(batch: ForecastBatch, point: ForecastPoint): boolean {
  return batch.status === "experimental" && !batch.weather.stale
    && point.status === "experimental" && point.score !== null && Number.isFinite(point.score);
}

export function scoreText(score: number | null | undefined) {
  return score == null ? "Unavailable" : score.toFixed(6);
}

export function manilaTime(value: string) {
  return new Intl.DateTimeFormat("en-PH", { timeZone: "Asia/Manila", year: "numeric",
    month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(value));
}
export function intervalText(point: ForecastPoint) {
  return `(${manilaTime(point.target_start)}, ${manilaTime(point.target_end)}] PHT`;
}
