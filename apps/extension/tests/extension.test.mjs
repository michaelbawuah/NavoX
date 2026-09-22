import test from "node:test";
import assert from "node:assert/strict";
import {
  assertNoRemoteExecutableCode,
  manifestFor,
  normalizeOrigin,
} from "../scripts/build-lib.mjs";
import {
  actionSummary,
  flattenSignals,
  terminalPlan,
  uniqueTodayItems,
} from "../src/presentation.js";

const template = JSON.stringify({
  manifest_version: 3,
  permissions: ["sidePanel", "storage"],
  host_permissions: ["__NAVOX_API_HOST__/*"],
  side_panel: { default_path: "sidepanel.html" },
});

test("build accepts HTTPS or local HTTP API origins only", () => {
  assert.equal(normalizeOrigin("https://api.example.com", "API"), "https://api.example.com");
  assert.equal(normalizeOrigin("http://localhost:8000", "API"), "http://localhost:8000");
  assert.throws(() => normalizeOrigin("http://example.com", "API"), /HTTPS/);
  assert.throws(() => normalizeOrigin("https://example.com/path", "API"), /scheme, host/);
});

test("manifest remains permission minimal", () => {
  const manifest = manifestFor(template, "http://localhost:8000");
  assert.deepEqual(manifest.permissions, ["sidePanel", "storage"]);
  assert.deepEqual(manifest.host_permissions, ["http://localhost:8000/*"]);

  const unsafe = JSON.stringify({
    manifest_version: 3,
    permissions: ["sidePanel", "scripting"],
    host_permissions: ["__NAVOX_API_HOST__/*"],
  });
  assert.throws(() => manifestFor(unsafe, "https://api.example.com"), /Forbidden/);
});

test("build rejects remote and dynamic executable code", () => {
  assert.doesNotThrow(() =>
    assertNoRemoteExecutableCode([["safe.js", 'fetch("https://api.example.com/state")']]),
  );
  assert.throws(
    () => assertNoRemoteExecutableCode([["bad.html", '<script src="https://evil.example/x.js">']]),
    /Remote or dynamic executable code/,
  );
  assert.throws(
    () => assertNoRemoteExecutableCode([["bad.js", 'eval("alert(1)")']]),
    /Remote or dynamic executable code/,
  );
});

test("Today and proactive presentation deduplicate and flatten saved state", () => {
  const today = {
    needs_attention: [{ id: "1", title: "One" }],
    coming_up: [{ id: "1", title: "One" }, { id: "2", title: "Two" }],
    renewals: [],
    waiting_on: [{ id: "3", title: "Three" }],
  };
  assert.deepEqual(uniqueTodayItems(today).map((item) => item.id), ["1", "2", "3"]);
  assert.equal(
    flattenSignals({ notify_now: [{ id: "a" }], briefing: [{ id: "b" }], dashboard: [] }).length,
    2,
  );
});

test("approval and plan presentation never changes server risk semantics", () => {
  assert.deepEqual(
    actionSummary({
      provider: "gmail",
      action_type: "gmail.send",
      payload: { to: "person@example.com", subject: "Hello", body_text: "Exact body" },
    }),
    {
      title: "Hello",
      recipient: "person@example.com",
      body: "Exact body",
    },
  );
  assert.equal(terminalPlan("completed"), true);
  assert.equal(terminalPlan("running"), false);
});
