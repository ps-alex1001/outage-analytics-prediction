import { test } from "node:test";
import assert from "node:assert/strict";
import { relativeScoreScale, relativeScoreColor, relativeScaleNote } from "../src/lib/relative-score.ts";

test("one full-batch min/max scale with margin keeps raw scores and playback colors stable", () => {
  const values = [.001, .002, null, .004]; const saved = [...values];
  const scale = relativeScoreScale(values);
  assert.ok(Math.abs(scale.lower-.00085)<1e-12);
  assert.ok(Math.abs(scale.upper-.00415)<1e-12);
  const expression=relativeScoreColor(scale,["yellow","orange","red"],"gray");
  assert.deepEqual(expression[1],["==",["feature-state","available"],true]);
  assert.equal(expression[3],"gray");
  for (const frame of values) {
    void frame;
    assert.deepEqual(relativeScoreColor(scale,["yellow","orange","red"],"gray"),expression);
  }
  assert.deepEqual(values,saved);
  assert.match(relativeScaleNote(scale),/5% range margin/);
});
test("missing, equal, tiny and bounded scores never create fake score changes", () => {
  assert.equal(relativeScoreScale([null,undefined,NaN,Infinity,-1,2]).count,0);
  const flat=relativeScoreScale([0,0,null]);
  assert.equal(flat.flat,true);
  assert.equal(relativeScoreColor(flat,["yellow","orange","red"],"gray")[2],"orange");
  assert.match(relativeScaleNote(flat),/neutral/);
  const bounded=relativeScoreScale([0,1]);
  assert.equal(bounded.lower,0); assert.equal(bounded.upper,1);
  assert.ok(relativeScoreScale([1e-9,2e-9]).upper>2e-9);
  assert.throws(()=>relativeScoreScale([.01],-1),/Invalid/);
});
