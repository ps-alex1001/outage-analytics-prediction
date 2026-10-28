import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { getVisualizationColors } from "@/lib/visualization-theme";
import type { EChartsOption, EChartsType } from "echarts";
import { DonutChartSkeleton, DonutSkeleton } from "./loading-skeletons";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
  ssr: false,
  loading: () => <DonutChartSkeleton />,
});

type Cause = {
  name: string;
  value: number;
  percentage: number;
};

type Colors = ReturnType<typeof getVisualizationColors>;

type CauseDistributionProps = {
  year: string;
  selectedCause: string | null;
  onCauseChange: (cause: string | null) => void;
};


export default function CauseDistribution({
  year,
  selectedCause,
  onCauseChange,
}: CauseDistributionProps) {

  const { resolvedTheme } = useTheme();

  const [colors, setColors] = useState<Colors | null>(null);
  const [causes, setCauses] = useState<Cause[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const chartRef = useRef<EChartsType | null>(null);

useEffect(() => {
  const chart = chartRef.current;
  if (!chart || chart.isDisposed()) return;

  // Reset previously highlighted slices.
  chart.dispatchAction({
    type: "downplay",
    seriesIndex: 0,
  });

  // Keep the selected slice enlarged.
  if (selectedCause) {
    chart.dispatchAction({
      type: "highlight",
      seriesIndex: 0,
      name: selectedCause,
    });
  }
}, [selectedCause, causes, colors]);
  // Refresh chart colors when appearance settings change.
  useEffect(() => {
    const frame = requestAnimationFrame(() => setColors(getVisualizationColors()));
    return () => cancelAnimationFrame(frame);
  }, [resolvedTheme]);

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

  const displayedCause =
    causes.find((cause) => cause.name === selectedCause) ?? causes[0];

  const options: EChartsOption = {
    color: colors?.chart.series,
    tooltip: {
      trigger: "item",
      formatter: "{b}: {c} events ({d}%)",
    },
    series: [
      {
        type: "pie",
        radius: ["42%", "74%"],
        center: ["50%", "50%"],
        padAngle: 2,
        itemStyle: {
          borderRadius: 5,
          borderColor: colors?.chart.background,
          borderWidth: 2,
        },
        avoidLabelOverlap: true,
        label: {
          show: true,
          position: "outside",
          alignTo: "edge",
          edgeDistance: 8,
          distanceToLabelLine: 5,
          color: colors?.chart.text,
          fontSize: 12,
          lineHeight: 17,
          overflow: "break",
          formatter: (params) => {
            const cause = causes[params.dataIndex];
            return `${params.name}\n${cause.percentage.toFixed(1)}%`;
          },
        },
        labelLine: {
          show: true,
          length: 16,
          length2: 12,
          smooth: 0.15,
          lineStyle: { width: 1.2 },
        },
        labelLayout: { moveOverlap: "shiftY", hideOverlap: true },
        data: causes.map((cause) => ({
          name: cause.name,
          value: cause.value,
          itemStyle: {
            opacity:
                selectedCause && selectedCause !== cause.name
                    ? 0.25
                    : 1,
          },
        })),
        emphasis: {
          scale: true,
          scaleSize: 6,
},
      },
    ],
    media: [{
      query: { maxWidth: 620 },
      option: {
        series: [{ radius: ["40%", "68%"] }],
      },
    }, {
      query: { maxWidth: 480 },
      option: {
        series: [{
          radius: ["34%", "60%"],
          label: { fontSize: 11, lineHeight: 15, edgeDistance: 2 },
          labelLine: { length: 10, length2: 6 },
        }],
      },
    }],
  };

  function selectCause(name: string) {
  onCauseChange(selectedCause === name ? null : name);
}

  return (
    <section aria-busy={loading || !colors} className="flex h-[434px] min-w-0 flex-col rounded-xl border bg-card p-4">
      <div className="flex min-h-10 shrink-0 items-center justify-between gap-2">
        <h2 className="font-semibold">Outage Cause Distribution</h2>

        {selectedCause && (
            <button
                type="button"
                onClick={() => onCauseChange(null)}
                className="shrink-0 rounded-md border px-2 py-1 text-xs hover:bg-muted"
            >
              Clear Cause ×
            </button>
        )}
      </div>

      <div className="min-h-0 flex-1">
        {loading ? (
          <DonutSkeleton />
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
            <div className="relative h-full">
              <ReactECharts
                  option={options}
                  notMerge
                  opts={{ renderer: "svg" }}
                  style={{height: "100%", width: "100%"}}
                  onEvents={{
                    click: (params: { name?: string }) => {
                      if (params.name) {
                        selectCause(params.name);
                      }
                    },
                  }}
                  onChartReady={(chart: EChartsType) => {
                    chartRef.current = chart;

                    if (selectedCause) {
                      chart.dispatchAction({
                        type: "highlight",
                        seriesIndex: 0,
                        name: selectedCause,
                      });
                    }
                  }}
              />

              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-2xl font-bold">
                  {displayedCause.percentage.toFixed(1)}%
                </span>

                <span className="mt-1 max-w-28 text-center text-xs leading-tight text-muted-foreground">
                  {displayedCause.name}
                </span>
              </div>
            </div>

            <ul className="sr-only" aria-label="Outage causes">
              {causes.map((cause) => (
                <li key={cause.name}>{cause.name}: {cause.percentage.toFixed(1)}%</li>
              ))}
            </ul>
          </>
        ) : (
          <DonutSkeleton />
        )}
      </div>
    </section>
  );
}
