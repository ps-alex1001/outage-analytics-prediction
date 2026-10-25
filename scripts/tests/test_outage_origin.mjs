import assert from 'node:assert/strict';
import test from 'node:test';
import { outageOriginPoint } from '../../frontend/src/lib/outage-origin.ts';
const feature = geometry => ({ type: 'Feature', properties: {}, geometry });
const point = coordinates => feature({ type: 'Point', coordinates });
test('transformer source uses its own location', () => {
  assert.deepEqual(outageOriginPoint(point([120.5,14.7])), [120.5,14.7]);
});
test('line ripple starts at upstream topology node even when line coordinates are reversed', () => {
  const line = feature({type:'LineString',coordinates:[[122,14],[120,14]]});
  assert.deepEqual(outageOriginPoint(line, point([120,14])), [120,14]);
});
test('line with no valid topology anchor does not invent a midpoint source', () => {
  const line = feature({type:'LineString',coordinates:[[120,14],[122,14]]});
  assert.equal(outageOriginPoint(line), undefined);
  assert.equal(outageOriginPoint(line,point([NaN,14])), undefined);
});
