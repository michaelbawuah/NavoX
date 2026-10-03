import { describe, expect, it } from "vitest";
import { AssistantError } from "./errors";
import { createAssistantRuntime } from "./runtime";
import {
  createFakeUpstream,
  createMemoryStore,
  OTHER_SCOPE,
  REQUEST_ID,
} from "./testing/fakes";

const COOKIE = "navox_session=abc123";
const NOW = new Date("2026-10-03T12:00:00Z");

async function setup() {
  const store = createMemoryStore();
  const upstream = createFakeUpstream({
    planError: new AssistantError("unavailable", "No model available."),
  });
  let counter = 0;
  const runtime = createAssistantRuntime({
    store,
    upstream,
    now: () => NOW,
    newId: () =>
      `00000000-0000-4000-8000-${String(++counter).padStart(12, "0")}`,
  });
  const session = await runtime.createSession({ cookie: COOKIE });
  return { store, upstream, runtime, session };
}

describe("aggregate reads in the saved assistant", () => {
  it("reads upcoming renewals without requiring a planner or cancellation action", async () => {
    const { runtime, upstream, session } = await setup();
    upstream.subscriptions = { intent: "UPCOMING", subscriptions: [] };
    const result = await runtime.submitTurn({
      cookie: COOKIE,
      session_id: session.id,
      body: {
        request_id: REQUEST_ID,
        text: "Show my upcoming subscriptions",
        modality: "TEXT",
      },
    });
    expect(result.turn.state).toBe("READY");
    expect(result.turn.decision?.reason).toBe("subscription.upcoming");
    expect(result.turn.action_refs).toEqual([]);
    expect(upstream.calls.subscriptions).toEqual([
      { cookie: COOKIE, selector: null },
    ]);
    expect(upstream.calls.cancellation).toEqual([]);
    expect(upstream.calls.plan).toEqual([]);
  });
  it("gives an unavailable response for missing world news, never a named-topic question", async () => {
    const { runtime, upstream, session } = await setup();
    const result = await runtime.submitTurn({
      cookie: COOKIE,
      session_id: session.id,
      body: {
        request_id: REQUEST_ID,
        text: "Give me international news",
        modality: "TEXT",
      },
    });
    expect(result.turn.state).toBe("UNAVAILABLE");
    expect(result.turn.decision?.reason).toBe("news.no_current_trends");
    expect(upstream.calls.trendingNews).toEqual([COOKIE]);
    expect(upstream.calls.plan).toEqual([]);
    expect(result.turn.action_refs).toEqual([]);
  });
  it("does not let the read shortcut bypass session ownership", async () => {
    const { runtime, store, upstream, session } = await setup();
    const row = store.sessions[0];
    if (!row) throw new Error("Missing test session");
    row.workspace_id = OTHER_SCOPE.workspace_id;
    await expect(
      runtime.submitTurn({
        cookie: COOKIE,
        session_id: session.id,
        body: {
          request_id: REQUEST_ID,
          text: "Show my upcoming subscriptions",
          modality: "TEXT",
        },
      }),
    ).rejects.toMatchObject({ code: "not_found" });
    expect(upstream.calls.subscriptions).toEqual([]);
  });
});
