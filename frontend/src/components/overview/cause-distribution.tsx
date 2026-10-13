
"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { useColorblindMode } from "@/components/theme-provider";
import { getVisualizationColors } from "@/lib/visualization-theme";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
});

type Cause = {
  name: string;
  value: number;
  percentage: number;
};

type Colors = ReturnType<typeof getVisualizationColors>;

export default function CauseDistribution({ year }: { year: string }) {
  const { resolvedTheme } = useTheme();
  const { colorblind } = useColorblindMode();

  const [colors, setColors] = useState<Colors | null>(null);
  const [causes, setCauses] = useState<Cause[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Refresh chart colors when appearance settings change.
  useEffect(() => {
    setColors(getVisualizationColors());
  }, [resolvedTheme, colorblind]);

  // Fetch cause distribution whenever the selected year changes.
  useEffect(() => {
    const controller = new AbortController();

    async function loadCauses() {
      setLoading(true);
      setError(null);

      try {
        const params = new URLSearchParams();
        if (year !== "all") params.set("year", year);

        const response = await fetch(
          `http://127.0.0.1:8000/api/overview/causes?${params}`,
          { signal: controller.signal }
        );

        if (!response.ok) throw new Error("Request failed");

        const result = await response.json();

        if (!controller.signal.aborted) {
          setCauses(result.causes);
        }
      } catch {
        if (!controller.signal.aborted) {
          setError("Unable to load cause distribution.");
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      }
    }

    void loadCauses();
    return () => controller.abort();
  }, [year]);

  const topCause = causes[0];

  const options = {
    color: colors?.chart.series,
    tooltip: {
      trigger: "item",
      formatter: "{b}: {c} events ({d}%)",
    },
    series: [
      {
        type: "pie",
        radius: ["68%", "95%"],
        center: ["50%", "50%"],
        label: { show: false },
        data: causes.map((cause) => ({
          name: cause.name,
          value: cause.value,
        })),
      },
    ],
  };

  return (
    <section className="rounded-xl border bg-card p-4">
      <h2 className="font-semibold">Outage Cause Distribution</h2>

      {loading ? (
        <p className="mt-8 text-sm">Loading...</p>
      ) : error ? (
        <p role="alert" className="mt-8 text-sm text-destructive">
          {error}
        </p>
      ) : causes.length === 0 ? (
        <p className="mt-8 text-sm text-muted-foreground">
          No outages in the selected period.
        </p>
      ) : colors ? (
        <>
          <div className="relative h-52">
            <ReactECharts
              option={options}
              notMerge
              style={{ height: "100%", width: "100%" }}
            />

            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-2xl font-bold">
                {topCause.percentage.toFixed(1)}%
              </span>
              <span className="mt-1 max-w-32 text-center text-xs leading-tight text-muted-foreground">
                {topCause.name}
              </span>
            </div>
          </div>

          <div className="space-y-2">
            {causes.map((cause, index) => (
              <div
                key={cause.name}
                className="flex items-center gap-2 text-xs"
              >
                <span
                  className="h-3 w-3 shrink-0 rounded-sm"
                  style={{
                    backgroundColor:
                      colors.chart.series[index % colors.chart.series.length],
                  }}
                />

                <span className="min-w-0 flex-1">{cause.name}</span>

                <span className="font-medium">
                  {cause.percentage.toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="mt-8 text-sm">Preparing chart...</p>
      )}
    </section>
  );
}
