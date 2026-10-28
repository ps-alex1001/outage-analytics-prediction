export type RasterKind = "ndvi" | "slope" | "combined";
export type RasterImage = {
  psgc: string; name: string; bounds_wgs84: [number, number, number, number];
  coordinates: [[number, number], [number, number], [number, number], [number, number]];
  maps: Record<RasterKind, { url: string; sha256: string; dimensions: [number, number]; status: string;
    data_available: boolean; valid_30m_pct: number; render_valid_pct: number }>;
};
type Ramp = { color_stops: [number, string][] };
export type Environment = {
  selection_key: string; area: string; ndvi_year: number; basis: string; missing_note: string; terrain: string;
  bounds_wgs84: [number, number, number, number]; images: RasterImage[];
  statistics: Record<string, number | null>;
  coverage: { native_ndvi_valid_pct: number | null; joint_valid_30m_pct: number | null };
  legend: { ndvi: Ramp; slope: Ramp; combined: { palette_rows_low_to_high_slope_columns_low_to_high_ndvi: string[][] } };
};

// Completed compressed images only; aborts never poison the cache. No bitmap/GPU ownership here.
const blobs = new Map<string, Blob>();
export async function loadRasterBlob(image: RasterImage, kind: RasterKind, origin: string, signal: AbortSignal): Promise<Blob> {
  const asset = image.maps[kind];
  if (!asset.data_available) throw new Error(`No ${kind} measurements for ${image.name}.`);
  const url = new URL(asset.url, origin + "/").href;
  const key = url + "#" + asset.sha256;
  signal.throwIfAborted();
  const cached = blobs.get(key);
  if (cached) { blobs.delete(key); blobs.set(key, cached); return cached; }
  const response = await fetch(url, { signal, cache: "no-cache" });
  if (!response.ok) throw new Error(`Unable to load ${kind} for ${image.name}.`);
  const blob = await response.blob();
  signal.throwIfAborted();
  blobs.set(key, blob);
  while (blobs.size > 24) blobs.delete(blobs.keys().next().value!);
  return blob;
}
