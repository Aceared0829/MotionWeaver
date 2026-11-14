"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture(filename, payloads) {
  const source = fs.readFileSync(path.join(__dirname, "../data/tools/ops", filename), "utf8");
  const script = source.match(/<script>([\s\S]*?)<\/script>/)[1];
  const nodes = {};
  for (const id of ["status", "total", "overall", "meta", "jobs", "percent", "done", "speed", "eta", "bar", "updated", "error"]) {
    nodes[id] = { textContent: "", innerHTML: "", style: {} };
  }
  const context = vm.createContext({
    ...nodes,
    status: "browser window.status",
    document: { body: { innerHTML: "original page" }, getElementById: id => nodes[id] },
    fetch: async () => ({ json: async () => payloads.shift() }),
    setInterval: () => {},
  });
  vm.runInContext(script, context);
  return { nodes, context };
}

for (const filename of ["migration_progress_lite.js", "migration_progress_lite_server.py"]) {
  test(`${filename}: updates the status element without using window.status`, async () => {
    const { nodes, context } = fixture(filename, [{
      state: "fixture scanning", total_bytes: 4, expected_bytes: 8,
      jobs: [], speed_bps: 0, eta_seconds: null, updated_at: 1,
    }]);
    await new Promise(resolve => setImmediate(resolve));
    assert.match(nodes.status.textContent, /fixture scanning/);
    assert.equal(context.status, "browser window.status");
    assert.match(nodes.total.textContent, /50\.00%/);
  });
}

test("manifest observer recovers from startup and error responses", async () => {
  const { nodes, context } = fixture("migration_progress_server.py", [
    { status: "first scan pending" },
    { error: "<untrusted path>" },
    { complete_bytes: 4, total_bytes: 8, speed_bytes_per_sec: 0, eta_seconds: null, jobs: [], updated_at: "2026-10-03T00:00:00Z" },
  ]);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(nodes.error.textContent, "first scan pending");
  await context.refresh();
  assert.equal(nodes.error.textContent, "<untrusted path>");
  assert.equal(context.document.body.innerHTML, "original page");
  await context.refresh();
  assert.equal(nodes.percent.textContent, "50.00%");
  assert.equal(nodes.error.textContent, "无");
});
