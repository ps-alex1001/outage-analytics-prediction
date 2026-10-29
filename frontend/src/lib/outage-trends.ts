export type TrendPoint = { period: string; total_outages: number };

/** Continuous calendar timeline, retaining missing months as gaps. */
export function monthlyOutageTimeline(points: TrendPoint[]) {
  const valid = points.filter(p => /^\d{4}-(0[1-9]|1[0-2])$/.test(p.period.slice(0, 7)));
  const values = new Map(valid.map(p => [p.period.slice(0, 7), p.total_outages]));
  const periods = [...values.keys()].sort();
  if (!periods.length) return [];
  const [firstYear, firstMonth] = periods[0].split("-").map(Number);
  const [lastYear, lastMonth] = periods.at(-1)!.split("-").map(Number);
  const result: { period: string; value: number | null }[] = [];
  for (let index = firstYear * 12 + firstMonth - 1; index <= lastYear * 12 + lastMonth - 1; index++) {
    const period = `${Math.floor(index / 12)}-${String(index % 12 + 1).padStart(2, "0")}`;
    result.push({ period, value: values.get(period) ?? null });
  }
  return result;
}

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
