"use client";

import { useEffect, useState } from "react";
import type {
  BoundaryLevel,
  SelectedArea,
} from "@/components/overview/outage-map";

type LocationRow = {
  municipality_psgc: string;
  barangay_psgc?: string;
  municipality: string;
  barangay?: string;
  total_outages: number;
};

type GeographyResponse = {
  municipalities: LocationRow[];
  barangays: LocationRow[];
};

type Props = {
  year: string;
  selectedCause: string | null;
  level: BoundaryLevel;
  onAreaSelect: (area: SelectedArea | null) => void;
};

export default function TopLocations({
  year,
  selectedCause,
  level,
  onAreaSelect,
}: Props) {
  const [data, setData] = useState<GeographyResponse | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();

    if (year !== "all") params.set("year", year);
    if (selectedCause) params.set("cause_category", selectedCause);

    setData(null);
    setError(false);

    async function load() {
      try {
        const response = await fetch(
          `http://127.0.0.1:8000/api/overview/geography?${params}`,
          { signal: controller.signal }
        );

        if (!response.ok) throw new Error("Request failed");

        const result: GeographyResponse = await response.json();

        if (!controller.signal.aborted) setData(result);
      } catch {
        if (!controller.signal.aborted) setError(true);
      }
    }

    void load();
    return () => controller.abort();
  }, [year, selectedCause]);

  const rows = data
    ? [...(level === "municipality"
        ? data.municipalities
        : data.barangays)]
        .sort(
          (a, b) =>
            b.total_outages - a.total_outages ||
            (level === "municipality"
              ? a.municipality.localeCompare(b.municipality)
              : (a.barangay ?? "").localeCompare(b.barangay ?? ""))
        )
        .slice(0, level === "municipality" ? 12 : 10)
    : [];

  const maxCount = Math.max(
    1,
    ...rows.map((row) => row.total_outages)
  );

  function selectLocation(row: LocationRow) {
    const code =
      level === "municipality"
        ? row.municipality_psgc
        : row.barangay_psgc;

    if (!code) return;

    onAreaSelect({
      level,
      code,
      name:
        level === "municipality"
          ? row.municipality
          : row.barangay ?? code,
      municipality: row.municipality,
      municipalityPsgc: row.municipality_psgc,
    });
  }

  if (error) {
    return <p className="text-sm text-destructive">
      Unable to load rankings.
    </p>;
  }

  if (!data) {
    return <p className="text-sm text-muted-foreground">
      Loading rankings...
    </p>;
  }


    return (
        <div className="space-y-0.5">
            {rows.map((row, index) => {
                const name =
                    level === "municipality"
                        ? row.municipality
                        : row.barangay ?? "Unknown barangay";

                const fullName =
                    level === "barangay"
                        ? `${name}, ${row.municipality}`
                        : name;

                return (
                    <button
                        key={
                            level === "municipality"
                                ? row.municipality_psgc
                                : row.barangay_psgc
                        }
                        type="button"
                        onClick={() => selectLocation(row)}
                        className="w-full rounded-md px-2 py-1.5 text-left transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
                    >
                        <div className="flex min-w-0 items-center justify-between gap-2">
            <span
                className="min-w-0 truncate"
                title={fullName}
            >
              <span className="text-sm font-medium">
                {index + 1}. {name}
              </span>

                {level === "barangay" && (
                    <span className="text-[11px] text-muted-foreground">
                  , {row.municipality}
                </span>
                )}
            </span>

                            <span className="shrink-0 text-xs font-semibold tabular-nums">
              {row.total_outages.toLocaleString()}
            </span>
                        </div>

                        <div className="mt-1 h-1 overflow-hidden rounded-full bg-muted">
                            <div
                                className="h-full rounded-full bg-primary"
                                style={{
                                    width: `${(row.total_outages / maxCount) * 100}%`,
                                }}
                            />
                        </div>
                    </button>
                );
            })}
        </div>
    );
}
