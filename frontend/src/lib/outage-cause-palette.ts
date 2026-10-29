export const causeColors: Record<string, string> = {
  Weather: "#071ac8",
  "Equipment Failure": "#4b94ff",
  "Tree contact": "#2460df",
  Supply: "#ff7a1a",
  Other: "#858fa1",
};

export function groupOutageCauses(causes: { name: string; value: number }[]) {
  const grouped = new Map<string, number>();
  for (const cause of causes) {
    const name = ["Animal", "Others"].includes(cause.name) ? "Other" : cause.name;
    grouped.set(name, (grouped.get(name) ?? 0) + cause.value);
  }
  const total = causes.reduce((sum, cause) => sum + cause.value, 0);
  return [...grouped].map(([name, value]) => ({ name, value, percentage: total ? value / total * 100 : 0 }))
    .sort((a, b) => b.value - a.value);
}
