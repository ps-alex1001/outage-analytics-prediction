import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { useRouter } from "next/navigation";
import * as maplibregl from "maplibre-gl";
import { useTheme } from "next-themes";
import { useColorblindMode } from "@/components/theme-provider";
import { getVisualizationColors } from "@/lib/visualization-theme";

type BoundaryLevel = "municipality" | "barangay";
type Handle = "min" | "max";

type GeographicRow = {
  municipality_psgc?: string;
  barangay_psgc?: string;
  municipality?: string;
  barangay?: string;
  total_outages: number;
  total_duration_hours?: number;
};

type GeographicData = {
  municipalities: GeographicRow[];
  barangays: GeographicRow[];
};

type Range = { min: number; max: number };

const API = "http://127.0.0.1:8000";
const SOURCE_ID = "barangays";
const FILL_LAYER = "barangay-fill";
const OUTLINE_LAYER = "barangay-outline";
const HIGHLIGHT_LAYER = "area-highlight";

function getAreaName(row: GeographicRow, level: BoundaryLevel) {
  return level === "municipality"
    ? row.municipality ?? row.municipality_psgc ?? "Unknown municipality"
    : row.barangay ?? row.barangay_psgc ?? "Unknown barangay";
}

export type SelectedArea = {
  level: "municipality" | "barangay";
  code: string;
  name: string;
  municipality: string | null;
  municipalityPsgc: string | null;
};

export default function OutageMap({
  year,
  selectedCause,
  selectedArea,
  onAreaSelect,
}: {
  year: string;
  selectedCause: string | null;
  selectedArea: SelectedArea | null;
  onAreaSelect: (area: SelectedArea | null) => void;
}) {
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const hoverPopupRef = useRef<maplibregl.Popup | null>(null);
  const detailPopupRef = useRef<maplibregl.Popup | null>(null);
  const requestId = useRef(0);
  const dragHandleRef = useRef<Handle | null>(null);

  const [level, setLevel] = useState<BoundaryLevel>("municipality");
  const [mapReady, setMapReady] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [geographicData, setGeographicData] = useState<GeographicData | null>(null);
  const [range, setRange] = useState<Range>({ min: 0, max: 0 });
  const [legendHover, setLegendHover] = useState<number | null>(null);
  const [hoveredCode, setHoveredCode] = useState<string | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { resolvedTheme } = useTheme();
  const { colorblind } = useColorblindMode();

  const currentRows = useMemo(
    () =>
      level === "municipality"
        ? geographicData?.municipalities ?? []
        : geographicData?.barangays ?? [],
    [level, geographicData]
  );

  const codeKey = level === "municipality" ? "municipality_psgc" : "barangay_psgc";
  const maxOutages = Math.max(0, ...currentRows.map((row) => row.total_outages));

  // A legend position can match multiple areas. Highlight all areas with
  // the nearest observed outage count rather than inventing a unique match.
  const legendMatch = useMemo(() => {
    if (legendHover === null || currentRows.length === 0) return null;
    const difference = Math.min(
      ...currentRows.map((row) => Math.abs(row.total_outages - legendHover))
    );
    const matches = currentRows.filter(
      (row) => Math.abs(row.total_outages - legendHover) === difference
    );
    return {
      count: matches[0].total_outages,
      names: matches.map((row) => getAreaName(row, level)),
      codes: matches
        .map((row) => row[codeKey])
        .filter((code): code is string => Boolean(code)),
    };
  }, [legendHover, currentRows, level, codeKey]);

  // Load statistics when global Overview filters change.
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();
    if (year !== "all") params.set("year", year);
    if (selectedCause) params.set("cause_category", selectedCause);

    setGeographicData(null);

    async function loadGeography() {
      try {
        const response = await fetch(`${API}/api/overview/geography?${params}`, {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Unable to load geographic statistics");
        const result: GeographicData = await response.json();
        if (!controller.signal.aborted) {
          setGeographicData(result);
          setError(null);
        }
      } catch {
        if (!controller.signal.aborted) {
          setError("Unable to load geographic statistics.");
        }
      }
    }

    void loadGeography();
    return () => controller.abort();
  }, [year, selectedCause]);

  // Always start with the full range when the geography or filters change.
  useEffect(() => {
    setRange({ min: 0, max: maxOutages });
    setLegendHover(null);
  }, [level, year, selectedCause, maxOutages]);

  // Create MapLibre once. The existing source is reused after toggling levels.
  useEffect(() => {
    if (!containerRef.current) return;
    const controller = new AbortController();
    const colors = getVisualizationColors();

    maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {},
        layers: [{
          id: "background",
          type: "background",
          paint: { "background-color": colors.map.background },
        }],
      },
      center: [120.55, 14.66],
      zoom: 9,
    });

    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    map.on("load", async () => {
      try {
        const response = await fetch(
          `${API}/api/overview/boundaries?level=municipality`,
          { signal: controller.signal }
        );
        if (!response.ok) throw new Error("Unable to load boundaries");
        const geojson = await response.json();
        if (controller.signal.aborted) return;

        map.addSource(SOURCE_ID, { type: "geojson", data: geojson });
        map.addLayer({
          id: FILL_LAYER,
          type: "fill",
          source: SOURCE_ID,
          paint: {
            "fill-color": colors.map.noData,
            "fill-opacity": 0.92,
          },
        });
        map.addLayer({
          id: OUTLINE_LAYER,
          type: "line",
          source: SOURCE_ID,
          paint: {
            "line-color": colors.map.boundary,
            "line-width": 0.9,
          },
        });
        map.addLayer({
          id: HIGHLIGHT_LAYER,
          type: "line",
          source: SOURCE_ID,
          filter: ["==", ["get", "municipality_psgc"], "__none__"],
          paint: {
            "line-color": colors.map.high,
            "line-width": 3,
            "line-opacity": 1,
          },
        });

        const bounds = new maplibregl.LngLatBounds();
        function extendBounds(value: unknown): void {
          if (!Array.isArray(value)) return;
          if (typeof value[0] === "number" && typeof value[1] === "number") {
            bounds.extend([value[0], value[1]]);
          } else {
            for (const child of value) extendBounds(child);
          }
        }
        for (const feature of geojson.features) {
          extendBounds(feature.geometry?.coordinates);
        }
        requestAnimationFrame(() => {
          if (controller.signal.aborted || bounds.isEmpty()) return;
          map.resize();
          const canvas = map.getCanvas();
          if (canvas.width > 80 && canvas.height > 80) {
            map.fitBounds(bounds, { padding: 25, duration: 0 });
          }
        });
        setMapReady(true);
      } catch {
        if (!controller.signal.aborted) setError("Unable to load map boundaries.");
      }
    });

    return () => {
      controller.abort();
      requestId.current += 1;
      hoverPopupRef.current?.remove();
      // detailPopupRef.current?.remove();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  async function changeLevel(nextLevel: BoundaryLevel) {
    if (!mapReady || switching || nextLevel === level) return;
    const currentRequest = ++requestId.current;
    setSwitching(true);
    setError(null);
    setLegendHover(null);
    setHoveredCode(null);
    setLevel(nextLevel);
    onAreaSelect(null);
    hoverPopupRef.current?.remove();
    // detailPopupRef.current?.remove();

    try {
      const response = await fetch(
        `${API}/api/overview/boundaries?level=${nextLevel}`
      );
      if (!response.ok) throw new Error("Unable to switch boundaries");
      const geojson = await response.json();
      if (requestId.current !== currentRequest) return;

      const source = mapRef.current?.getSource(SOURCE_ID) as
        | maplibregl.GeoJSONSource
        | undefined;
      if (!source) throw new Error("Map source missing");
      await source.setData(geojson);
      if (requestId.current !== currentRequest) return;
      setLevel(nextLevel);
    } catch {
      if (requestId.current === currentRequest) {
        setError("Unable to switch geographic boundaries.");
      }
    } finally {
      if (requestId.current === currentRequest) setSwitching(false);
    }
  }

  // Reuse the same PSGC-to-count expression for color and range opacity.
  const countExpression = useMemo(() => {
    const lookup: (string | number)[] = [];
    for (const row of currentRows) {
      const code = row[codeKey];
      if (code) lookup.push(code, row.total_outages);
    }
    return ["match", ["get", codeKey], ...lookup, 0] as unknown as
      maplibregl.ExpressionSpecification;
  }, [currentRows, codeKey]);

  // Repaint colors only when statistics, level, or theme changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const colors = getVisualizationColors();

    map.setPaintProperty("background", "background-color", colors.map.background);
    map.setPaintProperty(OUTLINE_LAYER, "line-color", colors.map.boundary);
    map.setPaintProperty(HIGHLIGHT_LAYER, "line-color", colors.map.high);

    if (!geographicData || maxOutages === 0) {
      map.setPaintProperty(FILL_LAYER, "fill-color", colors.map.noData);
      map.setPaintProperty(FILL_LAYER, "fill-opacity", 0.92);
      return;
    }

    const fillExpression = [
      "interpolate", ["linear"], countExpression,
      0, colors.map.noData,
      maxOutages * 0.15, colors.map.low,
      maxOutages * 0.55, colors.map.medium,
      maxOutages, colors.map.high,
    ] as maplibregl.ExpressionSpecification;

    map.setPaintProperty(FILL_LAYER, "fill-color", fillExpression);
  }, [mapReady, geographicData, maxOutages, countExpression,
    resolvedTheme, colorblind]);

  // Dragging the handles only updates opacity, not the color scale or map data.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !geographicData || maxOutages === 0) return;

    const opacityExpression = [
      "case",
      ["all", [">=", countExpression, range.min], ["<=", countExpression, range.max]],
      0.92,
      0.16,
    ] as maplibregl.ExpressionSpecification;

    map.setPaintProperty(FILL_LAYER, "fill-opacity", opacityExpression);
  }, [mapReady, geographicData, maxOutages, countExpression, range.min, range.max]);

  // Use a separate outline layer for selection; no geometry reload is needed.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !map.getLayer(HIGHLIGHT_LAYER)) return;

    const selectedCode =
        selectedArea?.level === level
            ? selectedArea.code
            : null;

    const codes =
        legendMatch?.codes ??
        (hoveredCode
            ? [hoveredCode]
            : selectedCode
                ? [selectedCode]
                : []);

    map.setFilter(HIGHLIGHT_LAYER, [
      "in", ["get", codeKey], ["literal", codes],
    ]);
  }, [mapReady, level, codeKey, legendMatch, hoveredCode, selectedCode]);

  // Hover: small tooltip. Click: persistent popup with an Explore link.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !geographicData) return;

    const onMove = (event: maplibregl.MapLayerMouseEvent) => {
      const code = String(event.features?.[0]?.properties?.[codeKey] ?? "");
      if (!code) return;
      map.getCanvas().style.cursor = "pointer";
      setHoveredCode((current) => current === code ? current : code);

      const row = currentRows.find((item) => item[codeKey] === code);
      const name = row ? getAreaName(row, level) : code;
      const count = row?.total_outages ?? 0;

      if (!hoverPopupRef.current) {
        hoverPopupRef.current = new maplibregl.Popup({
          closeButton: false, closeOnClick: false, offset: 12,
        });
      }
      const popup = hoverPopupRef.current;
      popup.setLngLat(event.lngLat).setText(
        `${name} · ${count.toLocaleString()} outages`
      );
      if (!popup.isOpen()) popup.addTo(map);
      popup.getElement().style.pointerEvents = "none";
    };

    const onLeave = () => {
      map.getCanvas().style.cursor = "";
      setHoveredCode(null);
      hoverPopupRef.current?.remove();
      hoverPopupRef.current = null;
    };

    const onClick = (event: maplibregl.MapLayerMouseEvent) => {
      const code = String(
          event.features?.[0]?.properties?.[codeKey] ?? ""
      );

      if (!code) return;

      const row = currentRows.find(
          (item) => item[codeKey] === code
      );

      if (!row) return;

      onLeave();

      // Clicking an already selected area clears it.
      if (
          selectedArea?.level === level &&
          selectedArea.code === code
      ) {
        onAreaSelect(null);
        return;
      }

      onAreaSelect({
        level,
        code,
        name: getAreaName(row, level),
        municipality: row.municipality ?? null,
        municipalityPsgc: row.municipality_psgc ?? null,
      });
    };

    map.on("mousemove", FILL_LAYER, onMove);
    map.on("mouseleave", FILL_LAYER, onLeave);
    map.on("click", FILL_LAYER, onClick);
    return () => {
      map.off("mousemove", FILL_LAYER, onMove);
      map.off("mouseleave", FILL_LAYER, onLeave);
      map.off("click", FILL_LAYER, onClick);
      onLeave();
    };
  }, [
    mapReady,
    geographicData,
    currentRows,
    codeKey,
    level,
    selectedArea,
    onAreaSelect,
  ]);

  // Keyboard-accessible range handles; the gradient itself is the slider track.
  function setHandleValue(handle: Handle, rawValue: number) {
    const value = Math.max(0, Math.min(maxOutages, Math.round(rawValue)));
    setRange((previous) =>
      handle === "min"
        ? { ...previous, min: Math.min(value, previous.max) }
        : { ...previous, max: Math.max(value, previous.min) }
    );
  }

  function pointerValue(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const proportion = bounds.width > 0
      ? (event.clientX - bounds.left) / bounds.width : 0;
    return Math.round(Math.max(0, Math.min(1, proportion)) * maxOutages);
  }

  function onSliderPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (maxOutages === 0) return;
    const value = pointerValue(event);
    const target = event.target as HTMLElement;
    const handle = target.closest("[data-handle]")?.getAttribute("data-handle") as Handle | null;
    const chosen: Handle = handle ??
      (Math.abs(value - range.min) <= Math.abs(value - range.max) ? "min" : "max");
    dragHandleRef.current = chosen;
    event.currentTarget.setPointerCapture(event.pointerId);
    setHandleValue(chosen, value);
    setLegendHover(value);
  }

  function onSliderPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (maxOutages === 0) return;
    const value = pointerValue(event);
    setLegendHover((previous) => previous === value ? previous : value);
    if (dragHandleRef.current) setHandleValue(dragHandleRef.current, value);
  }

  function onSliderPointerEnd() {
    dragHandleRef.current = null;
    setLegendHover(null);
  }

  function onSliderKeyDown(event: KeyboardEvent<HTMLButtonElement>, handle: Handle) {
    let next = handle === "min" ? range.min : range.max;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") next -= 1;
    else if (event.key === "ArrowRight" || event.key === "ArrowUp") next += 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = maxOutages;
    else return;
    event.preventDefault();
    setHandleValue(handle, next);
  }

  const minPct = maxOutages ? (range.min / maxOutages) * 100 : 0;
  const maxPct = maxOutages ? (range.max / maxOutages) * 100 : 100;

  return (
    <section className="w-full min-w-0 rounded-xl border bg-card p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">Geographic Outage Distribution</h2>
        <div role="group" aria-label="Geographic level" className="flex rounded-md border p-1">
          {(["municipality", "barangay"] as const).map((value) => (
            <button
              key={value}
              type="button"
              disabled={!mapReady || switching}
              aria-pressed={level === value}
              onClick={() => void changeLevel(value)}
              className={`rounded px-3 py-1 text-xs font-medium disabled:opacity-50 ${
                level === value ? "bg-primary text-primary-foreground" : "hover:bg-muted"
              }`}
            >
              {value === "municipality" ? "Municipality" : "Barangay"}
            </button>
          ))}
        </div>
      </div>

      <div ref={containerRef} className="h-[430px] w-full overflow-hidden rounded-lg" />

      <div className="mt-4 space-y-2">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>Simulated outage frequency</span>
          <span>{range.min.toLocaleString()}–{range.max.toLocaleString()} events</span>
        </div>

        <div
          className="relative mx-2 h-8 select-none touch-none"
          onPointerDown={onSliderPointerDown}
          onPointerMove={onSliderPointerMove}
          onPointerUp={onSliderPointerEnd}
          onPointerCancel={onSliderPointerEnd}
          onPointerLeave={() => { if (!dragHandleRef.current) setLegendHover(null); }}
          aria-label="Outage frequency legend"
        >
          <div
            className="absolute inset-x-0 top-2 h-4 rounded-md border border-border"
            style={{
              background: "linear-gradient(to right, var(--map-no-data) 0%, var(--map-low) 15%, var(--map-medium) 55%, var(--map-high) 100%)",
            }}
          />
          <div
            className="pointer-events-none absolute top-2 h-4 rounded-l-md bg-background/65"
            style={{ left: 0, width: `${minPct}%` }}
          />
          <div
            className="pointer-events-none absolute top-2 h-4 rounded-r-md bg-background/65"
            style={{ left: `${maxPct}%`, right: 0 }}
          />
          {(["min", "max"] as const).map((handle) => {
            const value = handle === "min" ? range.min : range.max;
            return (
              <button
                key={handle}
                type="button"
                role="slider"
                data-handle={handle}
                aria-label={`${handle === "min" ? "Minimum" : "Maximum"} outage count`}
                aria-valuemin={0}
                aria-valuemax={maxOutages}
                aria-valuenow={value}
                aria-valuetext={`${value} outages`}
                disabled={maxOutages === 0}
                onKeyDown={(event) => onSliderKeyDown(event, handle)}
                className="absolute top-0 z-10 h-8 w-3 -translate-x-1/2 cursor-ew-resize rounded border-2 border-foreground bg-card shadow-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-default"
                style={{ left: `${handle === "min" ? minPct : maxPct}%` }}
              />
            );
          })}
        </div>

        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>0</span>
          <span>{maxOutages.toLocaleString()} (max)</span>
        </div>
        <div className="flex min-h-6 items-center justify-between gap-2 text-xs">
          <span className="min-w-0 text-muted-foreground">
            {legendMatch
              ? `Near ${legendMatch.count.toLocaleString()} events: ${legendMatch.names.slice(0, 2).join(", ")}${legendMatch.names.length > 2 ? ` +${legendMatch.names.length - 2} more` : ""}`
              : "Hover the legend to highlight matching areas; drag handles to fade others."}
          </span>
          <button
            type="button"
            onClick={() => setRange({ min: 0, max: maxOutages })}
            className="shrink-0 rounded border px-2 py-1 hover:bg-muted"
          >
            Reset
          </button>
        </div>
      </div>

      {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
    </section>
  );
}
