import test from "node:test";
import assert from "node:assert/strict";
import {
  assertNoRemoteExecutableCode,
  manifestFor,
  normalizeOrigin,
} from "../scripts/build-lib.mjs";
import { pageNoteImport, pageSelection } from "../src/capture.js";
import {
  actionSummary,
  flattenSignals,
  deadlineItems,
  deadlineTone,
  priorityLabel,
  terminalPlan,
  uniqueTodayItems,
  urgencyLabel,
} from "../src/presentation.js";

const template = JSON.stringify({
  manifest_version: 3,
  permissions: ["sidePanel", "storage", "activeTab"],
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
  assert.deepEqual(manifest.permissions, ["sidePanel", "storage", "activeTab"]);
  assert.deepEqual(manifest.host_permissions, ["http://localhost:8000/*"]);

  const unsafe = JSON.stringify({
    manifest_version: 3,
    permissions: ["sidePanel", "scripting"],
    host_permissions: ["__NAVOX_API_HOST__/*"],
  });
  assert.throws(() => manifestFor(unsafe, "https://api.example.com"), /Forbidden/);
});

test("page note selects only visible site origin, title, and user-written text", () => {
  const selection = pageSelection({
    url: "https://example.org/private/path?token=SECRET-QUERY#SECRET-FRAGMENT",
    title: "Submit the report",
  });
  assert.deepEqual(selection, { origin: "https://example.org", title: "Submit the report" });
  const command = pageNoteImport(selection, "Submit by Friday.");
  assert.equal(command.format, "json");
  assert.equal(command.name, "Page note: example.org");
  assert.deepEqual(JSON.parse(command.content), [{
    id: "https://example.org",
    title: "Submit the report",
    description: "Submit by Friday.",
    url: "https://example.org",
  }]);
  assert.doesNotMatch(JSON.stringify(command), /SECRET|private\/path/);
  const longHost = `https://${"a".repeat(55)}.${"b".repeat(55)}.example.org`;
  assert.ok(pageNoteImport({ origin: longHost, title: "Task" }, "Review this").name.length <= 120);
});

test("page note rejects inaccessible pages and missing user confirmation text", () => {
  for (const url of ["chrome://settings", "http://example.org", "https://user:pass@example.org"]) {
    assert.throws(() => pageSelection({ url, title: "Private" }), /HTTPS|saved/);
  }
  assert.throws(() => pageSelection({ url: "https://example.org", title: "" }), /title/);
  assert.throws(() => pageNoteImport({ origin: "https://example.org", title: "Task" }, ""), /note/);
  assert.throws(() => pageNoteImport({ origin: "https://example.org", title: "Task" }, "x".repeat(2001)), /2,000/);
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


test("deadline presentation uses clear urgency language instead of scores", () => {
  const now = Date.parse("2026-09-22T12:00:00Z");
  const urgent = {
    id: "urgent",
    type: "deadline",
    title: "Submit checkpoint",
    status: "confirmed",
    priority: 5,
    due_at: "2026-09-23T12:00:00Z",
  };
  const upcoming = {
    id: "upcoming",
    type: "deadline",
    title: "Submit report",
    status: "confirmed",
    priority: 3,
    due_at: "2026-09-29T12:00:00Z",
  };
  const completed = {
    id: "completed",
    type: "deadline",
    title: "Finish lab",
    status: "completed",
    priority: 4,
    due_at: "2026-09-21T12:00:00Z",
  };

  assert.equal(deadlineTone(urgent, now), "urgent");
  assert.equal(deadlineTone(upcoming, now), "upcoming");
  assert.equal(deadlineTone(completed, now), "completed");
  assert.equal(urgencyLabel(urgent, now), "Urgent");
  assert.equal(priorityLabel(2), "Moderate");
  assert.equal(priorityLabel(4), "Important");
  assert.deepEqual(
    deadlineItems({
      needs_attention: [urgent],
      coming_up: [upcoming],
      renewals: [],
      waiting_on: [],
      completed_recently: [completed],
    }).map((item) => item.id),
    ["urgent", "upcoming", "completed"],
  );
});
