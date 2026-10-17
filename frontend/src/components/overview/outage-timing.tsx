"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import type { CSSProperties } from "react";
import dynamic from "next/dynamic";
import type { EChartsOption } from "echarts";
import { useTheme } from "next-themes";
import { useColorblindMode } from "@/components/theme-provider";
import { getVisualizationColors } from "@/lib/visualization-theme";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
});

const API = "http://127.0.0.1:8000";
const CHART_HEIGHT = 340;
const GRID_TOP = 22;
const GRID_BOTTOM = 34;
const HEATMAP_LEFT = 39; // Percent of the ECharts canvas width.
const HEATMAP_RIGHT = 3;
const HEATMAP_WIDTH = 100 - HEATMAP_LEFT - HEATMAP_RIGHT;
const HEATMAP_HEIGHT = CHART_HEIGHT - GRID_TOP - GRID_BOTTOM;

const weekdays = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

const hours = Array.from({ length: 24 }, (_, hour) =>
  String(hour).padStart(2, "0"),
);

type TimingData = {
  days: {
    weekday: number;
    name: string;
    count: number;
  }[];
  cells: {
    weekday: number;
    hour: number;
    count: number;
  }[];
  max_count: number;
  total_events: number;
  invalid_timestamp_count: number;
};

type Props = {
  year: string;
  selectedCause: string | null;
};

type TimingResult = {
  key: string;
  data: TimingData | null;
  error: boolean;
};

function subscribeReducedMotion(onChange: () => void) {
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

function getReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function getServerReducedMotion() {
  return false;
}

export default function OutageTiming({ year, selectedCause }: Props) {
  const [result, setResult] = useState<TimingResult | null>(null);
  const [visual, setVisual] = useState<
    ReturnType<typeof getVisualizationColors> | null
  >(null);

  const { resolvedTheme } = useTheme();
  const { colorblind } = useColorblindMode();
  const reduceMotion = useSyncExternalStore(
    subscribeReducedMotion,
    getReducedMotion,
    getServerReducedMotion,
  );

  // Wait for the document's theme classes to update before reading CSS colors.
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      setVisual(getVisualizationColors());
    });
    return () => cancelAnimationFrame(frame);
  }, [resolvedTheme, colorblind]);

  const params = new URLSearchParams();
  if (year !== "all") params.set("year", year);
  if (selectedCause) params.set("cause_category", selectedCause);
  const requestUrl = `${API}/api/overview/timing?${params.toString()}`;

  // Results carry their request key: no synchronous state reset in useEffect.
  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      try {
        const response = await fetch(requestUrl, {
          signal: controller.signal,
        });

        if (!response.ok) {
          if (!controller.signal.aborted) {
            setResult({ key: requestUrl, data: null, error: true });
          }
          return;
        }

        const data: TimingData = await response.json();
        if (!controller.signal.aborted) {
          setResult({ key: requestUrl, data, error: false });
        }
      } catch {
        if (!controller.signal.aborted) {
          setResult({ key: requestUrl, data: null, error: true });
        }
      }
    }

    void load();
    return () => controller.abort();
  }, [requestUrl]);

  const loading = result?.key !== requestUrl;
  const error = !loading && result?.error === true;
  const data = !loading && !error ? (result?.data ?? null) : null;

  const dayCounts = weekdays.map(
    (_, day) => data?.days.find((item) => item.weekday === day)?.count ?? 0,
  );

  const heatmapData = (data?.cells ?? []).map((cell) => [
    hours[cell.hour],
    weekdays[cell.weekday],
    cell.count,
  ]);

  // Only highlight the three busiest positive-count weekday/hour cells.
  const hotspots = [...(data?.cells ?? [])]
    .filter((cell) => cell.count > 0)
    .sort(
      (a, b) =>
        b.count - a.count ||
        a.weekday - b.weekday ||
        a.hour - b.hour,
    )
    .slice(0, 3);

  const peakColor = colorblind
    ? (visual?.chart.series[0] ?? "#377eb8")
    : (visual?.map.high ?? "#b91c32");

  const option: EChartsOption = {
    backgroundColor: "transparent",
    animation: !reduceMotion,
    grid: [
      {
        left: "5%",
        width: "31%",
        top: GRID_TOP,
        bottom: GRID_BOTTOM,
      },
      {
        left: `${HEATMAP_LEFT}%`,
        right: `${HEATMAP_RIGHT}%`,
        top: GRID_TOP,
        bottom: GRID_BOTTOM,
      },
    ],
    tooltip: {
      trigger: "item",
      formatter: (parameter: unknown) => {
        const item = parameter as {
          seriesIndex: number;
          dataIndex: number;
          value: unknown;
        };

        if (item.seriesIndex === 0) {
          const day = weekdays[item.dataIndex];
          return `${day}: ${dayCounts[item.dataIndex].toLocaleString()} outage starts`;
        }

        const value = item.value as [string, string, number];
        return (
          `${value[1]}, ${value[0]}:00–${value[0]}:59` +
          `<br/>${value[2].toLocaleString()} outage starts`
        );
      },
    },
    xAxis: [
      {
        type: "value",
        gridIndex: 0,
        inverse: true,
        min: 0,
        max: Math.max(1, ...dayCounts),
        axisLabel: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
      },
      {
        type: "category",
        gridIndex: 1,
        data: hours,
        boundaryGap: true,
        axisTick: { show: false },
        axisLine: { show: false },
        axisLabel: {
          interval: 0,
          color: visual?.chart.text,
          fontSize: 10,
          formatter: (value: string, index: number) =>
            index % 3 === 0 ? value : "",
        },
      },
    ],
    yAxis: [
      {
        type: "category",
        gridIndex: 0,
        data: weekdays,
        inverse: true,
        axisLabel: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      {
        type: "category",
        gridIndex: 1,
        data: weekdays,
        inverse: true,
        axisTick: { show: false },
        axisLine: { show: false },
        axisLabel: {
          color: visual?.chart.text,
          fontSize: 13,
          fontWeight: "normal",
          margin: 10,
          formatter: (value: string) => value.slice(0, 3).toUpperCase(),
        },
      },
    ],
    visualMap: {
      type: "continuous",
      show: false,
      min: 0,
      max: Math.max(1, data?.max_count ?? 0),
      dimension: 2,
      seriesIndex: 1,
      inRange: {
        color: [
          visual?.map.noData ?? "#f5f5f5",
          visual?.map.low ?? "#fff3b0",
          visual?.map.medium ?? "#f77f38",
          visual?.map.high ?? "#b91c32",
        ],
      },
    },
    series: [
      {
        name: "Weekday totals",
        type: "bar",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: dayCounts,
        barWidth: "52%",
        itemStyle: {
          color: visual?.map.medium ?? "#f77f38",
          borderRadius: [3, 0, 0, 3],
        },
        emphasis: {
          itemStyle: { color: peakColor },
        },
        label: {
          show: true,
          position: "left",
          distance: 5,
          color: visual?.chart.text,
          fontSize: 10,
          fontWeight: "normal",
          formatter: "{c}",
        },
      },
      {
        name: "Hourly heatmap",
        type: "heatmap",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: heatmapData,
        itemStyle: {
          borderColor: visual?.map.background ?? "#ffffff",
          borderWidth: 1.2,
          borderRadius: 2,
        },
        emphasis: {
          itemStyle: {
            borderColor: visual?.chart.text ?? "#222222",
            borderWidth: 2,
          },
        },
      },
    ],
  };

  return (
    <section className="min-w-0 rounded-xl border bg-card p-4">
      <div className="mb-2">
        <h2 className="font-semibold">Outage Timing Patterns</h2>
      </div>

      {error ? (
        <p role="alert" className="py-8 text-sm text-destructive">
          Unable to load timing statistics.
        </p>
      ) : !data || !visual ? (
        <p className="py-8 text-sm text-muted-foreground">
          Loading timing patterns...
        </p>
      ) : (
        <>
          <div className="w-full overflow-x-auto">
            <div className="relative min-w-180">
              <ReactECharts
                option={option}
                notMerge
                style={{ height: CHART_HEIGHT, width: "100%" }}
              />

              {/* CSS overlays follow the exact same category-grid geometry.
                  They do not intercept ECharts hover events. */}
              <div aria-hidden="true" className="pointer-events-none absolute inset-0">
                {hotspots.map((cell) => {
                  const hotspotStyle: CSSProperties & {
                    "--hotspot-glow": string;
                  } = {
                    left: `${HEATMAP_LEFT + ((cell.hour + 0.5) / 24) * HEATMAP_WIDTH}%`,
                    top: GRID_TOP + ((cell.weekday + 0.5) / 7) * HEATMAP_HEIGHT,
                    width: `calc(${HEATMAP_WIDTH / 24}% - 1.2px)`,
                    height: HEATMAP_HEIGHT / 7 - 1.2,
                    transform: "translate(-50%, -50%)",
                    backgroundColor: colorblind
                      ? "rgba(195, 235, 255, 0.20)"
                      : "rgba(255, 247, 188, 0.20)",
                    borderColor: colorblind ? "#d6f5ff" : "#fff6c7",
                    "--hotspot-glow": colorblind
                      ? "rgba(115, 206, 255, 0.90)"
                      : "rgba(255, 238, 145, 0.95)",
                  };

                  return (
                    <div
                      key={`${cell.weekday}-${cell.hour}`}
                      className="outage-timing-hotspot absolute rounded-[2px] border-2"
                      style={hotspotStyle}
                    />
                  );
                })}
              </div>
            </div>
          </div>

          <div className="mt-2 ml-[80%] flex items-center gap-3 text-xs text-muted-foreground">
            <span>Fewer</span>
            <div
              className="h-2.5 w-44 rounded-sm"
              style={{
                background:
                  "linear-gradient(to right, var(--map-no-data), var(--map-low), var(--map-medium), var(--map-high))",
              }}
            />
            <span>More</span>
          </div>
        </>
      )}

      <style>{`
        @keyframes outageTimingHotspotBreathe {
          0%, 100% {
            opacity: 0.6;
            box-shadow: 0 0 4px 1px var(--hotspot-glow);
          }
          50% {
            opacity: 1;
            box-shadow: 0 0 18px 6px var(--hotspot-glow);
          }
        }
        .outage-timing-hotspot {
          animation: outageTimingHotspotBreathe 2.6s ease-in-out infinite;
        }
        @media (prefers-reduced-motion: reduce) {
          .outage-timing-hotspot {
            animation: none;
            opacity: 1;
            box-shadow: 0 0 6px 2px var(--hotspot-glow);
          }
        }
      `}</style>
    </section>
  );
}
