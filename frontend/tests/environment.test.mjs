import { test } from "node:test";
import assert from "node:assert/strict";
import { loadRasterBlob } from "../src/lib/environment.ts";
const image = (code, sha="v1") => ({ psgc:code, name:code, maps: Object.fromEntries(["ndvi","slope","combined"].map(kind=>[kind,{url:`/images/${code}/${kind}`,sha256:sha,data_available:true}])) });

test("only requested layer is fetched; completed images reuse the bounded cache", async () => {
  const original=globalThis.fetch; const urls=[];
  globalThis.fetch=async (url, options) => { urls.push(url); assert.equal(options.cache,"no-cache"); return new Response(new Blob(["webp"])); };
  try {
    const signal=new AbortController().signal;
    const a=await loadRasterBlob(image("one"),"ndvi","http://localhost:8000",signal);
    const b=await loadRasterBlob(image("one"),"ndvi","http://localhost:8000",signal);
    assert.equal(a,b); assert.deepEqual(urls,["http://localhost:8000/images/one/ndvi"]);
    await loadRasterBlob(image("one","v2"),"ndvi","http://localhost:8000",signal);
    assert.equal(urls.length,2);
    for(let i=0;i<25;i++) await loadRasterBlob(image(`eviction-${i}`),"ndvi","http://localhost:8000",signal);
    await loadRasterBlob(image("one","v2"),"ndvi","http://localhost:8000",signal);
    assert.equal(urls.length,28);
  } finally { globalThis.fetch=original; }
});

test("aborted and failed image requests can retry; unavailable measurements are not requested", async () => {
  const original=globalThis.fetch; let calls=0;
  globalThis.fetch=async()=>{calls++;return new Response("missing",{status:404});};
  try {
    const abort=new AbortController();abort.abort();
    await assert.rejects(loadRasterBlob(image("cancelled"),"slope","http://localhost",abort.signal),{name:"AbortError"});
    assert.equal(calls,0);
    const signal=new AbortController().signal;
    await assert.rejects(loadRasterBlob(image("retry"),"slope","http://localhost",signal),/Unable to load/);
    globalThis.fetch=async()=>{calls++;return new Response("ok");};
    await loadRasterBlob(image("retry"),"slope","http://localhost",signal);assert.equal(calls,2);
    const missing=image("unavailable");missing.maps.ndvi.data_available=false;
    await assert.rejects(loadRasterBlob(missing,"ndvi","http://localhost",signal),/No ndvi measurements/);
    assert.equal(calls,2);
  } finally {globalThis.fetch=original;}
});
