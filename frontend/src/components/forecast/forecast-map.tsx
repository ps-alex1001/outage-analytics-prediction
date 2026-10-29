"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import { loadBoundaries } from "@/lib/topology";
import { getVisualizationColors } from "@/lib/visualization-theme";
import { scoreText } from "@/lib/forecast";

export type ForecastPalette = { ramp: [string, string, string]; missing: string; text: string; grid: string; background: string; boundary: string };
export default function ForecastMap({ scores, selected, onSelect, palette, level = "barangay", municipalitySelections }: {
  scores: Map<string, number>; selected: string | null; onSelect: (id: string) => void;
  palette: ForecastPalette; level?: "municipality" | "barangay"; municipalitySelections?: Map<string, string>;
}) {
  const municipalityLookup = useRef(municipalitySelections);
  useEffect(() => { municipalityLookup.current = municipalitySelections; }, [municipalitySelections]);
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!container.current) return;
    let disposed = false;
    queueMicrotask(() => { setReady(false); setError(null); });
    const colors = getVisualizationColors();
    maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");
    const map = new maplibregl.Map({ container: container.current,
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": colors.map.background } }] },
      center: [120.47, 14.67], zoom: 9, renderWorldCopies: false });
    instance.current = map;
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.getCanvas().setAttribute("aria-label", `${level === "municipality" ? "Municipality mean" : "Barangay"} experimental score map. Pan with arrow keys and zoom with plus or minus. Municipality selection opens its highest-scoring available barangay in the ranking.`);
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
    const resize = new ResizeObserver(() => map.resize()); resize.observe(container.current);
    map.on("error", () => { if (!disposed) setError("Map could not render. Use the barangay ranking to explore scores."); });
    map.on("load", async () => {
      try {
        const { boundaries } = await loadBoundaries(level);
        if (disposed) return;
        map.addSource("forecast-barangays", { type: "geojson", data: boundaries, promoteId: `${level}_psgc` });
        map.addLayer({ id: "forecast-fill", source: "forecast-barangays", type: "fill", paint: { "fill-color": colors.map.noData, "fill-opacity": .92 } });
        map.addLayer({ id: "forecast-borders", source: "forecast-barangays", type: "line", paint: { "line-color": colors.map.boundary, "line-width": .7 } });
        map.addLayer({ id: "forecast-selected", source: "forecast-barangays", type: "line", filter: ["==", ["get", `${level}_psgc`], ""], paint: { "line-color": colors.chart.text, "line-width": 3 } });
        const bounds = new maplibregl.LngLatBounds();
        function extend(value: unknown) {
          if (!Array.isArray(value)) return;
          if (typeof value[0] === "number" && typeof value[1] === "number") bounds.extend([value[0], value[1]]);
          else value.forEach(extend);
        }
        boundaries.features.forEach(f => { if ("coordinates" in f.geometry) extend(f.geometry.coordinates); });
        map.resize(); if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 28, duration: 0 });
        setReady(true);
      } catch { if (!disposed) setError("Barangay boundaries are unavailable. Scores remain accessible in the ranking."); }
    });
    map.on("click", "forecast-fill", event => {
      const id = event.features?.[0]?.properties?.[`${level}_psgc`];
      if (typeof id === "string") { const target = level === "municipality" ? municipalityLookup.current?.get(id) : id; if (target) onSelect(target); }
    });
    map.on("mousemove", "forecast-fill", event => {
      const feature = event.features?.[0]; if (!feature) return;
      map.getCanvas().style.cursor = "pointer";
      const state = map.getFeatureState({ source: "forecast-barangays", id: feature.id });
      popup.setLngLat(event.lngLat).setText(`${feature.properties[level] ?? feature.properties.name ?? feature.properties.barangay_psgc} · ${state.available ? scoreText(state.score) : "Unavailable"}`).addTo(map);
    });
    map.on("mouseleave", "forecast-fill", () => { popup.remove(); map.getCanvas().style.cursor = ""; });
    return () => { disposed = true; resize.disconnect(); popup.remove(); map.remove(); instance.current = null; };
  }, [onSelect, level]);

  useEffect(() => {
    const map = instance.current; if (!ready || !map || !map.getLayer("forecast-fill")) return;
    // Clear old availability so a missing hour can never inherit a previous score.
    map.removeFeatureState({ source: "forecast-barangays" });
    scores.forEach((score, id) => map.setFeatureState({ source: "forecast-barangays", id }, { available: true, score }));
    map.setPaintProperty("background", "background-color", palette.background);
    map.setPaintProperty("forecast-borders", "line-color", palette.boundary);
    map.setPaintProperty("forecast-selected", "line-color", palette.text);
    map.setFilter("forecast-selected", ["==", ["get", `${level}_psgc`], selected ?? ""]);
    map.setPaintProperty("forecast-fill", "fill-color", ["case", ["==", ["feature-state", "available"], true],
      ["interpolate", ["linear"], ["number", ["feature-state", "score"], 0],
        0, palette.ramp[0], 0.5, palette.ramp[1], 1, palette.ramp[2]], palette.missing]);
  }, [scores, selected, palette, ready, level]);

  return <div className="space-y-3">
    <div className="relative h-[430px] overflow-hidden rounded-lg border">
      <div ref={container} className="h-full w-full" />
      {!ready && !error && <p role="status" className="absolute inset-0 grid place-items-center bg-card/90 text-sm">Loading barangay boundaries…</p>}
    </div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="space-y-1 text-xs">
      <p className="font-medium">{level === "municipality" ? "Mean of available barangay scores" : "Experimental model score"}</p>
      <div className="h-3 rounded" style={{ background: `linear-gradient(to right, ${palette.ramp.join(",")})` }} />
      <div className="flex justify-between tabular-nums"><span>0</span><span>1</span></div>
      <p className="flex items-center gap-2 text-muted-foreground"><span className="inline-block size-3 border" style={{ background: palette.missing }} />Unavailable · fixed scale across all 24 intervals</p>
    </div>
  </div>;
}
