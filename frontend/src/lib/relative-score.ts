import type { ExpressionSpecification } from "maplibre-gl";

export type RelativeScoreScale = { lower: number; upper: number; flat: boolean; count: number; marginFraction: number };
/** One full-batch display range. Never call with just the current playback frame. */
export function relativeScoreScale(values: (number | null | undefined)[], marginFraction = .05): RelativeScoreScale {
  const valid = values.filter((v): v is number => v != null && Number.isFinite(v) && v >= 0 && v <= 1);
  if (!Number.isFinite(marginFraction) || marginFraction < 0) throw new Error("Invalid scale margin");
  let min = Infinity, max = -Infinity;
  for (const value of valid) { min = Math.min(min, value); max = Math.max(max, value); }
  if (!valid.length) return { lower: 0, upper: 0, flat: true, count: 0, marginFraction };
  const margin = (max-min)*marginFraction;
  return { lower: Math.max(0, min-margin), upper: Math.min(1, max+margin), flat: min === max, count: valid.length, marginFraction };
}
export function relativeScoreColor(scale: RelativeScoreScale, ramp: [string,string,string], missing: string): ExpressionSpecification {
  return ["case", ["==", ["feature-state", "available"], true], scale.flat ? ramp[1] :
    ["interpolate", ["linear"], ["number", ["feature-state", "score"], 0],
      scale.lower, ramp[0], (scale.lower+scale.upper)/2, ramp[1], scale.upper, ramp[2]], missing];
}
export function relativeScaleNote(scale: RelativeScoreScale) {
  if (!scale.count) return "No available model scores in this batch.";
  if (scale.flat) return `All available scores equal ${scale.lower.toPrecision(5)}; shown in one neutral color.`;
  return `Raw bounds ${scale.lower.toPrecision(5)}–${scale.upper.toPrecision(5)} · ${scale.marginFraction*100}% range margin, clipped to 0–1 · fixed for the full 24-hour batch.`;
}
