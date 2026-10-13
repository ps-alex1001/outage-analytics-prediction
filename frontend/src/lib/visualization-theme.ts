/** Read inside a client component after mounting, and again when themes change. */
export function getVisualizationColors() {
  const styles = getComputedStyle(document.documentElement);
  const read = (name: string) => styles.getPropertyValue(name).trim();

  return {
    chart: {
      series: [1, 2, 3, 4, 5].map((index) => read(`--chart-${index}`)),
      background: read("--viz-background"),
      text: read("--viz-text"),
      grid: read("--viz-grid"),
    },
    map: {
      background: read("--map-background"),
      boundary: read("--map-boundary"),
      low: read("--map-low"),
      medium: read("--map-medium"),
      high: read("--map-high"),
      noData: read("--map-no-data"),
    },
  };
}
