import assert from 'node:assert/strict';
import test from 'node:test';
import { ImpactCache, awaitImpact } from '../../frontend/src/lib/impact-cache.ts';

test('hover and selection share one in-flight request and retain a warm result', async () => {
  const cache = new ImpactCache(2);
  let calls = 0;
  const fetch = async () => { calls++; return { id: 'event' }; };
  const hover = cache.get('event', fetch), selection = cache.get('event', fetch);
  assert.equal(hover, selection);
  assert.equal(await hover, await cache.get('event', fetch));
  assert.equal(calls, 1);
});

test('a recently reused feeder survives eviction; failures remain retryable', async () => {
  const cache = new ImpactCache(2);
  const a = cache.get('a', async () => 'a');
  await cache.get('b', async () => 'b');
  assert.equal(cache.get('a', async () => 'wrong'), a);
  await cache.get('c', async () => 'c');
  await assert.rejects(cache.get('b', async () => { throw new Error('offline'); }), /offline/);
  assert.equal(await cache.get('b', async () => 'retry'), 'retry');
});

test('canceling a superseded selection does not cancel shared prefetch data', async () => {
  const cache = new ImpactCache(2), controller = new AbortController();
  let complete;
  const shared = cache.get('a', () => new Promise(resolve => { complete = resolve; }));
  const selection = awaitImpact(shared, controller.signal);
  await Promise.resolve();
  controller.abort();
  await assert.rejects(selection, { name: 'AbortError' });
  complete('available');
  assert.equal(await cache.get('a', async () => 'wrong'), 'available');
});

test('a slow canceled response cannot overwrite a newer displayed event', async () => {
  const oldController = new AbortController();
  let completeOld, displayed = 'initial';
  const old = awaitImpact(new Promise(resolve => { completeOld = resolve; }), oldController.signal)
    .then(value => { displayed = value; }).catch(error => assert.equal(error.name, 'AbortError'));
  oldController.abort();
  await awaitImpact(Promise.resolve('new')).then(value => { displayed = value; });
  completeOld('old'); await old;
  assert.equal(displayed, 'new');
});

test('an evicted rejected request cannot delete its replacement', async () => {
  const cache = new ImpactCache(1);
  let fail;
  const old = cache.get('a', () => new Promise((_, reject) => { fail = reject; }));
  await Promise.resolve();
  await cache.get('b', async () => 'b');
  const replacement = cache.get('a', async () => 'new');
  fail(new Error('old failure')); await assert.rejects(old);
  assert.equal(cache.get('a', async () => 'wrong'), replacement);
  assert.equal(await replacement, 'new');
});
