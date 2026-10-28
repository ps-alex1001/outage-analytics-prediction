export type TrendPoint = { period: string; total_outages: number };

/** Align years to calendar months; absent coverage stays missing, not zero. */
export function yearlyOutageSeries(points: TrendPoint[]) {
  const years = new Map<string, (number | null)[]>();
  for (const point of points) {
    const year = point.period.slice(0, 4);
    const month = Number(point.period.slice(5, 7)) - 1;
    if (month < 0 || month > 11 || !Number.isInteger(month)) continue;
    if (!years.has(year)) years.set(year, Array<number | null>(12).fill(null));
    years.get(year)![month] = point.total_outages;
  }
  return [...years].sort(([a], [b]) => a.localeCompare(b)).map(([name, data]) => ({ name, data }));
}
