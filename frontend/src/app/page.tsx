"use client";
import { useEffect, useState } from "react";
import CauseDistribution from "@/components/overview/cause-distribution";

type TopLocation ={
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
  const [data, setData] = useState<OverviewKpis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
          {Array.from({ length: 8 }, (_, i) => 2019 + i).map((y) => (
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
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-5">
        <div className="rounded-xl border bg-card p-4">
          <p className="text-sm text-muted-foreground">
            Total Outages
          </p>
          <h2 className="mt-3 text-2xl font-bold">
            {loading ? "Loading..." : format(data?.total_outages)}
          </h2>
          {!loading && data?.comparison && (
              <DeltaBadge
                  value={data.comparison.outages_change_pct}
              />
          )}
        </div>

        <div className="rounded-xl border bg-card p-4">
          <p className="text-sm text-muted-foreground">
            Total Event-Duration
          </p>
          <h2 className="mt-3 text-2xl font-bold">
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

        <div className="rounded-xl border bg-card p-4">
          <p className="text-sm text-muted-foreground">
            Average Duration
          </p>
          <h2 className="mt-3 text-2xl font-bold">
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

        <div className="rounded-xl border bg-card p-4">
          <p className="text-sm text-muted-foreground">
            Top Municipality
          </p>
          <h2 className="mt-3 text-xl font-bold">
            {loading ? "Loading..." : data?.top_municipality?.name ?? "—"}
          </h2>

          {!loading && data?.top_municipality && (
            <div className="mt-2 space-y-1 text-sm text-muted-foreground">
              <p>
                {format(data.top_municipality.events)} outages
                {" · "}
                {data.top_municipality.percentage}%
              </p>
            </div>
          )}
        </div>

        <div className="rounded-xl border bg-card p-4">
          <p className="text-sm text-muted-foreground">
            Top Barangay
          </p>
          <h2 className="mt-3 text-xl font-bold">
            {loading ? "Loading..." :  data?.top_barangay ? `${data.top_barangay.name}, ${data.top_barangay.municipality}`: "—"}
          </h2>

          {!loading && data?.top_barangay && (
            <div className="mt-2 space-y-1 text-sm text-muted-foreground">
              <p>
                {format(data.top_barangay.events)} outages
                {" · "}
                {data.top_barangay.percentage}%
              </p>
            </div>
          )}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-4 xl:col-span-3">
          <CauseDistribution
              year={year}
              selectedCause={selectedCause}
              onCauseChange={setSelectedCause}
          />
        </div>

        <div className="rounded-xl border bg-card p-4 lg:col-span-8 xl:col-span-9">
          <h2 className="font-semibold">Outage Trends</h2>
          <p className="text-sm text-muted-foreground">
            Annual and monthly trends will appear here.
          </p>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        Research demonstration using synthetically generated outage events.
        Figures are not verified PENELCO operational records.
        Data for 2026 covers January–June only.
      </p>
    </section>
  );
}
