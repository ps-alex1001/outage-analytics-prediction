"use client";

import {useEffect, useState} from "react";
import {useTheme} from "next-themes";
import {getVisualizationColors} from "@/lib/visualization-theme";
import type {SelectedArea} from "./outage-map";
import Link from "next/link";
import { LocationSkeleton } from "./loading-skeletons";
import { Skeleton } from "@/components/ui/skeleton";

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

    useEffect(() => {
        const frame = requestAnimationFrame(() => {
        const visual = getVisualizationColors();
        const styles = getComputedStyle(document.documentElement);

        setColors({
            selected: visual.chart.series[0] || "#d97706",
            remainder: styles.getPropertyValue("--muted").trim() || "#d1d5db",
        });
        });
        return () => cancelAnimationFrame(frame);
    }, [resolvedTheme]);

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
        return <LocationSkeleton />;
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
        <div className="flex h-full flex-col gap-3 [container-type:inline-size]">
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

            <div className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] items-center gap-3">
                <div className="relative aspect-square w-full max-w-44">
                    {parentCount > 0 ? (
                        <svg
                            viewBox="0 0 160 160"
                            role="img"
                            aria-label={`${selectedArea.name}: ${share.toFixed(1)}% of ${isMunicipality ? "Bataan" : "the municipality"}`}
                            className="size-full"
                        >
                            <title>{`${selectedArea.name}: ${outages.toLocaleString()} events (${share.toFixed(1)}%). Other areas: ${remainder.toLocaleString()} events.`}</title>
                            <circle cx="80" cy="80" r="66" fill="none" stroke={colors.remainder} strokeWidth="16" />
                            {share > 0 && (
                                <circle
                                    cx="80"
                                    cy="80"
                                    r="66"
                                    fill="none"
                                    stroke={colors.selected}
                                    strokeWidth="16"
                                    strokeLinecap="round"
                                    pathLength="100"
                                    strokeDasharray={`${Math.min(100, share)} 100`}
                                    transform="rotate(-90 80 80)"
                                />
                            )}
                        </svg>
                    ) : (
                        <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                            No events
                        </div>
                    )}

                    {parentCount > 0 && (
                        <div aria-hidden="true" className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-1">
                            <span className="text-[clamp(1rem,6cqi,1.5rem)] leading-none font-bold tabular-nums">
                                {share.toFixed(1)}%
                            </span>
                            <span className="max-w-20 text-center text-[clamp(0.625rem,3.5cqi,0.75rem)] leading-tight text-muted-foreground">
                                {isMunicipality ? "of Bataan" : "of municipality"}
                            </span>
                        </div>
                    )}
                </div>

                <div className="min-w-0">
                    <p className="text-sm text-muted-foreground">
                        Outages
                    </p>
                    <p className="mt-1 whitespace-nowrap text-[clamp(1.5rem,11cqi,3rem)] leading-none font-bold tracking-tight tabular-nums">
                        {outages.toLocaleString()}
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
            ) : causeLoading ? (
                <div role="status" aria-label="Loading leading outage cause" className="space-y-2 border-t pt-3">
                    <p className="text-xs text-muted-foreground">Leading outage cause</p>
                    <Skeleton className="h-4 w-3/4" />
                    <Skeleton className="h-1.5 w-full" />
                </div>
            ) : (
                <p className="border-t pt-3 text-xs text-muted-foreground">
                    Cause breakdown unavailable for this selection.
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
