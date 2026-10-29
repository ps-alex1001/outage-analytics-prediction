"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import * as maplibregl from "maplibre-gl";
import { useTheme } from "next-themes";
import { getVisualizationColors } from "@/lib/visualization-theme";
import { loadRasterBlob, type Environment, type RasterKind } from "@/lib/environment";

export default function EnvironmentalRasterMap({ profile, profileError, retryProfile, kind, api, controls, legend, area }: {
  profile?: Environment; profileError?: string; retryProfile: () => void; kind: RasterKind; api: string;
  controls?: ReactNode; legend?: ReactNode; area?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const viewer = useRef<maplibregl.Map | null>(null);
  const active = useRef<{ ids: string[]; bitmaps: ImageBitmap[] }>({ ids: [], bitmaps: [] });
  const generation = useRef(0);
  const fitted = useRef("");
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [mapError, setMapError] = useState("");
  const [result, setResult] = useState<{ key: string; error?: string } | null>(null);
  const [retry, setRetry] = useState(0);
  const { resolvedTheme } = useTheme();
  const key = profile ? `${profile.selection_key}:${kind}` : "";
  const loading = !profile || !ready || result?.key !== key;
  const error = profileError || mapError || (result?.key === key ? result.error : undefined);

  useEffect(() => {
    if (!container.current) return;
    let map: maplibregl.Map | undefined;
    let resize: ResizeObserver | undefined;
    try {
      maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");
      map = new maplibregl.Map({ container: container.current,
        style: { version: 8, sources: {}, layers: [{ id: "environment-background", type: "background",
          paint: { "background-color": getVisualizationColors().map.background } }] },
        center: [120.47, 14.67], zoom: 9, maxZoom: 18, renderWorldCopies: false,
        attributionControl: false, pitchWithRotate: false,
      });
      viewer.current = map;
      map.dragRotate.disable(); map.keyboard.disableRotation(); map.touchZoomRotate.disableRotation(); map.scrollZoom.disable();
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      map.addControl(new maplibregl.ScaleControl(), "bottom-left");
      map.getCanvas().setAttribute("aria-label", "Environmental map. Arrow keys pan; plus and minus zoom. North is up.");
      map.once("load", () => setReady(true));
      map.on("error", () => setMapError("The environmental map could not render. Retry to reload the viewer."));
      resize = new ResizeObserver(() => map?.resize()); resize.observe(container.current);
    } catch {
      queueMicrotask(() => setMapError("Unable to initialize the environmental map. Check WebGL support and retry."));
    }
    return () => {
      resize?.disconnect(); map?.remove(); viewer.current = null;
      active.current.bitmaps.forEach(bitmap => bitmap.close()); active.current = { ids: [], bitmaps: [] };
      fitted.current = "";
    };
  }, [attempt]);

  useEffect(() => {
    const map = viewer.current;
    if (ready && map?.getLayer("environment-background")) {
      map.setPaintProperty("environment-background", "background-color", getVisualizationColors().map.background);
    }
  }, [resolvedTheme, ready]);

  useEffect(() => {
    const map = viewer.current;
    if (!map || !ready || !profile) return;
    const controller = new AbortController();
    const token = ++generation.current;
    const decoded: ImageBitmap[] = [];
    let committed = false;
    async function stage() {
      const jobs = profile!.images.map(async image => {
        const blob = await loadRasterBlob(image, kind, api, controller.signal);
        const bitmap = await createImageBitmap(blob);
        decoded.push(bitmap);
        controller.signal.throwIfAborted();
        return { image, bitmap };
      });
      const results = await Promise.allSettled(jobs);
      controller.signal.throwIfAborted();
      const failed = results.find(r => r.status === "rejected");
      if (failed?.status === "rejected") throw failed.reason;
      if (token !== generation.current || viewer.current !== map) return;
      const ids: string[] = [];
      try {
        for (const entry of results) {
          if (entry.status !== "fulfilled") continue;
          const { image, bitmap } = entry.value;
          const id = `environment-${token}-${image.psgc}`;
          ids.push(id);
          map!.addSource(id, { type: "image", coordinates: image.coordinates });
          (map!.getSource(id) as maplibregl.ImageSource).updateImage({ image: bitmap });
          map!.addLayer({ id, type: "raster", source: id, paint: {
            "raster-resampling": "nearest", "raster-fade-duration": 0, "raster-opacity": 1,
          } });
        }
      } catch (e) {
        for (const id of ids) { if (map!.getLayer(id)) map!.removeLayer(id); if (map!.getSource(id)) map!.removeSource(id); }
        throw e;
      }
      for (const id of active.current.ids) { map!.removeLayer(id); map!.removeSource(id); }
      active.current.bitmaps.forEach(bitmap => bitmap.close());
      active.current = { ids, bitmaps: decoded }; committed = true;
      if (fitted.current !== profile!.selection_key) {
        const [west, south, east, north] = profile!.bounds_wgs84;
        map!.fitBounds([[west, south], [east, north]], { padding: 28, maxZoom: 16, duration: 0 });
        fitted.current = profile!.selection_key;
      }
      map!.getCanvas().setAttribute("aria-label", `${profile!.area}, ${kind} environmental map. NDVI 2025 and static terrain. Arrow keys pan; plus and minus zoom.`);
      map!.once("render", () => {
        if (!controller.signal.aborted && token === generation.current) setResult({ key });
      });
      map!.triggerRepaint();
    }
    stage().catch(e => {
      if (!controller.signal.aborted && token === generation.current) setResult({ key, error: e instanceof Error ? e.message : "Unable to load environmental images." });
    }).finally(() => { if (!committed) decoded.forEach(bitmap => bitmap.close()); });
    return () => controller.abort();
  }, [profile, kind, api, key, ready, retry, attempt]);

  function fit() {
    if (!profile) return;
    const [west, south, east, north] = profile.bounds_wgs84;
    viewer.current?.fitBounds([[west, south], [east, north]], { padding: 28, maxZoom: 16, duration: 0 });
  }
  return <div className="flex flex-1 flex-col gap-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="font-semibold">{area ?? profile?.area ?? "Bataan"}</h2>
      <button type="button" disabled={loading || !!error} onClick={fit} className="ml-auto min-h-9 rounded-md border bg-background px-3 text-xs hover:bg-muted focus-visible:outline-2 disabled:opacity-50">Fit area</button>
    </div>
    <div className="relative min-h-[380px] flex-1 overflow-hidden rounded border" aria-busy={loading && !error}>
    <div className="absolute inset-0"><div ref={container} className="h-full w-full" /></div>
    <div className="absolute left-2 top-2 z-10 flex max-w-[calc(100%-4rem)] flex-col items-start gap-2">
      {controls}
    </div>
    <span className="pointer-events-none absolute right-2 top-24 rounded bg-background/90 px-2 text-xs">N ↑</span>
    {legend && <div className="absolute bottom-3 right-3 z-10 max-w-[calc(100%-6rem)] rounded-md border bg-background/95 p-3 shadow-sm">{legend}</div>}
    {(loading || error) && <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-card/95 p-6 text-center text-sm" role={error ? "alert" : "status"}>
      <p>{error || (profile ? `Loading ${kind === "ndvi" ? "NDVI" : kind} for ${profile.area}…` : "Loading environmental profile…")}</p>
      {error && <button type="button" className="min-h-11 rounded border px-3 hover:bg-muted focus-visible:outline-2" onClick={() => {
        if (profileError) retryProfile();
        else if (mapError) { setMapError(""); setReady(false); setAttempt(n => n+1); }
        else { setResult(null); setRetry(n => n+1); }
      }}>Retry map</button>}
    </div>}
    </div>
  </div>;
}
