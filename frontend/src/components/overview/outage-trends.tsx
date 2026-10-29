
"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { getVisualizationColors } from "@/lib/visualization-theme";
import { yearlyOutageSeries, type TrendPoint } from "@/lib/outage-trends";
import { TrendSkeleton } from "./loading-skeletons";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
  loading: () => <TrendSkeleton />,
});

type TrendResponse = {
  granularity: "monthly";
  points: TrendPoint[];
};

const months = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

export default function OutageTrends({
  year,
  selectedCause,
}: {
  year: string;
  selectedCause: string | null;
}) {
  const { resolvedTheme } = useTheme();

  const [colors, setColors] = useState<
    ReturnType<typeof getVisualizationColors> | null
  >(null);

  const [data, setData] = useState<TrendResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hiddenYears, setHiddenYears] = useState<Record<string, boolean>>({});

  // Update chart colors when the application theme changes.
  useEffect(() => {
    const frame = requestAnimationFrame(() => setColors(getVisualizationColors()));
    return () => cancelAnimationFrame(frame);
  }, [resolvedTheme]);

  // Fetch data whenever the year or cause filter changes.
  useEffect(() => {
    const controller = new AbortController();

    async function loadTrends() {
      setLoading(true);
      setError(null);
      setData(null);

      try {
        const params = new URLSearchParams();

        if (year !== "all") {
          params.set("year", year);
        }

        if (selectedCause) {
          params.set("cause_category", selectedCause);
        }

        const response = await fetch(
          `http://127.0.0.1:8000/api/overview/trends?${params.toString()}`,
          { signal: controller.signal }
        );

        if (!response.ok) {
          throw new Error("Failed to load trends");
        }

        const result: TrendResponse = await response.json();

        if (!controller.signal.aborted) {
          setData(result);
        }
      } catch {
        if (!controller.signal.aborted) {
          setError("Unable to load outage trends.");
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    void loadTrends();

    return () => controller.abort();
  }, [year, selectedCause]);

  // Prepare data for the chart.
  const points = data?.points ?? [];

  const series = yearlyOutageSeries(points);
  const blues = resolvedTheme === "dark"
    ? ["#dbeafe", "#bfdbfe", "#93c5fd", "#60a5fa", "#3b82f6", "#38bdf8", "#0ea5e9", "#2563eb"]
    : ["#a3c9ed", "#7fb3df", "#559bd3", "#3282bf", "#1769aa", "#0c5594", "#08437c", "#062e60"];
  const yearColor = (name: string) => blues[((Number(name) - 2019) % blues.length + blues.length) % blues.length];
  const hasVisibleYears = year !== "all" || series.some(item => !hiddenYears[item.name]);

  // ECharts configuration.
  const option = {
    backgroundColor: "transparent",

    color: blues,
    aria: { enabled: true },

    tooltip: {
      trigger: "axis",
      confine: true,
      valueFormatter: (value: number) =>
        value == null ? "No coverage" : `${value.toLocaleString()} outages`,
    },

    grid: {
      left: 20,
      right: 25,
      top: 35,
      bottom: 35,
      containLabel: true,
    },

    xAxis: {
      type: "category",
      boundaryGap: false,
      data: months,
      axisLabel: {
        color: colors?.chart.text,
      },
      axisLine: {
        lineStyle: {
          color: colors?.chart.grid,
        },
      },
    },

    yAxis: {
      type: "value",
      name: "Outages",
      min: 0,
      minInterval: 1,
      nameTextStyle: {
        color: colors?.chart.text,
      },
      axisLabel: {
        color: colors?.chart.text,
      },
      splitLine: {
        lineStyle: {
          color: colors?.chart.grid,
        },
      },
    },

    series: series.map((item, index) => ({
        ...item,
        // Partial years stop at the last observed month.
        data: item.data.slice(0, item.data.findLastIndex(value => value !== null) + 1),
        type: "line",
        connectNulls: false,
        smooth: false,
        symbol: "none",
        symbolSize: 7,
        lineStyle: {
          width: 2,
          type: index < 5 ? "solid" : "dashed",
          color: yearColor(item.name),
        },
        itemStyle: { color: yearColor(item.name) },
        emphasis: { focus: "series" },
      })).filter(item => year !== "all" || !hiddenYears[item.name]),
  };

  return (
    <section aria-busy={loading || !colors} className="flex h-[434px] min-w-0 flex-col rounded-xl border bg-card p-4">
      {/* Chart heading */}
      <div className="flex min-h-10 shrink-0 flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Outage Trends</h2>
          <p className="text-xs text-muted-foreground">
            {year === "all" ? "Monthly outage counts by year" : `${year} monthly outage counts`}
          </p>
        </div>

      </div>

      {year === "all" && series.length > 0 && <div className="mt-2 flex shrink-0 flex-wrap items-center gap-1" role="group" aria-label="Years to compare">
        {series.map((item, index) => <button
          key={item.name}
          type="button"
          aria-pressed={!hiddenYears[item.name]}
          onClick={() => setHiddenYears(previous => ({ ...previous, [item.name]: !previous[item.name] }))}
          className={`inline-flex min-h-11 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors motion-reduce:transition-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${hiddenYears[item.name] ? "border-transparent text-muted-foreground hover:bg-muted" : "border-primary/25 bg-primary/10 text-primary hover:bg-primary/15"}`}
        >
          <span aria-hidden="true" className="w-3 border-t-2" style={{ borderColor: yearColor(item.name), borderStyle: index < 5 ? "solid" : "dashed", opacity: hiddenYears[item.name] ? .35 : 1 }} />
          {item.name}
        </button>)}
        <button type="button" className="min-h-11 rounded-md px-2 text-xs text-primary hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring" aria-label="Show all years" onClick={() => setHiddenYears({})}>All</button>
        <button type="button" className="min-h-11 rounded-md px-2 text-xs text-muted-foreground hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring" aria-label="Clear comparison years" onClick={() => setHiddenYears(Object.fromEntries(series.map(item => [item.name, true])))}>Clear</button>
      </div>}

      {/* Chart content */}
      <div className="mt-4 min-h-0 flex-1">
        {loading ? (
          <TrendSkeleton />
        ) : error ? (
          <p
            role="alert"
            className="flex h-full items-center justify-center text-sm text-destructive"
          >
            {error}
          </p>
        ) : !colors ? (
          <TrendSkeleton />
        ) : points.length === 0 ? (
          <p className="flex h-full items-center justify-center text-sm text-muted-foreground">
            No trend data available.
          </p>
        ) : !hasVisibleYears ? (
          <p role="status" className="flex h-full items-center justify-center text-sm text-muted-foreground">Select years above to compare outage counts.</p>
        ) : (
          <ReactECharts
            option={option}
            notMerge
            style={{ height: "100%", width: "100%" }}
          />
        )}
      </div>

      <p className="mt-2 min-h-4 shrink-0 text-xs text-muted-foreground">
        {(year === "all" || year === "2026") && "2026 contains January–June data only."}
      </p>
    </section>
  );
}
