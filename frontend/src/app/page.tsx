"use client";
import {memo, useEffect, useState} from "react";
import CauseDistribution from "@/components/overview/cause-distribution";
import OutageTrends from "@/components/overview/outage-trends";
import OutageMap, {
    type BoundaryLevel,
    type SelectedArea,
} from "@/components/overview/outage-map";
import TopLocations from "@/components/overview/top-locations";
import SelectedLocation from "@/components/overview/selected-location";
import LongestEvents from "@/components/overview/longest-events";
import OutageTiming from "@/components/overview/outage-timing";

const MemoOutageTrends = memo(OutageTrends);

type TopLocation = {
    name: string;
    municipality: string;
    events: number;
    percentage: number;
    common_cause: string | null;
};

type OverviewKpis = {
    total_outages: number;
    total_duration_hours: number;
    average_duration_minutes: number | null;
    top_municipality: TopLocation | null;
    top_barangay: TopLocation | null;
    comparison: {
        period: string;
        outages_change_pct: number | null;
        duration_change_pct: number | null;
        average_change_pct: number | null;
    } | null;
};

function DeltaBadge({
                        value,
                    }: {
    value: number | null;
}) {
    if (value === null) return null;

    const lower = value < 0;
    const higher = value > 0;

    const colors = lower
        ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
        : higher
            ? "bg-rose-500/10 text-rose-700 dark:text-rose-400"
            : "bg-muted text-muted-foreground";

    return (
        <span
            className={`mt-2 inline-flex rounded-md px-2 py-1 text-xs font-medium ${colors}`}
        >
      {lower ? "↓" : higher ? "↑" : "→"}
            {" "}
            {Math.abs(value).toFixed(2)}%
    </span>
    );
}

export default function OverviewPage() {
    const [year, setYear] = useState("all");
    const [selectedCause, setSelectedCause] = useState<string | null>(null);
    const [level, setLevel] = useState<BoundaryLevel>("municipality");
    const [data, setData] = useState<OverviewKpis | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedArea, setSelectedArea] =
        useState<SelectedArea | null>(null);

    useEffect(() => {
        const controller = new AbortController();

        async function loadKpis() {
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

                const url = `http://127.0.0.1:8000/api/overview/kpis?${params}`;

                const response = await fetch(url, {
                    signal: controller.signal,
                });

                if (!response.ok) {
                    throw new Error("Failed to load KPIs");
                }

                const result: OverviewKpis = await response.json();

                if (!controller.signal.aborted) {
                    setData(result);
                }
            } catch {
                if (!controller.signal.aborted) {
                    setError("Unable to load outage data.");
                }
            } finally {
                if (!controller.signal.aborted) {
                    setLoading(false);
                }
            }
        }

        loadKpis();

        return () => controller.abort();
    }, [year, selectedCause]);

    const format = (value: number | null | undefined) =>
        value == null ? "—" : value.toLocaleString();

    return (
        <section className="space-y-6">
            {/* Header and filters */}
            <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
                        Overview
                    </h1>
                    <p className="text-sm text-muted-foreground">
                        Power outage analytics across Bataan
                    </p>
                </div>

                <select
                    aria-label="Select year"
                    value={year}
                    onChange={(e) => setYear(e.target.value)}
                    className="rounded-md border bg-card px-4 py-2"
                >
                    <option value="all">All Time</option>
                    {Array.from({length: 8}, (_, i) => 2019 + i).map((y) => (
                        <option key={y} value={y}>
                            {y}
                        </option>
                    ))}
                </select>
            </div>

            {/* Error message */}
            {error && (
                <p role="alert" className="text-sm text-destructive">
                    {error}
                </p>
            )}

            {/*KPI Cards */}
            <div aria-busy={loading} className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <div className="h-40 min-w-0 rounded-xl border bg-card p-4">
                    <p className="text-sm text-muted-foreground">
                        Total Outages
                    </p>
                    <h2 className="mt-3 whitespace-nowrap text-xl font-bold tabular-nums 2xl:text-2xl">
                        {loading ? "Loading..." : format(data?.total_outages)}
                    </h2>
                    {!loading && data?.comparison && (
                        <DeltaBadge
                            value={data.comparison.outages_change_pct}
                        />
                    )}
                </div>

                <div className="h-40 min-w-0 rounded-xl border bg-card p-4">
                    <p className="text-sm text-muted-foreground">
                        Total Event-Duration
                    </p>
                    <h2 className="mt-3 whitespace-nowrap text-xl font-bold tabular-nums 2xl:text-2xl">
                        {loading ? "Loading..." : format(data?.total_duration_hours)}
                        {!loading && data && (
                            <span className="ml-1 text-sm font-normal">hrs</span>
                        )}
                    </h2>
                    {!loading && data?.comparison && (
                        <DeltaBadge
                            value={data.comparison.duration_change_pct}
                        />
                    )}
                </div>

                <div className="h-40 min-w-0 rounded-xl border bg-card p-4">
                    <p className="text-sm text-muted-foreground">
                        Average Duration
                    </p>
                    <h2 className="mt-3 whitespace-nowrap text-xl font-bold tabular-nums 2xl:text-2xl">
                        {loading ? "Loading..." : format(data?.average_duration_minutes)}
                        {!loading && data && (
                            <span className="ml-1 text-sm font-normal">min</span>
                        )}
                    </h2>
                    {!loading && data?.comparison && (
                        <DeltaBadge
                            value={data.comparison.average_change_pct}
                        />
                    )}
                </div>

                <div className="h-40 min-w-0 rounded-xl border bg-card p-4">
                    <p className="text-sm text-muted-foreground">
                        Top Municipality
                    </p>
                    <h2 className="mt-3 line-clamp-2 text-xl font-bold" title={data?.top_municipality?.name}>
                        {loading ? "Loading..." : data?.top_municipality?.name ?? "—"}
                    </h2>

                    {!loading && data?.top_municipality && (
                        <div className="mt-2 space-y-1 text-sm text-muted-foreground">
                            <p className="truncate" title={`${format(data.top_municipality.events)} outages · ${data.top_municipality.percentage}%`}>
                                {format(data.top_municipality.events)} outages
                                {" · "}
                                {data.top_municipality.percentage}%
                            </p>
                        </div>
                    )}
                </div>

                <div className="h-40 min-w-0 rounded-xl border bg-card p-4">
                    <p className="text-sm text-muted-foreground">
                        Top Barangay
                    </p>
                    <h2 className="mt-3 line-clamp-2 text-xl font-bold" title={data?.top_barangay ? `${data.top_barangay.name}, ${data.top_barangay.municipality}` : undefined}>
                        {loading ? "Loading..." : data?.top_barangay ? `${data.top_barangay.name}, ${data.top_barangay.municipality}` : "—"}
                    </h2>

                    {!loading && data?.top_barangay && (
                        <div className="mt-2 space-y-1 text-sm text-muted-foreground">
                            <p className="truncate" title={`${format(data.top_barangay.events)} outages · ${data.top_barangay.percentage}%`}>
                                {format(data.top_barangay.events)} outages
                                {" · "}
                                {data.top_barangay.percentage}%
                            </p>
                        </div>
                    )}
                </div>
            </div>
            {/* Row 2: Donut and Trends */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
                <div className="min-w-0 lg:col-span-4 xl:col-span-3">
                    <CauseDistribution
                        year={year}
                        selectedCause={selectedCause}
                        onCauseChange={setSelectedCause}
                    />
                </div>

                <div className="min-w-0 lg:col-span-8 xl:col-span-9">
                    <MemoOutageTrends
                        year={year}
                        selectedCause={selectedCause}
                    />
                </div>
            </div>

            {/* Row 3: Geographic analysis */}
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">

                {/* Map: 50% */}
                <div className="h-[680px] min-w-0 xl:col-span-2">
                    <OutageMap
                        year={year}
                        selectedCause={selectedCause}
                        selectedArea={selectedArea}
                        onAreaSelect={setSelectedArea}
                        level={level}
                        onLevelChange={setLevel}
                    />
                </div>

                {/* Rankings / Selected Location: 25% */}
                <div className="flex h-[680px] min-w-0 flex-col rounded-xl border bg-card p-4">
                    <div className="mb-3 flex shrink-0 items-center justify-between gap-2">
                        <h2 className="font-semibold">
                            {selectedArea
                                ? "Selected Location"
                                : level === "municipality"
                                    ? "Municipality Rankings"
                                    : "Top 10 Barangays"}
                        </h2>

                        {selectedArea && (
                            <button
                                type="button"
                                onClick={() => setSelectedArea(null)}
                                className="text-xs text-muted-foreground hover:text-foreground"
                            >
                                Clear
                            </button>
                        )}
                    </div>

                  <div className="min-h-0 flex-1 overflow-y-auto">
                    {selectedArea ? (
                        <SelectedLocation
                            selectedArea={selectedArea}
                            year={year}
                            selectedCause={selectedCause}
                        />
                    ) : (
                        <TopLocations
                            year={year}
                            selectedCause={selectedCause}
                            level={level}
                            onAreaSelect={setSelectedArea}
                        />
                    )}
                  </div>
                </div>

                {/* Longest Events: 25% */}
                <div className="h-[680px] min-w-0 rounded-xl border bg-card p-4">
                    <LongestEvents
                        year={year}
                        selectedCause={selectedCause}
                        selectedArea={selectedArea}
                    />
                </div>
            </div>

            {/* Row 4: Outage Timing Patterns */}
            <OutageTiming
                year={year}
                selectedCause={selectedCause}
            />

            <p className="text-xs text-muted-foreground">
                Research demonstration using synthetically generated outage events.
                Figures are not verified PENELCO operational records.
                Data for 2026 covers January–June only.
            </p>
        </section>
    );
}
