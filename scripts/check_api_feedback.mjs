// Exercise the browser API adapter's failure contracts; no network or model.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const require = createRequire(new URL('../web/package.json', import.meta.url));
const { build } = require('esbuild');
const compiled = await build({ entryPoints: [fileURLToPath(new URL('../web/src/api.ts', import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' });
const { api, downloadFile, ApiError } = await import('data:text/javascript;base64,' + Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const originalFetch = globalThis.fetch, originalTimer = globalThis.setTimeout;
const results = [];
// Advance only the adapter's long transport deadlines for deterministic tests.
globalThis.setTimeout = (fn, delay, ...args) => originalTimer(fn, delay >= 15000 ? 25 : delay, ...args);
try {
  globalThis.fetch = async () => new Response('{"saved":true}', { headers: { 'content-type': 'application/json' } });
  assert.deepEqual(await api('/projects/example/intake', 'POST', {}), { saved: true });
  results.push('valid JSON write response retained');
  for (const payload of ['', '<html>proxy login</html>', 'null']) {
    globalThis.fetch = async () => new Response(payload);
    await assert.rejects(api('/projects/example/intake', 'POST', {}), e => e instanceof ApiError && /未取得有效处理结果/.test(e.message));
  }
  results.push('empty, HTML and null HTTP 200 are failures, never save success');
  globalThis.fetch = async () => { throw new TypeError('transport failure'); };
  await assert.rejects(api('/projects/example/intake', 'POST', {}), e => e.code === 'NETWORK' && e.message.includes('是否已处理尚未确认') && e.message.includes('避免重复提交'));
  results.push('lost write response explicitly reports unknown server state');
  globalThis.fetch = async (url, options) => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))));
  await assert.rejects(api('/projects/example'), e => e.code === 'REQUEST_TIMEOUT');
  results.push('request without response reaches bounded timeout');
  globalThis.fetch = async (url, options) => new Response(new ReadableStream({ start(controller) { options.signal.addEventListener('abort', () => controller.error(new DOMException('aborted', 'AbortError'))); } }));
  await assert.rejects(api('/projects/example'), e => e.code === 'REQUEST_TIMEOUT');
  results.push('timeout also covers a response body that never finishes');
  const cancel = new AbortController();
  globalThis.fetch = async (url, options) => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))));
  const pending = api('/projects/example', 'GET', undefined, cancel.signal); cancel.abort();
  await assert.rejects(pending, e => e.name === 'AbortError');
  results.push('explicit caller cancellation remains distinguishable');
  for (const [body, type] of [['', 'application/zip'], ['<html>error</html>', 'text/html'], ['{"error":true}', 'application/json']]) {
    globalThis.fetch = async () => new Response(body, { headers: { 'content-type': type } });
    await assert.rejects(downloadFile('/api/test/zip', 'test.zip'), e => e instanceof ApiError && ['DOWNLOAD_INVALID', 'DOWNLOAD_EMPTY'].includes(e.code));
  }
  results.push('empty or error-shaped downloads rejected before browser save');
  console.log(JSON.stringify({ status: 'passed', cases: results, network_calls: 0 }, null, 2));
} finally { globalThis.fetch = originalFetch; globalThis.setTimeout = originalTimer; }
