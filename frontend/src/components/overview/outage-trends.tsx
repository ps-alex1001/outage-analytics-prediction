
"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { useColorblindMode } from "@/components/theme-provider";
import { getVisualizationColors } from "@/lib/visualization-theme";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
});

type TrendPoint = {
  period: string;
  total_outages: number;
  total_duration_hours: number;
  average_duration_minutes: number | null;
};

type TrendResponse = {
  granularity: "annual" | "monthly";
  points: TrendPoint[];
};

type Metric = "outages" | "duration";

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
  const { colorblind } = useColorblindMode();

  const [colors, setColors] = useState<
    ReturnType<typeof getVisualizationColors> | null
  >(null);

  const [data, setData] = useState<TrendResponse | null>(null);
  const [metric, setMetric] = useState<Metric>("outages");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Update chart colors when the application theme changes.
  useEffect(() => {
    setColors(getVisualizationColors());
  }, [resolvedTheme, colorblind]);

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

  const labels = points.map((point) =>
    data?.granularity === "annual"
      ? point.period
      : months[Number(point.period.slice(5, 7)) - 1]
  );

  // Use only the metric currently selected.
  const values = points.map((point) =>
    metric === "outages"
      ? point.total_outages
      : point.total_duration_hours
  );

  const metricLabel =
    metric === "outages" ? "Outage Events" : "Event-Duration Hours";

  const axisName = metric === "outages" ? "Events" : "Hours";

  const lineColor =
    metric === "outages"
      ? colors?.chart.series[0]
      : colors?.chart.series[1];

  // Calculate the average across displayed periods.
  const periodAverage =
    values.length > 0
      ? values.reduce((sum, value) => sum + value, 0) / values.length
      : 0;

  // ECharts configuration.
  const option = {
    backgroundColor: "transparent",

    color: [lineColor],

    tooltip: {
      trigger: "axis",
      valueFormatter: (value: number) =>
        `${value.toLocaleString()} ${axisName.toLowerCase()}`,
    },

    grid: {
      left: 20,
      right: 25,
      top: 50,
      bottom: 35,
      containLabel: true,
    },

    xAxis: {
      type: "category",
      boundaryGap: false,
      data: labels,
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
      name: axisName,
      min: 0,
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

    series: [
      {
        name: metricLabel,
        type: "line",
        data: values,
        smooth: false,
        symbol: "none",
        symbolSize: 7,
        lineStyle: {
          width: 3,
          color: lineColor,
        },
        itemStyle: {
          color: lineColor,
        },
      },
    ],
  };

  return (
    <section aria-busy={loading} className="h-full rounded-xl border bg-card p-4">
      {/* Chart heading and metric toggle */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Outage Trends</h2>
          <p className="text-xs text-muted-foreground">
            {year === "all" ? "Annual" : "Monthly"} trends
          </p>
        </div>

        <div className="flex rounded-md border p-1">
          {([
            ["outages", "Outages"],
            ["duration", "Duration"],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={metric === value}
              onClick={() => setMetric(value)}
              className={`rounded px-3 py-1 text-xs font-medium ${
                metric === value
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-muted"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Chart content */}
      <div className="mt-4 h-[320px]">
        {loading ? (
          <p className="flex h-full items-center justify-center text-sm">
            Loading trends...
          </p>
        ) : error ? (
          <p
            role="alert"
            className="flex h-full items-center justify-center text-sm text-destructive"
          >
            {error}
          </p>
        ) : !colors || points.length === 0 ? (
          <p className="flex h-full items-center justify-center text-sm text-muted-foreground">
            No trend data available.
          </p>
        ) : (
          <ReactECharts
            option={option}
            notMerge
            style={{ height: 320, width: "100%" }}
          />
        )}
      </div>

      <p className="mt-2 min-h-4 text-xs text-muted-foreground">
        {(year === "all" || year === "2026") && "2026 contains January–June data only."}
      </p>
    </section>
  );
}
