
"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { getVisualizationColors } from "@/lib/visualization-theme";
import { monthlyOutageTimeline, yearlyOutageSeries, type TrendPoint } from "@/lib/outage-trends";
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
  const timeline = monthlyOutageTimeline(points);
  const allTime = year === "all";
  const sections = series.map(item => {
    const start = timeline.findIndex(point => point.period.startsWith(item.name));
    const end = timeline.findLastIndex(point => point.period.startsWith(item.name));
    return { year: item.name, start, end, middle: Math.floor((start + end) / 2) };
  });
  const blues = resolvedTheme === "dark"
    ? ["#dbeafe", "#bfdbfe", "#93c5fd", "#60a5fa", "#3b82f6", "#38bdf8", "#0ea5e9", "#2563eb"]
    : ["#a3c9ed", "#7fb3df", "#559bd3", "#3282bf", "#1769aa", "#0c5594", "#08437c", "#062e60"];
  const yearColor = (name: string) => blues[((Number(name) - 2019) % blues.length + blues.length) % blues.length];

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
      bottom: allTime ? 60 : 35,
      containLabel: true,
    },

    xAxis: [{
      type: "category",
      boundaryGap: false,
      data: allTime ? timeline.map(point => point.period) : months,
      axisLabel: {
        color: colors?.chart.text,
        ...(allTime ? {
          interval: 0,
          fontSize: 9,
          formatter: (period: string) => {
            const month = Number(period.slice(5, 7)) - 1;
            return month % 3 === 0 ? months[month] : "";
          },
        } : {}),
      },
      axisLine: {
        lineStyle: {
          color: colors?.chart.grid,
        },
      },
    }, ...(allTime ? [{
      type: "category",
      position: "bottom",
      offset: 23,
      boundaryGap: false,
      data: timeline.map(point => point.period),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        interval: 0,
        color: colors?.chart.text,
        fontWeight: 600,
        formatter: (_: string, index: number) => {
          const section = sections.find(section => section.middle === index);
          return section ? `{year${section.year}|${section.year}}` : "";
        },
        rich: Object.fromEntries(sections.map(section => [
          `year${section.year}`,
          { color: yearColor(section.year), fontWeight: 600 },
        ])),
      },
    }] : [])],

    ...(allTime ? {
      visualMap: {
        show: false,
        dimension: 0,
        seriesIndex: 0,
        pieces: sections.map((section, index) => ({
          gte: section.start,
          ...(index < sections.length - 1 ? { lt: sections[index + 1].start } : { lte: section.end }),
          color: yearColor(section.year),
        })),
      },
    } : {}),

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

    series: allTime ? [{
      name: "Outages",
      type: "line",
      data: timeline.map(point => point.value),
      connectNulls: false,
      smooth: false,
      symbol: "none",
      lineStyle: { width: 2 },
      markLine: {
        silent: true,
        symbol: "none",
        label: { show: false },
        lineStyle: { color: colors?.chart.grid, type: "dashed", width: 1 },
        data: sections.slice(1).map(section => ({ xAxis: section.start })),
      },
    }] : series.map((item, index) => ({
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
      })),
  };

  return (
    <section aria-busy={loading || !colors} className="flex h-[434px] min-w-0 flex-col rounded-xl border bg-card p-4">
      {/* Chart heading */}
      <div className="flex min-h-10 shrink-0 flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Outage Trends</h2>

        </div>

      </div>



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
        ) : (
          <ReactECharts
            option={option}
            notMerge
            style={{ height: "100%", width: "100%" }}
          />
        )}
      </div>


    </section>
  );
}
