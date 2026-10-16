
"use client";

import { useEffect, useState } from "react";
import type { SelectedArea } from "./outage-map";

type OutageEvent = {
  outage_id: string;
  start_time: string;
  barangay: string;
  municipality: string;
  duration_minutes: number;
};

type EventsResponse = {
  total_matching_events: number;
  has_more: boolean;
  events: OutageEvent[];
};

type Props = {
  year: string;
  selectedCause: string | null;
  selectedArea: SelectedArea | null;
};

function formatDate(value: string) {
  const date = new Date(`${value.slice(0, 10)}T00:00:00Z`);

  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      });
}

function formatDuration(minutes: number) {
  return minutes >= 60
    ? `${(minutes / 60).toFixed(1)} h`
    : `${minutes.toFixed(0)} min`;
}

export default function LongestEvents({
  year,
  selectedCause,
  selectedArea,
}: Props) {
  const [data, setData] = useState<EventsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams();

    if (year !== "all") {
      params.set("year", year);
    }

    if (selectedCause) {
      params.set("cause_category", selectedCause);
    }

    if (selectedArea) {
      params.set(
        selectedArea.level === "municipality"
          ? "municipality_psgc"
          : "barangay_psgc",
        selectedArea.code
      );
    }

    setLoading(true);
    setError(false);

    async function load() {
      try {
        const response = await fetch(
          `http://127.0.0.1:8000/api/overview/longest-events?${params}`,
          { signal: controller.signal }
        );

        if (!response.ok) throw new Error("Request failed");

        const result: EventsResponse = await response.json();

        if (!controller.signal.aborted) {
          setData(result);
        }
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void load();

    return () => controller.abort();
  }, [year, selectedCause, selectedArea]);

  return (
    <div className="flex h-full min-w-0 flex-col">
      <h2 className="font-semibold">Longest Outages</h2>

      <p className="mt-1 text-xs text-muted-foreground">
        {selectedArea
          ? `Within ${selectedArea.name}`
          : "Across Bataan"}
      </p>

      {loading ? (
        <p className="mt-4 text-sm text-muted-foreground">
          Loading events...
        </p>
      ) : error ? (
        <p className="mt-4 text-sm text-destructive">
          Unable to load events.
        </p>
      ) : !data?.events.length ? (
        <p className="mt-4 text-sm text-muted-foreground">
          No matching outage events.
        </p>
      ) : (
        <>
          <div className="mt-4 flex-1">
            <table className="w-full table-fixed text-left text-xs">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="w-[72%] pb-2 font-medium">
                    Location / Date
                  </th>
                  <th className="pb-2 text-right font-medium">
                    Duration
                  </th>
                </tr>
              </thead>

              <tbody>
                {data.events.map((event) => (
                  <tr
                    key={event.outage_id}
                    className="border-b last:border-0"
                  >
                    <td className="py-3 pr-2">
                      <p
                        className="truncate font-medium"
                        title={`${event.barangay}, ${event.municipality}`}
                      >
                        {event.barangay}, {event.municipality}
                      </p>
                      <p className="mt-1 text-muted-foreground">
                        {formatDate(event.start_time)}
                      </p>
                    </td>

                    <td className="py-3 text-right align-top font-semibold tabular-nums">
                      {formatDuration(event.duration_minutes)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {data.has_more && (
            <p className="mt-3 border-t pt-3 text-center text-xs text-muted-foreground">
              {(
                data.total_matching_events - data.events.length
              ).toLocaleString()} more matching events
            </p>
          )}
        </>
      )}
    </div>
  );
}
