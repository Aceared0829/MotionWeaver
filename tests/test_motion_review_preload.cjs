const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', checked: false, style: {}, classList: { add() { }, remove() { } },
    addEventListener() { }, setAttribute() { }, focus() { },
    getBoundingClientRect() { return { width: 800, height: 600 }; },
  });
  return elements.get(id);
}
let requests = 0, active = 0, peak = 0, failOnce = true;
const context = vm.createContext({
  document: { getElementById: element, querySelectorAll() { return []; }, addEventListener() { } },
  window: { addEventListener() { } }, location: { origin: 'http://localhost', search: '' },
  URL, URLSearchParams, Float32Array, requestAnimationFrame() { }, confirm() { return true; },
  async fetch(url) {
    if (url.pathname === '/api/catalog') return new Promise(() => { });
    requests++; active++; peak = Math.max(peak, active);
    await new Promise(resolve => setTimeout(resolve, 1));
    active--;
    if (url.pathname.endsWith('/retry') && failOnce) { failOnce = false; throw Error('transient'); }
    return {
      ok: true, async json() {
        return {
          source_sha256: 'source', output_sha256: url.pathname.endsWith('/source-only') ? null : 'output', frames: 2,
          source: { positions: [0, 0, 0, 1, 2, 3], root_positions: [0, 0, 0, 1, 2, 3] }, target: null,
        };
      }
    };
  },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../data/tools/review/review_web/app.js'), 'utf8'), context);

(async () => {
  vm.runInContext("catalog={entries:[{id:'a',source_sha256:'source',output_sha256:'output'},{id:'b',source_sha256:'source',output_sha256:'output'},{id:'retry',source_sha256:'source',output_sha256:'output'}]}", context);
  const pair = await vm.runInContext('Promise.all([loadClip(catalog.entries[0]),loadClip(catalog.entries[0])])', context);
  assert.equal(requests, 1, 'concurrent selection and preloading share one request');
  assert.equal(pair[0], pair[1]);
  assert(pair[0].source.positions instanceof Float32Array);
  await vm.runInContext('loadClip(catalog.entries[0])', context);
  assert.equal(requests, 1, 'a cached clip does not request the server again');
  await vm.runInContext('preloadPage()', context);
  assert.match(element('preloadStatus').textContent, /已加载 2 \/ 3.*失败 1/);
  assert(peak <= 2, 'preloading has at most two server requests in flight');
  await vm.runInContext('preloadPage()', context);
  assert.match(element('preloadStatus').textContent, /已加载 3 \/ 3.*缓存/);
  assert.equal(requests, 4, 'retry only downloads the previously failed clip');
  await assert.rejects(vm.runInContext("loadClip({id:'changed',source_sha256:'different',output_sha256:'output'})", context), /版本已变化/);
  assert.equal(vm.runInContext('clipCache.size', context), 3, 'identity mismatch is never cached');
  const sourceOnly = await vm.runInContext("loadClip({id:'source-only',source_sha256:'source',output_sha256:'unverifiable'})", context);
  assert.equal(sourceOnly.target, null, 'integrity failures still allow source-only review');
  console.log('Motion Review page preload, request sharing, retry, and identity binding: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
