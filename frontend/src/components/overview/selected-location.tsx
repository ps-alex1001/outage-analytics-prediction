"use client";

import {useEffect, useState} from "react";
import dynamic from "next/dynamic";
import {useTheme} from "next-themes";
import {useColorblindMode} from "@/components/theme-provider";
import {getVisualizationColors} from "@/lib/visualization-theme";
import type {SelectedArea} from "./outage-map";
import Link from "next/link";

const ReactECharts = dynamic(() => import("echarts-for-react"), {
    ssr: false,
});

type AreaRow = {
    municipality_psgc?: string;
    barangay_psgc?: string;
    total_outages: number;
    total_duration_hours: number;
};

type GeographyResponse = {
    municipalities: AreaRow[];
    barangays: AreaRow[];
};

type Props = {
    selectedArea: SelectedArea;
    year: string;
    selectedCause: string | null;
};

// Accept the field names used by common versions of the causes endpoint.
// We normalize them before calculating the leading share.
type CauseItem = {
    name?: string;
    cause_category?: string;
    category?: string;
    count?: number;
    total_outages?: number;
    outages?: number;
    value?: number;
};

type CausesResponse =
    | CauseItem[]
    | { causes?: CauseItem[]; categories?: CauseItem[]; data?: CauseItem[] };

type LeadingCause = {
    name: string;
    count: number;
    percentage: number;
};

export default function SelectedLocation({
                                             selectedArea,
                                             year,
                                             selectedCause,
                                         }: Props) {
    const [data, setData] = useState<GeographyResponse | null>(null);
    const [error, setError] = useState(false);
    const [colors, setColors] = useState({
        selected: "#d97706",
        remainder: "#d1d5db",
    });
    const [leadingCause, setLeadingCause] =
        useState<LeadingCause | null>(null);
    const [causeLoading, setCauseLoading] = useState(false);

    const {resolvedTheme} = useTheme();
    const {colorblind} = useColorblindMode();

    useEffect(() => {
        const visual = getVisualizationColors();
        const styles = getComputedStyle(document.documentElement);

        setColors({
            selected: visual.chart.series[0] || "#d97706",
            remainder: styles.getPropertyValue("--muted").trim() || "#d1d5db",
        });
    }, [resolvedTheme, colorblind]);

    useEffect(() => {
        const controller = new AbortController();
        const params = new URLSearchParams();

        if (year !== "all") params.set("year", year);
        if (selectedCause) {
            params.set("cause_category", selectedCause);
        }

        setData(null);
        setError(false);

        async function load() {
            try {
                const response = await fetch(
                    `http://127.0.0.1:8000/api/overview/geography?${params}`,
                    {signal: controller.signal}
                );

                if (!response.ok) throw new Error("Request failed");

                const result: GeographyResponse = await response.json();

                if (!controller.signal.aborted) {
                    setData(result);
                }
            } catch {
                if (!controller.signal.aborted) setError(true);
            }
        }

        void load();
        return () => controller.abort();
    }, [year, selectedCause]);

    useEffect(() => {
        const controller = new AbortController();

        // A single-cause filter would make a leading-cause
        // comparison meaningless.
        if (selectedCause) {
            setLeadingCause(null);
            setCauseLoading(false);
            return () => controller.abort();
        }

        const params = new URLSearchParams();

        if (year !== "all") {
            params.set("year", year);
        }

        params.set(
            selectedArea.level === "municipality"
                ? "municipality_psgc"
                : "barangay_psgc",
            selectedArea.code
        );

        setLeadingCause(null);
        setCauseLoading(true);

        async function loadLeadingCause() {
            try {
                const response = await fetch(
                    `http://127.0.0.1:8000/api/overview/causes?${params}`,
                    {signal: controller.signal}
                );

                if (!response.ok) throw new Error("Request failed");

                const result: CausesResponse = await response.json();

                const rawCauses = Array.isArray(result)
                    ? result
                    : result.causes ?? result.categories ?? result.data ?? [];

                const causes = rawCauses
                    .map((cause) => ({
                        name: cause.name ?? cause.cause_category ?? cause.category ?? "",
                        count: Number(
                            cause.count ?? cause.total_outages ?? cause.outages ?? cause.value ?? 0
                        ),
                    }))
                    .filter((cause) => cause.name && Number.isFinite(cause.count) && cause.count > 0);

                const total = causes.reduce((sum, cause) => sum + cause.count, 0);
                const leading = [...causes].sort((a, b) => b.count - a.count)[0];

                if (!controller.signal.aborted && leading && total > 0) {
                    setLeadingCause({
                        name: leading.name,
                        count: leading.count,
                        percentage: (leading.count / total) * 100,
                    });
                }
            } catch {
                if (!controller.signal.aborted) {
                    setLeadingCause(null);
                }
            } finally {
                if (!controller.signal.aborted) setCauseLoading(false);
            }
        }

        void loadLeadingCause();

        return () => controller.abort();
    }, [year, selectedCause, selectedArea.level, selectedArea.code]);

    if (error) {
        return <p className="text-sm text-destructive">
            Unable to load location statistics.
        </p>;
    }

    if (!data) {
        return <p className="text-sm text-muted-foreground">
            Loading location statistics...
        </p>;
    }

    const isMunicipality = selectedArea.level === "municipality";

    const selectedRow = isMunicipality
        ? data.municipalities.find(
            (row) => row.municipality_psgc === selectedArea.code
        )
        : data.barangays.find(
            (row) => row.barangay_psgc === selectedArea.code
        );

    if (!selectedRow) {
        return <p className="text-sm text-muted-foreground">
            Location statistics unavailable.
        </p>;
    }

    const parentCount = isMunicipality
        ? data.municipalities.reduce(
            (sum, row) => sum + row.total_outages,
            0
        )
        : data.municipalities.find(
        (row) =>
            row.municipality_psgc === selectedArea.municipalityPsgc
    )?.total_outages ?? 0;

    const outages = selectedRow.total_outages;
    const remainder = Math.max(0, parentCount - outages);

    const share = parentCount > 0
        ? (outages / parentCount) * 100
        : 0;

    const averageMinutes = outages > 0
        ? (selectedRow.total_duration_hours * 60) / outages
        : null;

    const option = {
        animationDuration: 350,
        tooltip: {
            trigger: "item",
            formatter: "{b}: {c} events ({d}%)",
        },
        series: [
            {
                type: "pie",
                radius: ["55%", "76%"],
                center: ["50%", "50%"],
                selectedMode: "single",
                selectedOffset: 6,
                label: {show: false},
                labelLine: {show: false},
                data: [
                    {
                        name: selectedArea.name,
                        value: outages,
                        selected: outages > 0,
                        itemStyle: {color: colors.selected},
                    },
                    {
                        name: "Other areas",
                        value: remainder,
                        itemStyle: {color: colors.remainder},
                    },
                ],
            },
        ],
    };

    const exploreParams = new URLSearchParams();

    if (year !== "all") {
        exploreParams.set("year", year);
    }

    if (selectedCause) {
        exploreParams.set("cause_category", selectedCause);
    }

    exploreParams.set(
        isMunicipality ? "municipality_psgc" : "barangay_psgc",
        selectedArea.code
    );

    const exploreHref = `/spatial?${exploreParams.toString()}`;

    return (
        <div className="flex h-full flex-col gap-3">
            <div>
                <h3 className="text-lg font-semibold leading-tight">
                    {selectedArea.name}
                </h3>
                <p className="mt-1 text-xs text-muted-foreground">
                    {isMunicipality
                        ? "Municipality"
                        : `Barangay of ${selectedArea.municipality ?? "Bataan"}`}
                </p>
            </div>

            <div className="grid grid-cols-[115px_minmax(0,1fr)] items-center gap-2">
                <div className="relative h-[125px]">
                    {parentCount > 0 ? (
                        <ReactECharts
                            option={option}
                            notMerge
                            style={{height: 125, width: "100%"}}
                        />
                    ) : (
                        <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                            No events
                        </div>
                    )}

                    {parentCount > 0 && (
                        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-base font-bold tabular-nums">
                {share.toFixed(1)}%
              </span>
                        </div>
                    )}
                </div>

                <div className="min-w-0">
                    <p className="text-xs text-muted-foreground">
                        Outages
                    </p>
                    <p className="text-2xl font-bold tabular-nums">
                        {outages.toLocaleString()}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                        {isMunicipality
                            ? "Share of Bataan"
                            : "Share of municipality"}
                    </p>
                </div>
            </div>

            <div className="border-t pt-3">
                <p className="text-xs text-muted-foreground">
                    Average event duration
                </p>
                <p className="mt-1 text-lg font-semibold tabular-nums">
                    {averageMinutes === null
                        ? "—"
                        : `${averageMinutes.toFixed(1)} min`}
                </p>
            </div>

            {/* Local leading cause. When a single cause is selected, show the active filter instead. */}
            {selectedCause ? (
                <div className="border-t pt-3">
                    <p className="text-xs text-muted-foreground">Active cause filter</p>
                    <p className="mt-1 truncate text-sm font-medium" title={selectedCause}>
                        {selectedCause}
                    </p>
                </div>
            ) : leadingCause ? (
                <div className="border-t pt-3">
                    <p className="mb-2 text-xs text-muted-foreground">
                        Leading outage cause
                    </p>

                    <p
                        className="truncate text-sm font-medium"
                        title={`${leadingCause.name}: ${leadingCause.count} events (${leadingCause.percentage.toFixed(1)}%)`}
                    >
                        {leadingCause.name}
                    </p>

                    <div
                        className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"
                        title={`${leadingCause.percentage.toFixed(1)}% of local outages`}
                    >
                        <div
                            className="h-full rounded-full bg-primary"
                            style={{
                                width: `${leadingCause.percentage}%`,
                            }}
                        />
                    </div>
                </div>
            ) : (
                <p className="border-t pt-3 text-xs text-muted-foreground">
                    {causeLoading ? "Loading leading cause..." : "Cause breakdown unavailable for this selection."}
                </p>
            )}

            {/* Explore navigation */}
            <Link
                href={exploreHref}
                className="mt-auto flex items-center justify-center rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
            >
                Explore Spatial Analysis →
            </Link>

        </div>
    );
}
