import { test } from "node:test";
import assert from "node:assert/strict";
import * as echarts from "echarts";
import { WEATHER_TRACKS, weatherChartOption } from "../src/lib/weather-context.ts";

const fields = WEATHER_TRACKS.flatMap(track => track.fields);
const data = {
  outage_id: "test", status: "partial", timezone: "Asia/Manila",
  outage: { start: "2026-06-30T22:15:00+08:00", end: "2026-07-01T00:45:30+08:00" },
  window: { start: "2026-06-30T16:15:00+08:00", end: "2026-07-01T03:45:30+08:00" },
  source: { municipality: "Balanga", match: "municipality", latitude: 14.65, longitude: 120.52 },
  era5: { status: "available", latitude: 14.75, longitude: 120.5, grid_degrees: 0.25, reason: null, unavailable_variables: ["Total cloud cover"] },
  measurements: Object.fromEntries(fields.map(field => [field, { unit: field.startsWith("soil") ? "m³/m³" : "mm", available: true, missing_hours: 1 }])),
  points: ["2026-06-30T17:00:00+08:00", "2026-06-30T18:00:00+08:00", "2026-06-30T19:00:00+08:00"]
    .map((time, i) => ({ time, ...Object.fromEntries(fields.map(field => [field, i === 1 ? null : 0.4])) })),
};
const palettes = [
  { series: ["#262626", "#525252", "#737373"], text: "#171717", background: "#ffffff", grid: "#e5e5e5" },
  { series: ["#fafafa", "#d4d4d4", "#a3a3a3"], text: "#fafafa", background: "#171717", grid: "#404040" },
];

test("ten chart cards share plot gutters and exact time bounds; nulls remain gaps", () => {
  assert.equal(WEATHER_TRACKS.length, 10);
  for (const track of WEATHER_TRACKS) {
    const option = weatherChartOption(data, palettes[0], "#c83036", track);
    assert.deepEqual(option.grid, { left: 64, right: 16, top: 8, bottom: 38 });
    assert.equal(option.xAxis.min, Date.parse(data.window.start));
    assert.equal(option.xAxis.max, Date.parse(data.window.end));
    for (const series of option.series) {
      assert.equal(series.data[1][1], null);
      assert.equal(series.connectNulls, false);
    }
    const marked = option.series.filter(series => series.markArea);
    assert.equal(marked.length, 1);
    assert.deepEqual(marked[0].markArea.data, [[{ xAxis: Date.parse(data.outage.start) }, { xAxis: Date.parse(data.outage.end) }]]);
    assert.equal(marked[0].markArea.itemStyle.color.colorStops[1].offset, 0.5);
    assert.equal(marked[0].markLine.data[1].xAxis, Date.parse(data.outage.end));
    assert.equal(option.series[0].type, track.kind ?? "line");
  }
});

test("axis labels and shared tooltip use Manila clocks regardless of host timezone", () => {
  const option = weatherChartOption(data, palettes[0], "#c83036", WEATHER_TRACKS[4]);
  assert.match(option.xAxis.axisLabel.formatter(Date.parse(data.points[0].time)), /17:00/);
  const tooltip = option.tooltip.formatter([{ axisValue: Date.parse(data.points[1].time) }]);
  assert.match(tooltip, /18:00.*Manila/);
  assert.match(tooltip, /Soil 7–28 cm: No data/);
  assert.match(tooltip, /Soil 0–7 cm: No data/);
});

test("ECharts renders every card gradient in light/dark at narrow and desktop column sizes", () => {
  for (const colors of palettes) for (const width of [280, 600]) for (const track of WEATHER_TRACKS) {
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width, height: 154 });
    try {
      chart.setOption(weatherChartOption(data, colors, "#c83036", track));
      const svg = chart.renderToSVGString();
      assert.doesNotMatch(svg, /NaN|undefined/);
      assert.match(svg, /linearGradient/);
      assert.equal((svg.match(/fill="url\(#/g) ?? []).length, 1);
      assert.match(svg, /stop-opacity="0.18"/);
    } finally { chart.dispose(); }
  }
});

test("connected chart cursors propagate the same hourly timestamp to all ten cards", () => {
  const charts = WEATHER_TRACKS.map((track, i) => {
    const chart = echarts.init(null, null, { renderer: "svg", ssr: true, width: i % 2 ? 600 : 320, height: 154 });
    chart.setOption(weatherChartOption(data, palettes[0], "#c83036", track));
    chart.group = "weather-context-sync-test";
    return chart;
  });
  try {
    const values = [];
    charts.forEach(chart => chart.on("updateAxisPointer", payload => values.push(payload.axesInfo?.[0]?.value)));
    echarts.connect("weather-context-sync-test");
    const time = Date.parse(data.points[0].time);
    charts[0].dispatchAction({ type: "updateAxisPointer", x: charts[0].convertToPixel({ xAxisIndex: 0 }, time), y: 60 });
    assert.equal(values.length, 10);
    assert.ok(values.every(value => value === time));
  } finally {
    echarts.disconnect("weather-context-sync-test");
    charts.forEach(chart => chart.dispose());
  }
});
