import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import type {
  AssistantBlock,
  AssistantPresentationPlan,
  CapabilityDecision,
  IntentPlan,
} from "@navox/contracts";
import {
  WorkflowExecutionAlreadyStartedError,
  WorkflowNotFoundError,
} from "@temporalio/client";
import { describe, expect, it, vi } from "vitest";
import { createAssistantGoalService } from "./goal-service";
import {
  ASSISTANT_GOAL_WORKFLOW_TYPE,
  GOAL_DETAIL,
  GOAL_LIMITS,
  goalKindForTurn,
  goalWorkflowId,
  parseGoalWorkflowInput,
  verifyActionGoal,
  verifyTurnGoal,
  windowEndedOutcome,
} from "./goals";
import { createAssistantRuntime } from "./runtime";
import type { AssistantGoalRecord, AssistantTurnRecord } from "./store";
import { createGoalActivities } from "./temporal/activities";
import { createTemporalGoalDispatcher } from "./temporal/client";
import {
  createFakeUpstream,
  createMemoryStore,
  NAVOX_SESSION_ID,
  OTHER_SCOPE,
  SCOPE,
} from "./testing/fakes";

const ITEM_ID = "44444444-4444-4444-8444-444444444444";

const READY_TODAY_DECISION: CapabilityDecision = {
  kind: "DELEGATE",
  capability_id: "today.read",
  target: "today.query",
  reason: "today.today",
  requires_approval: false,
  action_state: "NONE",
  action_id: null,
  response_state: "READY",
};

const TODAY_PLAN: IntentPlan = {
  version: 1,
  intents: [
    {
      kind: "today.read",
      capability_id: "today.read",
      question: "What am I missing today?",
      confidence: 1,
      entity: { kind: "NONE", value: null, confidence: 1 },
      time: { kind: "NONE", expression: null, confidence: 1 },
      reference: { kind: "NONE", ordinal: null, turn_id: null },
      requires_clarification: false,
      clarification: null,
    },
  ],
};

function itemBlock(): AssistantBlock {
  return {
    kind: "ITEM",
    item: {
      id: ITEM_ID,
      type: "meeting",
      title: "Standup",
      description: null,
      status: "open",
      due_at: "2026-10-01T12:30:00.000Z",
      band: null,
      sources: [],
    },
  };
}

function meetingBlock(): AssistantBlock {
  return {
    kind: "MEETING_BRIEFING",
    meeting: {
      commitment_id: ITEM_ID,
      title: "Standup",
      starts_at: "2026-10-01T12:30:00.000Z",
      minutes_until: 30,
      description: null,
      related_commitments: [],
      prep_points: ["Read the agenda"],
    },
  };
}

function presentation(blocks: AssistantBlock[]): AssistantPresentationPlan {
  return {
    presentation: "TEXT",
    speak: false,
    speech_text: null,
    delivery: "AUTOMATIC",
    blocks,
  };
}

function turnRecord(
  overrides: Partial<AssistantTurnRecord> = {},
): AssistantTurnRecord {
  return {
    id: randomUUID(),
    session_id: randomUUID(),
    sequence: 1,
    modality: "TEXT",
    state: "READY",
    request_id: randomUUID(),
    request_fingerprint: "a".repeat(64),
    question: "What am I missing today?",
    response_text: "1 item needs attention now.",
    plan: TODAY_PLAN,
    decision: READY_TODAY_DECISION,
    presentation: presentation([{ kind: "ANSWER", text: "1 item" }]),
    action_refs: [],
    created_at: "2026-10-01T12:00:00.000Z",
    ...overrides,
  };
}

function provenance(
  overrides: {
    state?: string;
    plan?: IntentPlan | null;
    decision?: CapabilityDecision | null;
    presentation?: AssistantPresentationPlan | null;
  } = {},
) {
  return {
    state: "READY",
    plan: TODAY_PLAN,
    decision: READY_TODAY_DECISION,
    presentation: presentation([{ kind: "ANSWER", text: "1 item" }]),
    ...overrides,
  };
}

describe("grounded today.read provenance", () => {
  it("derives a briefing goal only from a grounded Today answer", () => {
    expect(goalKindForTurn(provenance())).toBe("BRIEFING");
  });

  it("derives meeting preparation only when the briefing owns a carried item", () => {
    expect(
      goalKindForTurn(
        provenance({
          presentation: presentation([itemBlock(), meetingBlock()]),
        }),
      ),
    ).toBe("MEETING_PREP");

    // A briefing block that does not belong to an item this turn carried is
    // not the owning service's own grounded output.
    expect(
      goalKindForTurn(
        provenance({
          presentation: presentation([
            { kind: "ANSWER", text: "Meeting in 30 minutes." },
            {
              ...meetingBlock(),
              meeting: {
                ...(meetingBlock() as { meeting: object }).meeting,
                commitment_id: randomUUID(),
              },
            } as AssistantBlock,
          ]),
        }),
      ),
    ).toBeNull();
  });

  it("fails closed when the decision provenance is missing or wrong", () => {
    expect(goalKindForTurn(provenance({ state: "CLARIFY" }))).toBeNull();
    expect(goalKindForTurn(provenance({ decision: null }))).toBeNull();
    for (const decision of [
      { ...READY_TODAY_DECISION, kind: "UNAVAILABLE" as const },
      { ...READY_TODAY_DECISION, capability_id: "email.search" as const },
      { ...READY_TODAY_DECISION, target: "search.query" },
      { ...READY_TODAY_DECISION, reason: "email.search.found_one" },
      { ...READY_TODAY_DECISION, response_state: "UNAVAILABLE" as const },
      { ...READY_TODAY_DECISION, requires_approval: true },
      { ...READY_TODAY_DECISION, action_state: "EXECUTED" as const },
      { ...READY_TODAY_DECISION, action_id: randomUUID() },
    ])
      expect(goalKindForTurn(provenance({ decision }))).toBeNull();
  });

  it("fails closed when the plan provenance is missing or wrong", () => {
    expect(goalKindForTurn(provenance({ plan: null }))).toBeNull();
    expect(
      goalKindForTurn(
        provenance({
          plan: {
            version: 1,
            intents: [
              { ...TODAY_PLAN.intents[0], requires_clarification: true },
            ],
          },
        }),
      ),
    ).toBeNull();
    expect(
      goalKindForTurn(
        provenance({
          plan: {
            version: 1,
            intents: [
              { ...TODAY_PLAN.intents[0], capability_id: "email.search" },
            ],
          },
        }),
      ),
    ).toBeNull();
    expect(
      goalKindForTurn(
        provenance({
          plan: {
            version: 1,
            intents: [TODAY_PLAN.intents[0], TODAY_PLAN.intents[0]],
          },
        }),
      ),
    ).toBeNull();
  });

  it("fails closed when the answer carries no owning-service result", () => {
    expect(
      goalKindForTurn(
        provenance({
          presentation: presentation([
            { kind: "NOTICE", state: "UNAVAILABLE", text: "Down" },
          ]),
        }),
      ),
    ).toBeNull();
    expect(goalKindForTurn(provenance({ presentation: null }))).toBeNull();
  });
});

describe("briefing and meeting verification authority", () => {
  it("verifies a grounded ready turn and refuses every other state", () => {
    expect(verifyTurnGoal({ kind: "BRIEFING", ...provenance() })).toEqual({
      status: "COMPLETED",
      detail: GOAL_DETAIL.verified,
      completed: true,
    });

    expect(
      verifyTurnGoal({
        kind: "BRIEFING",
        ...provenance({ state: "UNAVAILABLE" }),
      }),
    ).toEqual({
      status: "FAILED",
      detail: GOAL_DETAIL.turnNotReady,
      completed: false,
    });
  });

  it("refuses a turn whose provenance no longer matches its goal kind", () => {
    // The goal says meeting preparation, but nothing in the turn proves it.
    expect(verifyTurnGoal({ kind: "MEETING_PREP", ...provenance() })).toEqual({
      status: "FAILED",
      detail: GOAL_DETAIL.turnNotReady,
      completed: false,
    });
    // The goal says briefing, but the turn is a meeting preparation.
    expect(
      verifyTurnGoal({
        kind: "BRIEFING",
        ...provenance({
          presentation: presentation([itemBlock(), meetingBlock()]),
        }),
      }),
    ).toEqual({
      status: "FAILED",
      detail: GOAL_DETAIL.turnNotReady,
      completed: false,
    });
  });
});

describe("consequential action verification authority", () => {
  const now = new Date("2026-10-01T12:00:00.000Z");

  it("completes only a completed action with an independent verification", () => {
    expect(
      verifyActionGoal({
        status: "completed",
        executed_at: "2026-10-01T11:59:00.000Z",
        verified_at: "2026-10-01T11:59:30.000Z",
        now,
      }),
    ).toEqual({
      status: "COMPLETED",
      detail: GOAL_DETAIL.verified,
      completed: true,
    });
  });

  it("keeps executed but unverified work out of success", () => {
    expect(
      verifyActionGoal({
        status: "completed",
        executed_at: "2026-10-01T11:59:00.000Z",
        verified_at: null,
        now,
      }),
    ).toEqual({
      status: "WAITING_FOR_EXTERNAL",
      detail: GOAL_DETAIL.executedUnverified,
      completed: false,
    });
  });

  it("keeps pending approval with the operator", () => {
    for (const status of [
      "pending",
      "candidate",
      "queued",
      "awaiting_approval",
    ])
      expect(
        verifyActionGoal({
          status,
          executed_at: null,
          verified_at: null,
          now,
        }),
      ).toEqual({
        status: "WAITING_FOR_USER",
        detail: GOAL_DETAIL.waitingForApproval,
        completed: false,
      });
  });

  it("reports an in-flight execution without claiming completion", () => {
    for (const status of ["approved", "executing", "running"])
      expect(
        verifyActionGoal({
          status,
          executed_at: null,
          verified_at: null,
          now,
        }),
      ).toEqual({
        status: "RUNNING",
        detail: GOAL_DETAIL.running,
        completed: false,
      });
  });

  it("keeps an uncertain outcome waiting for verification, never failed", () => {
    expect(
      verifyActionGoal({
        status: "uncertain",
        executed_at: "2026-10-01T11:59:00.000Z",
        verified_at: null,
        now,
      }),
    ).toEqual({
      status: "WAITING_FOR_EXTERNAL",
      detail: GOAL_DETAIL.actionUncertain,
      completed: false,
    });
  });

  it("never turns a failed, blocked, cancelled or expired row into success", () => {
    for (const status of [
      "failed",
      "rejected",
      "blocked",
      "cancelled",
      "expired",
    ])
      expect(
        verifyActionGoal({
          status,
          executed_at: null,
          verified_at: null,
          now,
        }),
      ).toEqual({
        status: "FAILED",
        detail: GOAL_DETAIL.actionFailed,
        completed: false,
      });
  });

  it("refuses an impossible verification chronology", () => {
    // A verification that precedes the execution it claims to verify.
    expect(
      verifyActionGoal({
        status: "completed",
        executed_at: "2026-10-01T11:59:30.000Z",
        verified_at: "2026-10-01T11:59:00.000Z",
        now,
      }),
    ).toEqual({
      status: "FAILED",
      detail: GOAL_DETAIL.ledgerInconsistent,
      completed: false,
    });
    // A verification on a row that is not completed.
    expect(
      verifyActionGoal({
        status: "awaiting_approval",
        executed_at: null,
        verified_at: "2026-10-01T11:59:00.000Z",
        now,
      }).status,
    ).toBe("FAILED");
    // A verification with no recorded execution.
    expect(
      verifyActionGoal({
        status: "completed",
        executed_at: null,
        verified_at: "2026-10-01T11:59:00.000Z",
        now,
      }).status,
    ).toBe("FAILED");
    // A completed row with no execution cannot support any claim.
    expect(
      verifyActionGoal({
        status: "completed",
        executed_at: null,
        verified_at: null,
        now,
      }).status,
    ).toBe("FAILED");
    // A forward-dated verification beyond the skew bound is not proof.
    expect(
      verifyActionGoal({
        status: "completed",
        executed_at: "2026-10-01T11:59:00.000Z",
        verified_at: "2026-10-01T13:00:00.000Z",
        now,
      }).status,
    ).toBe("FAILED");
  });

  it("fails closed on an unrecognized status", () => {
    expect(
      verifyActionGoal({
        status: "something-new",
        executed_at: null,
        verified_at: null,
        now,
      }),
    ).toEqual({
      status: "FAILED",
      detail: GOAL_DETAIL.unknownStatus,
      completed: false,
    });
  });
});

describe("workflow window end", () => {
  it("preserves the last source-backed waiting state", () => {
    expect(windowEndedOutcome("WAITING_FOR_USER")).toEqual({
      status: "WAITING_FOR_USER",
      detail: GOAL_DETAIL.windowEndedWaitingForUser,
      completed: false,
    });
    expect(windowEndedOutcome("WAITING_FOR_EXTERNAL")).toEqual({
      status: "WAITING_FOR_EXTERNAL",
      detail: GOAL_DETAIL.windowEndedWaitingForExternal,
      completed: false,
    });
    expect(windowEndedOutcome("RUNNING")).toEqual({
      status: "RUNNING",
      detail: GOAL_DETAIL.windowEndedInFlight,
      completed: false,
    });
  });

  it("records a goal with no observation as an unverified outcome", () => {
    expect(windowEndedOutcome("PENDING")).toEqual({
      status: "FAILED",
      detail: GOAL_DETAIL.verificationDeadline,
      completed: false,
    });
  });

  it("marks the window detail as informational in the vocabulary", () => {
    for (const detail of [
      GOAL_DETAIL.windowEndedWaitingForUser,
      GOAL_DETAIL.windowEndedWaitingForExternal,
      GOAL_DETAIL.windowEndedInFlight,
    ])
      expect(detail.startsWith("goal.window_ended")).toBe(true);
  });
});

describe("goal workflow contract", () => {
  it("sends exactly one opaque goal ID", () => {
    const goalId = randomUUID();
    expect(parseGoalWorkflowInput({ goal_id: goalId })).toEqual({
      goal_id: goalId,
    });
    expect(goalWorkflowId(goalId)).toBe(`navox-assistant-goals:${goalId}`);
    for (const payload of [
      {},
      { goal_id: "not-a-uuid" },
      { goal_id: goalId, cookie: "navox_session=abc" },
      { goal_id: goalId, text: "anything" },
    ])
      expect(() => parseGoalWorkflowInput(payload)).toThrow();
  });

  it("registers the workflow under the name the client starts", () => {
    const source = readFileSync(
      new URL("./temporal/workflows.ts", import.meta.url),
      "utf8",
    );
    expect(source).toContain(
      `export async function ${ASSISTANT_GOAL_WORKFLOW_TYPE}(`,
    );
  });
});

interface FakeDispatcher {
  isActive(input: { goal_id: string }): Promise<boolean>;
  dispatch(input: { goal_id: string }): Promise<void>;
  readonly probes: string[];
  readonly calls: string[];
  /** Simulates a live run: probes answer true and starts are never attempted. */
  active: boolean;
  probeFails: boolean;
  startFails: boolean;
}

function fakeDispatcher(): FakeDispatcher {
  const probes: string[] = [];
  const calls: string[] = [];
  return {
    probes,
    calls,
    active: false,
    probeFails: false,
    startFails: false,
    async isActive({ goal_id }) {
      if (this.probeFails) throw new Error("temporal is unreachable");
      probes.push(goal_id);
      return this.active;
    },
    async dispatch(input) {
      calls.push(input.goal_id);
      if (this.startFails) throw new Error("temporal is unreachable");
    },
  };
}

/** Seeds the owned session and saved turn a goal must reference. */
function seedTurnSource(
  store: ReturnType<typeof createMemoryStore>,
  turn: AssistantTurnRecord = turnRecord(),
): AssistantTurnRecord {
  if (!store.sessions.some((session) => session.id === turn.session_id))
    store.sessions.push({
      id: turn.session_id,
      navox_session_id: NAVOX_SESSION_ID,
      workspace_id: SCOPE.workspace_id,
      user_id: SCOPE.user_id,
      next_sequence: 2,
      created_at: "2026-10-01T12:00:00.000Z",
      updated_at: "2026-10-01T12:00:00.000Z",
      expires_at: "2026-10-31T12:00:00.000Z",
    });
  if (!store.turns.some((candidate) => candidate.id === turn.id))
    store.turns.push(turn);
  return turn;
}

/** Seeds an owned session plus the SPEC-001/003 action a goal may reference. */
function seedActionSource(
  store: ReturnType<typeof createMemoryStore>,
  overrides: Partial<{
    status: string;
    executed_at: string | null;
    verified_at: string | null;
  }> = {},
) {
  const sessionId = randomUUID();
  store.sessions.push({
    id: sessionId,
    navox_session_id: NAVOX_SESSION_ID,
    workspace_id: SCOPE.workspace_id,
    user_id: SCOPE.user_id,
    next_sequence: 1,
    created_at: "2026-10-01T12:00:00.000Z",
    updated_at: "2026-10-01T12:00:00.000Z",
    expires_at: "2026-10-31T12:00:00.000Z",
  });
  const actionId = randomUUID();
  store.actions.set(actionId, {
    user_id: SCOPE.user_id,
    workspace_id: SCOPE.workspace_id,
    status: overrides.status ?? "awaiting_approval",
    executed_at: overrides.executed_at ?? null,
    verified_at: overrides.verified_at ?? null,
  });
  return { sessionId, actionId };
}

describe("goal service dispatch bounds", () => {
  it("records one goal for a saved turn and never duplicates it", async () => {
    const store = createMemoryStore();
    const dispatcher = fakeDispatcher();
    const goals = createAssistantGoalService({ store, dispatcher });
    const turn = seedTurnSource(store);
    const input = { scope: SCOPE, session_id: turn.session_id, turn };

    const first = await goals.recordTurnGoal(input);
    const second = await goals.recordTurnGoal(input);

    expect(store.goals).toHaveLength(1);
    expect(first?.id).toBe(second?.id);
    expect(dispatcher.calls).toHaveLength(1);
    expect(second?.dispatch_state).toBe("DISPATCHED");
    expect(second?.attempts).toBe(1);
  });

  it("is idempotent while a run is live and spends no attempt", async () => {
    const store = createMemoryStore();
    const dispatcher = fakeDispatcher();
    const goals = createAssistantGoalService({ store, dispatcher });
    const turn = seedTurnSource(store);
    const input = { scope: SCOPE, session_id: turn.session_id, turn };
    const goal = await goals.recordTurnGoal(input);
    expect(goal?.attempts).toBe(1);

    // The workflow is still running: a retry must attach, not start again.
    dispatcher.active = true;
    for (let attempt = 0; attempt < 4; attempt += 1)
      await goals.redispatch({ goal_id: goal?.id ?? "", scope: SCOPE });

    expect(dispatcher.calls).toHaveLength(1);
    expect(store.goals[0]?.attempts).toBe(1);
    expect(store.goals[0]?.dispatch_state).toBe("DISPATCHED");
  });

  it("bounds the total dispatch attempts across automatic retries", async () => {
    const store = createMemoryStore();
    const dispatcher = fakeDispatcher();
    dispatcher.startFails = true;
    const goals = createAssistantGoalService({ store, dispatcher });
    const turn = seedTurnSource(store);
    const input = { scope: SCOPE, session_id: turn.session_id, turn };

    for (
      let attempt = 0;
      attempt < GOAL_LIMITS.maxDispatchAttempts + 3;
      attempt += 1
    ) {
      await goals.recordTurnGoal(input);
      await goals.redispatch({
        goal_id: store.goals[0]?.id ?? "",
        scope: SCOPE,
      });
    }

    expect(store.goals).toHaveLength(1);
    expect(store.goals[0]?.attempts).toBe(GOAL_LIMITS.maxDispatchAttempts);
    expect(dispatcher.calls).toHaveLength(GOAL_LIMITS.maxDispatchAttempts);
    expect(store.goals[0]?.status).toBe("PENDING");
    expect(store.goals[0]?.dispatch_state).toBe("DISPATCH_FAILED");
    expect(store.goals[0]?.detail).toBe(GOAL_DETAIL.dispatchFailed);
    expect(store.goals[0]?.completed_at).toBeNull();
  });

  it("does not claim a restart after the budget is exhausted on a closed run", async () => {
    const store = createMemoryStore();
    const dispatcher = fakeDispatcher();
    const goals = createAssistantGoalService({ store, dispatcher });
    const turn = seedTurnSource(store);
    const created = await goals.recordTurnGoal({
      scope: SCOPE,
      session_id: turn.session_id,
      turn,
    });
    const goalId = created?.id ?? "";
    for (
      let attempt = 1;
      attempt < GOAL_LIMITS.maxDispatchAttempts;
      attempt += 1
    )
      expect(
        (await goals.redispatch({ goal_id: goalId, scope: SCOPE })).dispatched,
      ).toBe(true);

    const exhausted = await goals.redispatch({ goal_id: goalId, scope: SCOPE });
    expect(exhausted.dispatched).toBe(false);
    expect(exhausted.goal.attempts).toBe(GOAL_LIMITS.maxDispatchAttempts);
    expect(dispatcher.calls).toHaveLength(GOAL_LIMITS.maxDispatchAttempts);
  });

  it("never exceeds the budget under concurrent requests", async () => {
    const store = createMemoryStore();
    const dispatcher = fakeDispatcher();
    dispatcher.startFails = true;
    const goals = createAssistantGoalService({ store, dispatcher });
    const turn = seedTurnSource(store);
    await goals.recordTurnGoal({
      scope: SCOPE,
      session_id: turn.session_id,
      turn,
    });
    const goalId = store.goals[0]?.id ?? "";

    await Promise.all(
      Array.from({ length: 8 }, () =>
        goals.redispatch({ goal_id: goalId, scope: SCOPE }),
      ),
    );

    expect(store.goals[0]?.attempts).toBe(GOAL_LIMITS.maxDispatchAttempts);
    expect(dispatcher.calls).toHaveLength(GOAL_LIMITS.maxDispatchAttempts);
  });

  it("recovers through the explicit path once a run stops", async () => {
    const store = createMemoryStore();
    const dispatcher = fakeDispatcher();
    dispatcher.startFails = true;
    const goals = createAssistantGoalService({ store, dispatcher });
    const turn = seedTurnSource(store);
    const input = { scope: SCOPE, session_id: turn.session_id, turn };
    await goals.recordTurnGoal(input);
    expect(store.goals[0]?.dispatch_state).toBe("DISPATCH_FAILED");

    dispatcher.startFails = false;
    const recovered = await goals.redispatch({
      goal_id: store.goals[0]?.id ?? "",
      scope: SCOPE,
    });
    expect(recovered.dispatched).toBe(true);
    expect(store.goals).toHaveLength(1);
    expect(store.goals[0]?.dispatch_state).toBe("DISPATCHED");
  });

  it("records a missing dispatcher as an explicit failed dispatch", async () => {
    const store = createMemoryStore();
    const goals = createAssistantGoalService({ store, dispatcher: null });
    const turn = seedTurnSource(store);
    const goal = await goals.recordTurnGoal({
      scope: SCOPE,
      session_id: turn.session_id,
      turn,
    });
    expect(goal?.status).toBe("PENDING");
    expect(goal?.dispatch_state).toBe("DISPATCH_FAILED");
    expect(goal?.detail).toBe(GOAL_DETAIL.dispatchUnconfigured);
    // No start was ever attempted, so no budget is consumed.
    expect(goal?.attempts).toBe(0);
  });

  it("keeps a probe failure bounded and off an already-dispatched goal", async () => {
    const store = createMemoryStore();
    const dispatcher = fakeDispatcher();
    const goals = createAssistantGoalService({ store, dispatcher });
    const turn = seedTurnSource(store);
    const input = { scope: SCOPE, session_id: turn.session_id, turn };

    dispatcher.probeFails = true;
    const first = await goals.recordTurnGoal(input);
    expect(first?.dispatch_state).toBe("DISPATCH_FAILED");
    expect(first?.attempts).toBe(0);

    dispatcher.probeFails = false;
    await goals.recordTurnGoal(input);
    expect(store.goals[0]?.dispatch_state).toBe("DISPATCHED");

    // A later probe failure cannot invalidate a recorded start, but it also
    // cannot truthfully claim that a live run is present right now.
    dispatcher.probeFails = true;
    const after = await goals.redispatch({
      goal_id: store.goals[0]?.id ?? "",
      scope: SCOPE,
    });
    expect(after.dispatched).toBe(false);
    expect(after.goal.dispatch_state).toBe("DISPATCHED");
  });

  it("fences every goal read by the owning workspace and user", async () => {
    const store = createMemoryStore();
    const goals = createAssistantGoalService({
      store,
      dispatcher: fakeDispatcher(),
    });
    const turn = seedTurnSource(store);
    const goal = await goals.recordTurnGoal({
      scope: SCOPE,
      session_id: turn.session_id,
      turn,
    });
    expect(goal).not.toBeNull();
    await expect(
      goals.readGoal({ goal_id: goal?.id ?? "", scope: OTHER_SCOPE }),
    ).rejects.toMatchObject({ code: "not_found" });
    await expect(
      goals.listGoals({ session_id: turn.session_id, scope: OTHER_SCOPE }),
    ).resolves.toEqual([]);
    await expect(
      goals.redispatch({ goal_id: goal?.id ?? "", scope: OTHER_SCOPE }),
    ).rejects.toMatchObject({ code: "not_found" });
  });

  it("records one goal per prepared action, not per request", async () => {
    const store = createMemoryStore();
    const dispatcher = fakeDispatcher();
    const goals = createAssistantGoalService({ store, dispatcher });
    const { sessionId, actionId } = seedActionSource(store);
    const input = { scope: SCOPE, session_id: sessionId, action_id: actionId };
    const first = await goals.recordActionGoal(input);
    const second = await goals.recordActionGoal(input);
    expect(first.id).toBe(second.id);
    expect(store.goals).toHaveLength(1);
    expect(store.goals[0]?.kind).toBe("COMMUNICATION_ACTION");
    expect(store.goals[0]?.action_id).toBe(actionId);
    expect(store.goals[0]?.source_turn_id).toBeNull();
    expect(dispatcher.calls).toHaveLength(1);
  });
});

describe("goal source scope", () => {
  it("refuses a saved turn that does not belong to the named session", async () => {
    const store = createMemoryStore();
    const goals = createAssistantGoalService({
      store,
      dispatcher: fakeDispatcher(),
    });
    const turn = seedTurnSource(store);
    await expect(
      goals.recordTurnGoal({
        scope: SCOPE,
        session_id: randomUUID(),
        turn,
      }),
    ).rejects.toMatchObject({ code: "forbidden" });
    expect(store.goals).toHaveLength(0);
  });

  it("refuses a stored turn from another session at the store boundary", async () => {
    const store = createMemoryStore();
    const turn = seedTurnSource(store);
    await expect(
      store.createGoal({
        id: randomUUID(),
        // Same account, different session than the one that owns the turn.
        session_id: randomUUID(),
        scope: SCOPE,
        kind: "BRIEFING",
        source_turn_id: turn.id,
        action_id: null,
        created_at: "2026-10-01T12:00:00.000Z",
      }),
    ).rejects.toMatchObject({ code: "forbidden" });
    expect(store.goals).toHaveLength(0);
  });

  it("refuses a stored turn from another account at the store boundary", async () => {
    const store = createMemoryStore();
    const foreignTurn = seedTurnSource(store);
    const session = store.sessions[0];
    expect(session).toBeDefined();
    if (!session) return;
    session.workspace_id = OTHER_SCOPE.workspace_id;
    session.user_id = OTHER_SCOPE.user_id;
    await expect(
      store.createGoal({
        id: randomUUID(),
        session_id: foreignTurn.session_id,
        scope: SCOPE,
        kind: "BRIEFING",
        source_turn_id: foreignTurn.id,
        action_id: null,
        created_at: "2026-10-01T12:00:00.000Z",
      }),
    ).rejects.toMatchObject({ code: "forbidden" });
    expect(store.goals).toHaveLength(0);
  });

  it("refuses an action owned by another account", async () => {
    const store = createMemoryStore();
    const { sessionId, actionId } = seedActionSource(store);
    store.actions.set(actionId, {
      user_id: OTHER_SCOPE.user_id,
      workspace_id: OTHER_SCOPE.workspace_id,
      status: "awaiting_approval",
      executed_at: null,
      verified_at: null,
    });
    await expect(
      store.createGoal({
        id: randomUUID(),
        session_id: sessionId,
        scope: SCOPE,
        kind: "COMMUNICATION_ACTION",
        source_turn_id: null,
        action_id: actionId,
        created_at: "2026-10-01T12:00:00.000Z",
      }),
    ).rejects.toMatchObject({ code: "forbidden" });
    expect(store.goals).toHaveLength(0);
  });
});

describe("worker goal activities", () => {
  function seedActionGoal(
    store: ReturnType<typeof createMemoryStore>,
    action: {
      user_id: string;
      workspace_id: string;
      status: string;
      executed_at: string | null;
      verified_at: string | null;
    },
  ) {
    const sessionId = randomUUID();
    store.sessions.push({
      id: sessionId,
      navox_session_id: NAVOX_SESSION_ID,
      workspace_id: SCOPE.workspace_id,
      user_id: SCOPE.user_id,
      next_sequence: 1,
      created_at: "2026-10-01T12:00:00.000Z",
      updated_at: "2026-10-01T12:00:00.000Z",
      expires_at: "2026-10-31T12:00:00.000Z",
    });
    const actionId = randomUUID();
    store.actions.set(actionId, action);
    const record: AssistantGoalRecord = {
      id: randomUUID(),
      session_id: sessionId,
      workspace_id: SCOPE.workspace_id,
      user_id: SCOPE.user_id,
      kind: "COMMUNICATION_ACTION",
      status: "PENDING",
      dispatch_state: "DISPATCHED",
      source_turn_id: null,
      action_id: actionId,
      detail: GOAL_DETAIL.dispatched,
      attempts: 1,
      verify_attempts: 0,
      created_at: "2026-10-01T12:00:00.000Z",
      updated_at: "2026-10-01T12:00:00.000Z",
      completed_at: null,
    };
    store.goals.push(record);
    return record;
  }

  it("leaves a foreign-owned goal untouched instead of changing its status", async () => {
    const store = createMemoryStore();
    const activities = createGoalActivities(store);
    const goal = seedActionGoal(store, {
      user_id: OTHER_SCOPE.user_id,
      workspace_id: OTHER_SCOPE.workspace_id,
      status: "completed",
      executed_at: "2026-10-01T11:59:00.000Z",
      verified_at: "2026-10-01T11:59:30.000Z",
    });

    await expect(activities.verifyGoal({ goal_id: goal.id })).resolves.toEqual({
      outcome: "SOURCE_NOT_OWNED",
      detail: null,
    });
    expect(store.goals[0]?.status).toBe("PENDING");
    expect(store.goals[0]?.completed_at).toBeNull();
    expect(store.goals[0]?.verify_attempts).toBe(0);
  });

  it("reports a missing goal without inventing a status", async () => {
    const store = createMemoryStore();
    const activities = createGoalActivities(store);
    await expect(
      activities.verifyGoal({ goal_id: randomUUID() }),
    ).resolves.toEqual({ outcome: "MISSING", detail: null });
    expect(store.goals).toHaveLength(0);
  });

  it("completes only after the ledger carries a verification, then freezes", async () => {
    const store = createMemoryStore();
    const activities = createGoalActivities(store);
    const goal = seedActionGoal(store, {
      user_id: SCOPE.user_id,
      workspace_id: SCOPE.workspace_id,
      status: "approved",
      executed_at: null,
      verified_at: null,
    });

    await expect(
      activities.verifyGoal({ goal_id: goal.id }),
    ).resolves.toMatchObject({ outcome: "RUNNING" });
    expect(store.goals[0]?.status).toBe("RUNNING");
    expect(store.goals[0]?.completed_at).toBeNull();

    // The owning service executes but has not verified the outcome yet.
    store.actions.set(goal.action_id ?? "", {
      user_id: SCOPE.user_id,
      workspace_id: SCOPE.workspace_id,
      status: "completed",
      executed_at: "2026-10-01T12:01:00.000Z",
      verified_at: null,
    });
    await expect(
      activities.verifyGoal({ goal_id: goal.id }),
    ).resolves.toMatchObject({ outcome: "WAITING_FOR_EXTERNAL" });
    expect(store.goals[0]?.status).toBe("WAITING_FOR_EXTERNAL");

    store.actions.set(goal.action_id ?? "", {
      user_id: SCOPE.user_id,
      workspace_id: SCOPE.workspace_id,
      status: "completed",
      executed_at: "2026-10-01T12:01:00.000Z",
      verified_at: "2026-10-01T12:01:30.000Z",
    });
    await expect(
      activities.verifyGoal({ goal_id: goal.id }),
    ).resolves.toMatchObject({ outcome: "COMPLETED" });
    const completedAt = store.goals[0]?.completed_at;
    expect(completedAt).not.toBeNull();

    // A replayed run may not downgrade or re-complete the frozen goal.
    store.actions.set(goal.action_id ?? "", {
      user_id: SCOPE.user_id,
      workspace_id: SCOPE.workspace_id,
      status: "failed",
      executed_at: "2026-10-01T12:01:00.000Z",
      verified_at: null,
    });
    await expect(
      activities.verifyGoal({ goal_id: goal.id }),
    ).resolves.toMatchObject({ outcome: "COMPLETED" });
    expect(store.goals[0]?.status).toBe("COMPLETED");
    expect(store.goals[0]?.completed_at).toBe(completedAt);
  });

  it("preserves the last waiting state when the window ends", async () => {
    const store = createMemoryStore();
    const activities = createGoalActivities(store);
    const goal = seedActionGoal(store, {
      user_id: SCOPE.user_id,
      workspace_id: SCOPE.workspace_id,
      status: "awaiting_approval",
      executed_at: null,
      verified_at: null,
    });
    await activities.verifyGoal({ goal_id: goal.id });
    expect(store.goals[0]?.status).toBe("WAITING_FOR_USER");

    await expect(
      activities.markGoalDeadline({ goal_id: goal.id }),
    ).resolves.toEqual({
      outcome: "WAITING_FOR_USER",
      detail: GOAL_DETAIL.windowEndedWaitingForUser,
    });
    expect(store.goals[0]?.status).toBe("WAITING_FOR_USER");
    expect(store.goals[0]?.completed_at).toBeNull();

    // An executed but unverified action keeps waiting for its verification.
    store.actions.set(goal.action_id ?? "", {
      user_id: SCOPE.user_id,
      workspace_id: SCOPE.workspace_id,
      status: "completed",
      executed_at: "2026-10-01T12:01:00.000Z",
      verified_at: null,
    });
    await activities.verifyGoal({ goal_id: goal.id });
    await expect(
      activities.markGoalDeadline({ goal_id: goal.id }),
    ).resolves.toEqual({
      outcome: "WAITING_FOR_EXTERNAL",
      detail: GOAL_DETAIL.windowEndedWaitingForExternal,
    });
    expect(store.goals[0]?.status).toBe("WAITING_FOR_EXTERNAL");
    expect(store.goals[0]?.completed_at).toBeNull();
  });

  it("keeps an uncertain action waiting rather than failed", async () => {
    const store = createMemoryStore();
    const activities = createGoalActivities(store);
    const goal = seedActionGoal(store, {
      user_id: SCOPE.user_id,
      workspace_id: SCOPE.workspace_id,
      status: "uncertain",
      executed_at: "2026-10-01T12:01:00.000Z",
      verified_at: null,
    });
    await expect(
      activities.verifyGoal({ goal_id: goal.id }),
    ).resolves.toMatchObject({ outcome: "WAITING_FOR_EXTERNAL" });
    expect(store.goals[0]?.status).toBe("WAITING_FOR_EXTERNAL");
    expect(store.goals[0]?.completed_at).toBeNull();
  });

  it("fails a ledger failure and an ungrounded turn", async () => {
    const store = createMemoryStore();
    const activities = createGoalActivities(store);
    const failedGoal = seedActionGoal(store, {
      user_id: SCOPE.user_id,
      workspace_id: SCOPE.workspace_id,
      status: "failed",
      executed_at: null,
      verified_at: null,
    });
    await expect(
      activities.verifyGoal({ goal_id: failedGoal.id }),
    ).resolves.toMatchObject({ outcome: "FAILED" });
    expect(store.goals[0]?.status).toBe("FAILED");

    // A briefing goal whose saved turn lost its grounded provenance.
    const turn = turnRecord({ presentation: presentation([]) });
    seedTurnSource(store, turn);
    const turnGoal: AssistantGoalRecord = {
      id: randomUUID(),
      session_id: turn.session_id,
      workspace_id: SCOPE.workspace_id,
      user_id: SCOPE.user_id,
      kind: "BRIEFING",
      status: "PENDING",
      dispatch_state: "DISPATCHED",
      source_turn_id: turn.id,
      action_id: null,
      detail: GOAL_DETAIL.dispatched,
      attempts: 1,
      verify_attempts: 0,
      created_at: "2026-10-01T12:00:00.000Z",
      updated_at: "2026-10-01T12:00:00.000Z",
      completed_at: null,
    };
    store.goals.push(turnGoal);
    await expect(
      activities.verifyGoal({ goal_id: turnGoal.id }),
    ).resolves.toEqual({
      outcome: "FAILED",
      detail: GOAL_DETAIL.turnNotReady,
    });
  });

  it("verifies a grounded turn and refuses a cross-session reference", async () => {
    const store = createMemoryStore();
    const activities = createGoalActivities(store);
    const turn = seedTurnSource(store);
    const goal: AssistantGoalRecord = {
      id: randomUUID(),
      session_id: turn.session_id,
      workspace_id: SCOPE.workspace_id,
      user_id: SCOPE.user_id,
      kind: "BRIEFING",
      status: "PENDING",
      dispatch_state: "DISPATCHED",
      source_turn_id: turn.id,
      action_id: null,
      detail: GOAL_DETAIL.dispatched,
      attempts: 1,
      verify_attempts: 0,
      created_at: "2026-10-01T12:00:00.000Z",
      updated_at: "2026-10-01T12:00:00.000Z",
      completed_at: null,
    };
    store.goals.push(goal);
    await expect(
      activities.verifyGoal({ goal_id: goal.id }),
    ).resolves.toMatchObject({ outcome: "COMPLETED" });
    expect(store.goals[0]?.completed_at).not.toBeNull();

    // The same turn referenced from a goal in another session is not owned.
    const foreign: AssistantGoalRecord = {
      ...goal,
      id: randomUUID(),
      session_id: randomUUID(),
      status: "PENDING",
      completed_at: null,
    };
    store.goals.push(foreign);
    await expect(
      activities.verifyGoal({ goal_id: foreign.id }),
    ).resolves.toEqual({ outcome: "SOURCE_NOT_OWNED", detail: null });
    expect(store.goals[1]?.status).toBe("PENDING");
  });
});

describe("runtime goal wiring", () => {
  async function runtimeWithGoals(dispatcher: FakeDispatcher) {
    const store = createMemoryStore();
    const upstream = createFakeUpstream({
      today: {
        intent: "today",
        answer: "1 item needs attention now.",
        items: [],
        supported_queries: ["What am I missing today?"],
        details: [],
      },
    });
    upstream.createAssistantSession = async () => NAVOX_SESSION_ID;
    const runtime = createAssistantRuntime({
      store,
      upstream,
      goalDispatcher: dispatcher,
    });
    const session = await runtime.createSession({
      cookie: "navox_session=abc",
    });
    return { store, runtime, session, upstream };
  }

  it("records exactly one briefing goal for a replayed Today turn", async () => {
    const dispatcher = fakeDispatcher();
    const { store, runtime, session } = await runtimeWithGoals(dispatcher);
    const body = {
      request_id: randomUUID(),
      text: "What am I missing today?",
      modality: "TEXT" as const,
    };

    const first = await runtime.submitTurn({
      cookie: "navox_session=abc",
      session_id: session.id,
      body,
    });
    const replay = await runtime.submitTurn({
      cookie: "navox_session=abc",
      session_id: session.id,
      body,
    });

    expect(first.replay).toBe(false);
    expect(replay.replay).toBe(true);
    expect(store.goals).toHaveLength(1);
    expect(store.goals[0]?.kind).toBe("BRIEFING");
    expect(store.goals[0]?.source_turn_id).toBe(first.turn.id);
    expect(dispatcher.calls).toHaveLength(1);

    const listed = await runtime.listSessionGoals({
      cookie: "navox_session=abc",
      session_id: session.id,
    });
    expect(listed).toHaveLength(1);
    expect(listed[0]?.id).toBe(store.goals[0]?.id);
    const read = await runtime.readGoal({
      cookie: "navox_session=abc",
      goal_id: listed[0]?.id ?? "",
    });
    expect(read.kind).toBe("BRIEFING");
  });

  it("keeps a dispatch outage out of the operator's answer", async () => {
    const dispatcher = fakeDispatcher();
    dispatcher.startFails = true;
    const { store, runtime, session } = await runtimeWithGoals(dispatcher);

    const turn = await runtime.submitTurn({
      cookie: "navox_session=abc",
      session_id: session.id,
      body: {
        request_id: randomUUID(),
        text: "What am I missing today?",
        modality: "TEXT",
      },
    });

    expect(turn.turn.state).toBe("READY");
    expect(store.goals[0]?.dispatch_state).toBe("DISPATCH_FAILED");
    expect(store.goals[0]?.status).toBe("PENDING");

    // The recoverable path is explicit and does not create a second goal.
    dispatcher.startFails = false;
    const recovered = await runtime.redispatchGoal({
      cookie: "navox_session=abc",
      goal_id: store.goals[0]?.id ?? "",
    });
    expect(recovered.dispatched).toBe(true);
    expect(store.goals).toHaveLength(1);
  });

  it("refuses a goal read for another account", async () => {
    const dispatcher = fakeDispatcher();
    const { store, runtime, session } = await runtimeWithGoals(dispatcher);
    await runtime.submitTurn({
      cookie: "navox_session=abc",
      session_id: session.id,
      body: {
        request_id: randomUUID(),
        text: "What am I missing today?",
        modality: "TEXT",
      },
    });
    const goalId = store.goals[0]?.id;
    const foreign = createFakeUpstream({
      account: {
        user_id: OTHER_SCOPE.user_id,
        workspace_id: OTHER_SCOPE.workspace_id,
        email: "other@example.com",
      },
    });
    const otherRuntime = createAssistantRuntime({
      store,
      upstream: foreign,
      goalDispatcher: dispatcher,
    });
    await expect(
      otherRuntime.readGoal({
        cookie: "navox_session=other",
        goal_id: goalId ?? "",
      }),
    ).rejects.toMatchObject({ code: "not_found" });
  });
});

describe("temporal dispatch contract", () => {
  it("probes for a live run and starts one deterministic, id-only workflow", async () => {
    const goalId = randomUUID();
    const calls: { name: string; options: Record<string, unknown> }[] = [];
    let describeCalls = 0;
    const client = {
      workflow: {
        getHandle: (workflowId: string) => ({
          describe: async () => {
            describeCalls += 1;
            throw new WorkflowNotFoundError(
              "no execution",
              workflowId,
              randomUUID(),
            );
          },
        }),
        start: async (name: string, options: Record<string, unknown>) => {
          calls.push({ name, options });
        },
      },
    };
    const dispatcher = createTemporalGoalDispatcher(
      client as unknown as Parameters<typeof createTemporalGoalDispatcher>[0],
      "navox-assistant-goals",
    );

    await expect(dispatcher.isActive({ goal_id: goalId })).resolves.toBe(false);
    await dispatcher.dispatch({ goal_id: goalId });

    expect(describeCalls).toBe(1);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.options.workflowId).toBe(goalWorkflowId(goalId));
    expect(calls[0]?.name).toBe(ASSISTANT_GOAL_WORKFLOW_TYPE);
    expect(calls[0]?.options.args).toEqual([{ goal_id: goalId }]);
    expect(calls[0]?.options.workflowIdConflictPolicy).toBe("USE_EXISTING");
    expect(calls[0]?.options.workflowIdReusePolicy).toBe("ALLOW_DUPLICATE");
  });

  it("reports a live run and propagates a refused start", async () => {
    const goalId = randomUUID();
    const client = {
      workflow: {
        getHandle: () => ({
          describe: async () => ({ status: { name: "RUNNING" } }),
        }),
        start: async () => {
          throw new WorkflowExecutionAlreadyStartedError(
            "already started",
            goalWorkflowId(goalId),
            randomUUID(),
          );
        },
      },
    };
    const dispatcher = createTemporalGoalDispatcher(
      client as unknown as Parameters<typeof createTemporalGoalDispatcher>[0],
      "navox-assistant-goals",
    );
    await expect(dispatcher.isActive({ goal_id: goalId })).resolves.toBe(true);
    await expect(
      dispatcher.dispatch({ goal_id: goalId }),
    ).rejects.toBeInstanceOf(WorkflowExecutionAlreadyStartedError);
  });

  it("surfaces an unexpected probe failure to the caller", async () => {
    const client = {
      workflow: {
        getHandle: () => ({
          describe: async () => {
            throw new Error("cluster unreachable");
          },
        }),
        start: async () => undefined,
      },
    };
    const dispatcher = createTemporalGoalDispatcher(
      client as unknown as Parameters<typeof createTemporalGoalDispatcher>[0],
      "navox-assistant-goals",
    );
    await expect(
      dispatcher.isActive({ goal_id: randomUUID() }),
    ).rejects.toThrow("cluster unreachable");
  });
});

describe("goal service write path", () => {
  it("never lets a store failure fabricate a dispatched goal", async () => {
    const store = createMemoryStore();
    const dispatcher = fakeDispatcher();
    const goals = createAssistantGoalService({ store, dispatcher });
    vi.spyOn(store, "createGoal").mockRejectedValueOnce(
      new Error("database is down"),
    );
    const turn = seedTurnSource(store);
    await expect(
      goals.recordTurnGoal({
        scope: SCOPE,
        session_id: turn.session_id,
        turn,
      }),
    ).rejects.toThrow("database is down");
    expect(store.goals).toHaveLength(0);
    expect(dispatcher.calls).toHaveLength(0);
  });
});
