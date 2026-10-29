"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import { loadBoundaries } from "@/lib/topology";
import { getVisualizationColors } from "@/lib/visualization-theme";
import { scoreText } from "@/lib/forecast";
import { differenceText, scenarioFillColor } from "@/lib/scenario";
import { relativeScoreColor, relativeScaleNote } from "@/lib/relative-score";
import type { RelativeScoreScale } from "@/lib/relative-score";

export type ScenarioPalette = { ramp: [string, string, string]; missing: string; text: string; grid: string; background: string; boundary: string };
export type WeatherMapFrame = { label: string; maximum: number; points: { id: string; name: string; latitude: number; longitude: number; value: number | null }[] };
export default function ScenarioMap({ scores, selected, onSelect, palette, difference, differenceExtent, weather, relativeScale, level = "barangay" }: {
  scores: Map<string, number>; selected: string | null; onSelect: (id: string) => void;
  palette: ScenarioPalette; difference: boolean; differenceExtent: number | null;
  weather?: WeatherMapFrame | null;
  level?: "barangay" | "municipality";
  relativeScale?: RelativeScoreScale;
}) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<maplibregl.Map | null>(null);
  const selectCallback = useRef(onSelect);
  useEffect(() => { selectCallback.current = onSelect; }, [onSelect]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!container.current) return;
    let disposed = false;
    const colors = getVisualizationColors();
    maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");
    const map = new maplibregl.Map({ container: container.current,
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": colors.map.background } }] },
      center: [120.47, 14.67], zoom: 9, renderWorldCopies: false });
    instance.current = map;
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.getCanvas().setAttribute("aria-label", "Municipality or barangay experimental outage-onset score map. Pan with arrow keys and zoom with plus or minus. Use the detail selector below the map for accessible inspection.");
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
    const resize = new ResizeObserver(() => map.resize()); resize.observe(container.current);
    map.on("error", () => { if (!disposed) setError("Map could not render. Use the barangay detail selector to explore scores."); });
    map.on("load", async () => {
      try {
        const [{ boundaries }, municipal] = await Promise.all([loadBoundaries("barangay"), loadBoundaries("municipality")]);
        if (disposed) return;
        map.addSource("forecast-barangays", { type: "geojson", data: boundaries, promoteId: "barangay_psgc" });
        map.addLayer({ id: "forecast-fill", source: "forecast-barangays", type: "fill", paint: { "fill-color": colors.map.noData, "fill-opacity": .92 } });
        map.addLayer({ id: "forecast-borders", source: "forecast-barangays", type: "line", paint: { "line-color": colors.map.boundary, "line-width": .7 } });
        map.addLayer({ id: "forecast-selected", source: "forecast-barangays", type: "line", filter: ["==", ["get", "barangay_psgc"], ""], paint: { "line-color": colors.chart.text, "line-width": 3 } });
        map.addSource("replay-municipalities", { type: "geojson", data: municipal.boundaries, promoteId: "municipality_psgc" });
        map.addLayer({ id: "municipality-fill", source: "replay-municipalities", type: "fill", layout: { visibility: "none" }, paint: { "fill-color": colors.map.noData, "fill-opacity": .92 } });
        map.addLayer({ id: "municipality-borders", source: "replay-municipalities", type: "line", layout: { visibility: "none" }, paint: { "line-color": colors.map.boundary, "line-width": 1.2 } });
        map.addLayer({ id: "municipality-selected", source: "replay-municipalities", type: "line", layout: { visibility: "none" }, filter: ["==", ["get", "municipality_psgc"], ""], paint: { "line-color": colors.chart.text, "line-width": 3 } });
        map.addSource("replay-weather", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
        map.addLayer({ id: "replay-weather-points", source: "replay-weather", type: "circle", layout: { visibility: "none" }, paint: { "circle-radius": 13, "circle-stroke-width": 2, "circle-stroke-color": colors.chart.text, "circle-opacity": .95 } });
        const bounds = new maplibregl.LngLatBounds();
        function extend(value: unknown) {
          if (!Array.isArray(value)) return;
          if (typeof value[0] === "number" && typeof value[1] === "number") bounds.extend([value[0], value[1]]);
          else value.forEach(extend);
        }
        boundaries.features.forEach(f => { if ("coordinates" in f.geometry) extend(f.geometry.coordinates); });
        map.resize(); if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 28, duration: 0 });
        setReady(true);
      } catch { if (!disposed) setError("Barangay boundaries are unavailable. Scores remain accessible in the barangay detail selector."); }
    });
    map.on("click", "forecast-fill", event => {
      const id = event.features?.[0]?.properties?.barangay_psgc;
      if (typeof id === "string") selectCallback.current(id);
    });
    map.on("mousemove", "forecast-fill", event => {
      const feature = event.features?.[0]; if (!feature) return;
      map.getCanvas().style.cursor = "pointer";
      const state = map.getFeatureState({ source: "forecast-barangays", id: feature.id });
      popup.setLngLat(event.lngLat).setText(`${feature.properties.barangay ?? feature.properties.name ?? feature.properties.barangay_psgc} · ${state.available ? scoreText(state.score) : "Unavailable"}`).addTo(map);
    });
    map.on("mouseleave", "forecast-fill", () => { popup.remove(); map.getCanvas().style.cursor = ""; });
    map.on("click", "municipality-fill", event => {
      const id = event.features?.[0]?.properties?.municipality_psgc;
      if (typeof id === "string") selectCallback.current(id);
    });
    map.on("mousemove", "municipality-fill", event => {
      const f = event.features?.[0]; if (!f) return;
      const state = map.getFeatureState({ source: "replay-municipalities", id: f.id });
      map.getCanvas().style.cursor = "pointer";
      popup.setLngLat(event.lngLat).setText(`${f.properties.municipality ?? f.properties.name} · Mean available barangay score: ${state.available ? scoreText(state.score) : "Unavailable"}`).addTo(map);
    });
    map.on("mouseleave", "municipality-fill", () => { popup.remove(); map.getCanvas().style.cursor = ""; });
    map.on("mousemove", "replay-weather-points", event => {
      const p = event.features?.[0]?.properties; if (!p) return;
      popup.setLngLat(event.lngLat).setText(`${p.name} · ${p.value == null ? "Unavailable" : Number(p.value).toFixed(2)}`).addTo(map);
    });
    map.on("mouseleave", "replay-weather-points", () => popup.remove());
    return () => { disposed = true; resize.disconnect(); popup.remove(); map.remove(); instance.current = null; };
  }, []);

  useEffect(() => {
    const map = instance.current; if (!ready || !map) return;
    // Clear old availability so a missing hour can never inherit a previous score.
    const source = level === "barangay" ? "forecast-barangays" : "replay-municipalities";
    const prefix = level === "barangay" ? "forecast" : "municipality";
    const key = level === "barangay" ? "barangay_psgc" : "municipality_psgc";
    map.removeFeatureState({ source });
    scores.forEach((score, id) => map.setFeatureState({ source, id }, { available: true, score }));
    map.setPaintProperty("background", "background-color", palette.background);
    map.setPaintProperty(`${prefix}-borders`, "line-color", palette.boundary);
    map.setPaintProperty(`${prefix}-selected`, "line-color", palette.text);
    map.setFilter(`${prefix}-selected`, ["==", ["get", key], selected ?? ""]);
    map.setPaintProperty(`${prefix}-fill`, "fill-color", !difference && relativeScale ? relativeScoreColor(relativeScale, palette.ramp, palette.missing) : scenarioFillColor(difference, differenceExtent, palette.ramp, palette.missing));
  }, [scores, selected, palette, ready, difference, differenceExtent, level, relativeScale]);

  useEffect(() => {
    const map = instance.current; if (!ready || !map) return;
    for (const prefix of ["forecast", "municipality"]) {
      const active = prefix === (level === "barangay" ? "forecast" : "municipality");
      for (const suffix of ["fill", "borders", "selected"]) map.setLayoutProperty(`${prefix}-${suffix}`, "visibility", active && (suffix !== "fill" || !weather) ? "visible" : "none");
    }
    map.setLayoutProperty("replay-weather-points", "visibility", weather ? "visible" : "none");
    if (!weather) return;
    (map.getSource("replay-weather") as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features: weather.points.map(p => ({
      type: "Feature", id: p.id, geometry: { type: "Point", coordinates: [p.longitude, p.latitude] }, properties: { name: p.name, value: p.value },
    })) });
    map.setPaintProperty("replay-weather-points", "circle-stroke-color", palette.text);
    map.setPaintProperty("replay-weather-points", "circle-color", ["case", ["==", ["get", "value"], null], palette.missing,
      weather.maximum > 0 ? ["interpolate", ["linear"], ["number", ["get", "value"], 0], 0, palette.ramp[0], weather.maximum / 2, palette.ramp[1], weather.maximum, palette.ramp[2]] : palette.ramp[0]]);
  }, [weather, ready, palette, level]);

  return <div className="space-y-3">
    <div className="relative h-[430px] overflow-hidden rounded-lg border">
      <div ref={container} className="h-full w-full" />
      {!ready && !error && <p role="status" className="absolute inset-0 grid place-items-center bg-card/90 text-sm">Loading barangay boundaries…</p>}
    </div>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <div className="space-y-1 text-xs">
      <p className="font-medium">{weather ? weather.label : !difference && relativeScale ? "Relative model score · not outage probability" : level === "municipality" ? "Mean available barangay model score" : difference ? "Scenario − baseline score" : "Experimental model score"}</p>
      <div className="h-3 rounded" style={{ background: !weather && !difference && relativeScale?.flat ? palette.ramp[1] : `linear-gradient(to right, ${palette.ramp.join(",")})` }} />
      <div className="flex justify-between tabular-nums"><span>{!weather && !difference && relativeScale ? "0%" : !weather && difference ? differenceText(differenceExtent === null ? null : -differenceExtent) : "0"}</span>{!weather && difference && <span>0</span>}<span>{weather ? differenceText(weather.maximum) : difference ? differenceText(differenceExtent) : relativeScale ? "100%" : "1"}</span></div>
      {!weather && !difference && relativeScale && <p className="text-muted-foreground">{relativeScaleNote(relativeScale)}</p>}
      <p className="flex items-center gap-2 text-muted-foreground"><span className="inline-block size-3 border" style={{ background: palette.missing }} />Unavailable · fixed scale across all 24 intervals</p>
    </div>
  </div>;
}

