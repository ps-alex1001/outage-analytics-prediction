import { test } from "node:test";
import assert from "node:assert/strict";
import { loadStormReplay, municipalityReplayScores, replayScores, weatherMaximum } from "../src/lib/storm-replay.ts";
import { nextPlaybackFrame } from "../src/lib/scenario.ts";

function fixture() {
  return { hours: 24, barangay_count: 1, weather_locations: [],
    weather_hourly: [{ rainfall: 0, wind: 20 }, { rainfall: 5, wind: null }, { rainfall: null, wind: 40 }],
    points: Array.from({ length: 24 }, (_, i) => {
      const start = new Date(Date.UTC(2025, 9, 20, i)).toISOString();
      return { barangay_psgc: "0300801001", prediction_cutoff: start, target_start: start,
        target_end: new Date(Date.parse(start)+3600000).toISOString(),
        score: i === 1 ? null : .002, status: i === 1 ? "insufficient_weather" : "experimental" };
    }) };
}

test("one loaded replay supplies all playback frames; model values and missingness stay unchanged", async () => {
  const original = globalThis.fetch; let calls = 0;
  globalThis.fetch = async url => { calls++; assert.ok(url.endsWith("/api/storm-replays/2025289N13140")); return Response.json(fixture()); };
  try {
    const batch = await loadStormReplay("2025289N13140");
    const saved = JSON.stringify(batch); const bounds = weatherMaximum(batch, "rainfall");
    let state = { hour: 0, playing: true };
    while (state.playing) {
      const scores = replayScores([batch.points[state.hour]]);
      assert.equal(scores.size, state.hour === 1 ? 0 : 1);
      if (scores.size) assert.equal(scores.get("0300801001"), .002);
      assert.equal(weatherMaximum(batch, "rainfall"), bounds);
      state = nextPlaybackFrame(state.hour);
    }
    assert.equal(state.hour, 23);
    assert.equal(calls, 1); assert.equal(bounds, 5); assert.equal(weatherMaximum(batch, "wind"), 40);
    assert.equal(JSON.stringify(batch), saved);
    assert.equal(replayScores(batch.points, true).size, 0);
  } finally { globalThis.fetch = original; }
});

test("replay request rejects invalid scores and preserves clear weather/model failure messages", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => Response.json({ detail: "Historical weather is unavailable" }, { status: 503 });
    await assert.rejects(loadStormReplay("test"), /Historical weather is unavailable/);
    const batch = fixture(); batch.points[0].score = 2;
    globalThis.fetch = async () => Response.json(batch);
    await assert.rejects(loadStormReplay("test"), /24-hour model contract/);
  } finally { globalThis.fetch = original; }
});

test("municipality display means only available barangay scores without replacing missing values", () => {
  const base = fixture().points[0];
  const points = [{ ...base, municipality_psgc: "0300801000", score: .002 },
    { ...base, municipality_psgc: "0300801000", score: .006 },
    { ...base, municipality_psgc: "0300801000", score: null, status: "insufficient_weather" },
    { ...base, municipality_psgc: "0300802000", score: null, status: "insufficient_weather" },
    { ...base, municipality_psgc: "0300803000", score: 0 }];
  const saved = JSON.stringify(points);
  const result = municipalityReplayScores(points);
  assert.equal(result.scores.get("0300801000"), .004);
  assert.deepEqual(result.groups.get("0300801000"), { sum: .008, available: 2, total: 3 });
  assert.equal(result.scores.has("0300802000"), false);
  assert.equal(result.scores.get("0300803000"), 0);
  assert.equal(municipalityReplayScores(points, true).scores.size, 0);
  assert.equal(JSON.stringify(points), saved);
});
