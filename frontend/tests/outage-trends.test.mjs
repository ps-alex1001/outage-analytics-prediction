import { test } from "node:test";
import assert from "node:assert/strict";
import { yearlyOutageSeries } from "../src/lib/outage-trends.ts";

test("years share calendar months without joining December to next January", () => {
  const result = yearlyOutageSeries([
    { period: "2020-01", total_outages: 9 },
    { period: "2019-12", total_outages: 4 },
    { period: "2019-01", total_outages: 2 },
    { period: "2019-02", total_outages: 0 },
  ]);
  assert.deepEqual(result.map(s => s.name), ["2019", "2020"]);
  assert.equal(result[0].data.length, 12);
  assert.equal(result[0].data[0], 2);
  assert.equal(result[0].data[1], 0);
  assert.equal(result[0].data[11], 4);
  assert.equal(result[1].data[0], 9);
  assert.equal(result[1].data[11], null);
});

test("partial coverage stops at June instead of drawing future zeros", () => {
  const [year] = yearlyOutageSeries(Array.from({ length: 6 }, (_, i) => ({
    period: `2026-${String(i + 1).padStart(2, "0")}`, total_outages: i,
  })));
  assert.deepEqual(year.data, [0, 1, 2, 3, 4, 5, null, null, null, null, null, null]);
  assert.deepEqual(yearlyOutageSeries([]), []);
});
