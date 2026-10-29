import { test } from "node:test";
import assert from "node:assert/strict";
import { controls, differenceSummary, differenceText, nextPlaybackFrame, runScenario, scenarioFillColor, scenarioMapScores, zeroAdjustments } from "../src/lib/scenario.ts";

function batch() {
  return { hours: 24, barangay_count: 1, weather: { stale: false }, points: Array.from({ length: 24 }, (_, i) => {
    const cutoff = new Date(Date.UTC(2026, 9, 29, i)).toISOString();
    return { barangay_psgc: "0300801001", prediction_cutoff: cutoff, target_start: cutoff,
      target_end: new Date(Date.parse(cutoff) + 3600000).toISOString(),
      baseline_score: i ? null : .003, scenario_score: i ? null : .004, difference: i ? null : .001 };
  }) };
}

test("one simulation request retains null scores and string PSGCs; playback stops without requesting inference", async () => {
  const original = globalThis.fetch; let calls = 0;
  globalThis.fetch = async (url, options) => {
    calls++;
    assert.ok(url.endsWith("/api/scenario/simulate"));
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { mode: "storm", adjustments: zeroAdjustments });
    return Response.json(batch());
  };
  try {
    const result = await runScenario("storm", zeroAdjustments);
    const saved = JSON.stringify(result);
    for (const speed of [.5, 1, 2]) {
      assert.equal(1000 / speed, speed === .5 ? 2000 : speed === 1 ? 1000 : 500);
      let state = { hour: 0, playing: true }; const visited = [0];
      while (state.playing) { state = nextPlaybackFrame(state.hour); visited.push(state.hour); }
      assert.deepEqual(visited, Array.from({ length: 24 }, (_, i) => i));
      assert.deepEqual(nextPlaybackFrame(23), { hour: 23, playing: false });
    }
    assert.equal(calls, 1);
    assert.equal(result.points[0].barangay_psgc, "0300801001");
    assert.equal(result.points[1].scenario_score, null);
    assert.equal(JSON.stringify(result), saved);
  } finally { globalThis.fetch = original; }
});

test("stale batches, invalid scores and service failures are rejected", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => Response.json({ detail: "Model unavailable" }, { status: 503 });
    await assert.rejects(runScenario("constant", zeroAdjustments), /Model unavailable/);
    for (const modify of [b => { b.weather.stale = true; }, b => { b.points[0].scenario_score = 2; }, b => { b.points[0].barangay_psgc = 300801001; }]) {
      const b = batch(); modify(b);
      globalThis.fetch = async () => Response.json(b);
      await assert.rejects(runScenario("constant", zeroAdjustments), /incompatible/);
    }
  } finally { globalThis.fetch = original; }
});

test("maximum slider values are serialized without being dropped", async () => {
  const original = globalThis.fetch;
  const adjustments = Object.fromEntries(Object.entries(controls).map(([key, c]) => [key, c.max]));
  globalThis.fetch = async (_, options) => {
    assert.deepEqual(JSON.parse(options.body), { mode: "constant", adjustments });
    return Response.json(batch());
  };
  try { await runScenario("constant", adjustments); }
  finally { globalThis.fetch = original; }
});

test("map uses response fields exactly and a symmetric full-batch range, never a per-frame range", () => {
  const points = batch().points.slice(0, 4).map((p, i) => ({ ...p, barangay_psgc: `030080100${i}`,
    baseline_status: "experimental", scenario_status: "experimental", baseline_score: .01,
    scenario_score: [.01, .013, .008, .03][i], difference: [0, .003, -.002, .02][i] }));
  const summary = differenceSummary(points);
  assert.deepEqual(summary, { extent: .02, min: -.002, max: .02, median: .0015, positive: 2, negative: 1, unchanged: 1, missing: 0 });
  const ramp = ["blue", "white", "red"];
  const expression = scenarioFillColor(true, summary.extent, ramp, "gray");
  assert.deepEqual(expression, ["case", ["==", ["feature-state", "available"], true],
    ["interpolate", ["linear"], ["number", ["feature-state", "score"], 0], -.02, "blue", 0, "white", .02, "red"], "gray"]);
  for (const p of points) {
    assert.equal(scenarioMapScores([p], "baseline").get(p.barangay_psgc), p.baseline_score);
    assert.equal(scenarioMapScores([p], "scenario").get(p.barangay_psgc), p.scenario_score);
    assert.equal(scenarioMapScores([p], "difference").get(p.barangay_psgc), p.difference);
    assert.deepEqual(scenarioFillColor(true, summary.extent, ramp, "gray"), expression);
  }
  assert.deepEqual(scenarioFillColor(false, summary.extent, ramp, "gray")[2].slice(3), [0, "blue", .5, "white", 1, "red"]);
});

test("zero, missing and tiny differences do not create fake changes or duplicate interpolation stops", () => {
  const p = { ...batch().points[0], baseline_status: "experimental", scenario_status: "experimental", difference: 0 };
  assert.equal(differenceSummary([p]).extent, 0);
  assert.deepEqual(scenarioFillColor(true, 0, ["blue", "white", "red"], "gray"),
    ["case", ["==", ["feature-state", "available"], true], "white", "gray"]);
  const missing = { ...p, scenario_status: "insufficient_weather", difference: null };
  assert.equal(scenarioMapScores([missing], "difference").size, 0);
  assert.equal(differenceSummary([missing]).extent, null);
  assert.equal(differenceSummary([missing]).missing, 1);
  assert.equal(differenceSummary([{ ...p, difference: -.04 }, { ...p, difference: .01 }]).extent, .04);
  assert.equal(differenceSummary([{ ...p, difference: 1e-9 }]).extent, 1e-9);
  assert.equal(differenceText(1e-9), "1.0000e-9");
});
