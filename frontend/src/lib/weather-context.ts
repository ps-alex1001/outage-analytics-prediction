import type { EChartsOption, SeriesOption } from "echarts";

export type WeatherField = "precipitation_mm" | "wind_speed_kmh" | "wind_gusts_kmh" |
  "temperature_c" | "relative_humidity_pct" | "soil_moisture_0_7" | "soil_moisture_7_28" |
  "surface_pressure_hpa" | "cape_j_kg" | "column_water_vapour_kg_m2" | "total_totals_index_k" | "cloud_base_height_m";
export type WeatherPoint = { time: string } & Record<WeatherField, number | null>;
export type WeatherContextData = {
  outage_id: string;
  status: "complete" | "partial" | "missing";
  timezone: "Asia/Manila";
  outage: { start: string; end: string };
  window: { start: string; end: string };
  source: { municipality: string; match: "municipality" | "unmatched"; latitude: number | null; longitude: number | null };
  era5: { status: "available" | "partial" | "unavailable" | "unmatched"; latitude: number | null; longitude: number | null;
    grid_degrees: number; reason: string | null; unavailable_variables: string[] };
  measurements: Record<WeatherField, { column: string; unit: string; source: "municipal" | "era5";
    available: boolean; missing_hours: number; reason?: string | null }>;
  points: WeatherPoint[];
};

export type WeatherTrack = { title: string; note: string; fields: WeatherField[]; names: string[]; min?: number; max?: number; kind?: "bar" };
export const WEATHER_TRACKS: WeatherTrack[] = [
  { title: "Precipitation (mm)", note: "Hourly total", fields: ["precipitation_mm"], names: ["Precipitation"], min: 0, kind: "bar" },
  { title: "Wind (km/h)", note: "Speed: solid · Gusts: dashed", fields: ["wind_speed_kmh", "wind_gusts_kmh"], names: ["Wind speed", "Wind gusts"], min: 0 },
  { title: "Temperature (°C)", note: "2 m above ground", fields: ["temperature_c"], names: ["Temperature"] },
  { title: "Relative humidity (%)", note: "2 m above ground", fields: ["relative_humidity_pct"], names: ["Relative humidity"], min: 0, max: 100 },
  { title: "Soil moisture (m³/m³)", note: "0–7 cm: solid · 7–28 cm: dashed", fields: ["soil_moisture_0_7", "soil_moisture_7_28"], names: ["Soil 0–7 cm", "Soil 7–28 cm"], min: 0 },
  { title: "Surface pressure (hPa)", note: "Municipal surface atmospheric pressure", fields: ["surface_pressure_hpa"], names: ["Surface pressure"] },
  { title: "CAPE (J/kg)", note: "Convective available potential energy", fields: ["cape_j_kg"], names: ["CAPE"], min: 0 },
  { title: "Column water vapour (kg/m²)", note: "Total vertically integrated water vapour", fields: ["column_water_vapour_kg_m2"], names: ["Column water vapour"], min: 0 },
  { title: "Total Totals Index (K)", note: "Atmospheric stability index; not air temperature", fields: ["total_totals_index_k"], names: ["Total Totals Index"] },
  { title: "Cloud-base height (m)", note: "Cloud-base height; gaps remain missing", fields: ["cloud_base_height_m"], names: ["Cloud-base height"], min: 0 },
];

const clock = new Intl.DateTimeFormat("en-PH", { timeZone: "Asia/Manila", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const date = new Intl.DateTimeFormat("en-PH", { timeZone: "Asia/Manila", day: "2-digit", month: "short" });
const fullTime = new Intl.DateTimeFormat("en-PH", { timeZone: "Asia/Manila", dateStyle: "medium", timeStyle: "short", hourCycle: "h23" });

export function weatherValue(value: number | null) {
  return value === null ? "No data" : value.toLocaleString("en-PH", { maximumFractionDigits: 3 });
}

type ChartColors = { series: string[]; background: string; text: string; grid: string };

/** Equal plot gutters and explicit epoch bounds align the connected charts. */
export function weatherChartOption(data: WeatherContextData, colors: ChartColors, outageColor: string, track: WeatherTrack): EChartsOption {
  const series: SeriesOption[] = [];
  const outageStart = Date.parse(data.outage.start), outageEnd = Date.parse(data.outage.end);
  const gradient = {
    type: "linear" as const, x: 0, y: 0, x2: 1, y2: 0,
    colorStops: [{ offset: 0, color: "rgba(220,38,38,0.025)" },
      { offset: 0.5, color: "rgba(220,38,38,0.18)" },
      { offset: 1, color: "rgba(220,38,38,0.025)" }],
  };
    track.fields.forEach((field, fieldIndex) => {
      series.push({
        name: track.names[fieldIndex], type: track.kind ?? "line",
        data: data.points.map(p => [Date.parse(p.time), p[field]]),
        // Preserve gaps and exact hourly measurements, without smoothing.
        connectNulls: false, showSymbol: data.points.length <= 48, symbolSize: 3,
        lineStyle: { width: 1.8, type: fieldIndex ? "dashed" : "solid" },
        itemStyle: { color: colors.series[fieldIndex ? 2 : 0] },
        barMaxWidth: 18, z: 3,
        ...(track.fields.length > 1 ? { endLabel: { show: true, formatter: track.names[fieldIndex], color: colors.text, fontSize: 10 }, labelLayout: { moveOverlap: "shiftY" as const } } : {}),
        ...(fieldIndex === 0 ? {
          markArea: { silent: true, z: 0, itemStyle: { color: gradient },
            data: [[{ xAxis: outageStart }, { xAxis: outageEnd }]] },
          markLine: { silent: true, symbol: "none", label: { show: false },
            lineStyle: { color: outageColor, opacity: 0.5, width: 1, type: "dashed" },
            data: [{ name: "Outage start", xAxis: outageStart }, { name: "Outage end", xAxis: outageEnd }] },
        } : {}),
      });
    });
  return {
    animation: false, backgroundColor: "transparent",
    aria: { enabled: true, description: `${track.title}, hourly in Philippine time. Red shading marks the exact selected outage. An hourly data table follows the charts.` },
    grid: { left: 64, right: 100, top: 8, bottom: 38 },
    xAxis: {
      type: "time",
      min: Date.parse(data.window.start), max: Date.parse(data.window.end),
      minInterval: 3600000, splitNumber: 4,
      axisLabel: { color: colors.text, fontSize: 11, hideOverlap: true,
        formatter: (value: number) => `${clock.format(value)}\n${date.format(value)}` },
      axisLine: { lineStyle: { color: colors.grid } }, axisTick: { show: false },
      splitLine: { show: false },
      axisPointer: { show: true, snap: true, label: { show: false }, lineStyle: { color: colors.text, opacity: 0.35 } },
    },
    yAxis: {
      type: "value", splitNumber: 2, scale: track.min === undefined,
      min: track.min, max: track.max,
      axisLabel: { color: colors.text, fontSize: 10 },
      splitLine: { lineStyle: { color: colors.grid, opacity: 0.6 } },
    },
    tooltip: {
      trigger: "axis", confine: true, backgroundColor: colors.background,
      borderColor: colors.grid, textStyle: { color: colors.text, fontSize: 12 },
      formatter: (params: unknown) => {
        const first = (Array.isArray(params) ? params[0] : params) as { axisValue?: number } | undefined;
        const point = data.points.find(p => Date.parse(p.time) === Number(first?.axisValue));
        if (!point) return "";
        return [`<strong>${fullTime.format(new Date(point.time))} · Manila</strong>`,
          ...track.fields.map((field, i) =>
            `${track.names[i]}: ${weatherValue(point[field])} ${point[field] === null ? "" : data.measurements[field].unit}`)].join("<br/>");
      },
    },
    series,
  };
}
