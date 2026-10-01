import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { Client, Connection } from "@temporalio/client";
import { NativeConnection, Worker } from "@temporalio/worker";
import { Client as PgClient } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createAssistantGoalService } from "../goal-service";
import { goalWorkflowId } from "../goals";
import { createPostgresStore, type SqlExecutor } from "../store";
import { createGoalActivities } from "./activities";
import { createTemporalGoalDispatcher } from "./client";
import { assistantGoalWorkflowsPath } from "./worker";

/**
 * Durable-workflow integration coverage.
 *
 * Runs only when ASSISTANT_TEST_DATABASE_URL and TEMPORAL_TEST_ADDRESS both
 * point at a disposable stack. It seeds its own account, session and turn,
 * runs the real TypeScript worker against the real Temporal server, and then
 * inspects the resulting workflow history for leaked content.
 */
const connectionString = process.env.ASSISTANT_TEST_DATABASE_URL;
const temporalAddress =
  process.env.TEMPORAL_TEST_ADDRESS ?? process.env.TEMPORAL_TARGET;
const enabled = Boolean(connectionString && temporalAddress);
const suite = enabled ? describe : describe.skip;
const migrations = [
  "../../migrations/0001_assistant_runtime.sql",
  "../../migrations/0002_assistant_goals.sql",
].map((path) => readFileSync(new URL(path, import.meta.url), "utf8"));

const QUESTION = "What am I missing today?";
const ANSWER = "1 item needs attention now.";

suite("assistant goal workflow against a disposable Temporal server", () => {
  const client = new PgClient({ connectionString });
  const store = createPostgresStore({
    query: async (text, values) => {
      const result = await client.query(text, values ? [...values] : undefined);
      return { rows: result.rows };
    },
  } satisfies SqlExecutor);
  const userId = randomUUID();
  const workspaceId = randomUUID();
  const scope = { user_id: userId, workspace_id: workspaceId };
  const taskQueue = `navox-assistant-goals-m14b-${randomUUID().slice(0, 8)}`;
  let temporal: Client;
  let native: NativeConnection;
  let worker: Worker;

  beforeAll(async () => {
    await client.connect();
    for (const sql of migrations) await client.query(sql);
    await client.query("INSERT INTO users (id, email) VALUES ($1, $2)", [
      userId,
      `m14b-temporal-${userId}@example.test`,
    ]);
    await client.query("INSERT INTO workspaces (id, name) VALUES ($1, $2)", [
      workspaceId,
      "M14B disposable Temporal workspace",
    ]);
    const connection = await Connection.connect({
      address: temporalAddress ?? "",
    });
    temporal = new Client({ connection });
    native = await NativeConnection.connect({
      address: temporalAddress ?? "",
    });
    worker = await Worker.create({
      connection: native,
      taskQueue,
      workflowsPath: assistantGoalWorkflowsPath(),
      activities: createGoalActivities(store),
    });
  }, 120_000);

  afterAll(async () => {
    try {
      worker?.shutdown();
    } catch {
      // The worker may already be draining after runUntil returned.
    }
    await client
      .query("DELETE FROM assistant_runtime_sessions WHERE user_id = $1", [
        userId,
      ])
      .catch(() => {});
    await client
      .query("DELETE FROM assistant_sessions WHERE user_id = $1", [userId])
      .catch(() => {});
    await client
      .query("DELETE FROM workspaces WHERE id = $1", [workspaceId])
      .catch(() => {});
    await client
      .query("DELETE FROM users WHERE id = $1", [userId])
      .catch(() => {});
    await client.end().catch(() => {});
  }, 60_000);

  async function seedTurn() {
    const upstreamSessionId = randomUUID();
    await client.query(
      `INSERT INTO assistant_sessions (id, workspace_id, user_id, next_sequence, mode, status)
       VALUES ($1, $2, $3, 1, 'PERSONAL', 'active')`,
      [upstreamSessionId, workspaceId, userId],
    );
    const session = await store.createSession({
      id: randomUUID(),
      navox_session_id: upstreamSessionId,
      scope,
      created_at: "2026-10-01T12:00:00.000Z",
      expires_at: "2026-10-31T12:00:00.000Z",
    });
    const sequence = await store.reserveSequence({
      session_id: session.id,
      scope,
      now: "2026-10-01T12:01:00.000Z",
    });
    const turn = await store.insertTurn({
      id: randomUUID(),
      session_id: session.id,
      scope,
      sequence,
      modality: "TEXT",
      state: "READY",
      request_id: randomUUID(),
      request_fingerprint: "a".repeat(64),
      question: QUESTION,
      response_text: ANSWER,
      plan: {
        version: 1,
        intents: [
          {
            kind: "today.read",
            capability_id: "today.read",
            question: QUESTION,
            confidence: 1,
            entity: { kind: "NONE", value: null, confidence: 1 },
            time: { kind: "NONE", expression: null, confidence: 1 },
            reference: { kind: "NONE", ordinal: null, turn_id: null },
            requires_clarification: false,
            clarification: null,
          },
        ],
      },
      decision: {
        kind: "DELEGATE",
        capability_id: "today.read",
        target: "today.query",
        reason: "today.today",
        requires_approval: false,
        action_state: "NONE",
        action_id: null,
        response_state: "READY",
      },
      presentation: {
        presentation: "TEXT",
        speak: false,
        speech_text: null,
        delivery: "AUTOMATIC",
        blocks: [{ kind: "ANSWER", text: ANSWER }],
      },
      action_refs: [],
      created_at: "2026-10-01T12:01:00.000Z",
    });
    return { session, turn };
  }

  it("verifies a grounded turn durably and keeps history to the goal ID", async () => {
    const { session, turn } = await seedTurn();
    const dispatcher = createTemporalGoalDispatcher(temporal, taskQueue);
    const goals = createAssistantGoalService({ store, dispatcher });
    const goal = await goals.recordTurnGoal({
      scope,
      session_id: session.id,
      turn,
    });
    expect(goal?.dispatch_state).toBe("DISPATCHED");
    expect(goal?.attempts).toBe(1);
    if (!goal) return;

    const handle = temporal.workflow.getHandle(goalWorkflowId(goal.id));
    const outcome = await worker.runUntil(handle.result());
    expect(outcome).toMatchObject({ outcome: "COMPLETED" });

    const stored = await store.readGoal({ goal_id: goal.id, scope });
    expect(stored?.status).toBe("COMPLETED");
    expect(stored?.completed_at).not.toBeNull();

    // The live probe now sees a closed run, and a retry against an already
    // verified goal starts nothing and spends no further attempt.
    await expect(dispatcher.isActive({ goal_id: goal.id })).resolves.toBe(
      false,
    );
    const again = await goals.redispatch({ goal_id: goal.id, scope });
    expect(again.dispatched).toBe(false);
    const unchanged = await store.readGoal({ goal_id: goal.id, scope });
    expect(unchanged?.attempts).toBe(1);
    expect(unchanged?.completed_at).toBe(stored?.completed_at);

    const history = await handle.fetchHistory();
    const events = history.events ?? [];
    const started = events.find(
      (event) => event.workflowExecutionStartedEventAttributes,
    );
    const payloads =
      started?.workflowExecutionStartedEventAttributes?.input?.payloads ?? [];
    expect(
      // The JSON payload bytes are the exact body a workflow received; no
      // cookie, turn text or source content may be among them.
      payloads.map((payload) =>
        new TextDecoder().decode(payload.data ?? new Uint8Array()),
      ),
    ).toEqual([JSON.stringify({ goal_id: goal.id })]);

    // No cookie, question, answer or source content may reach history.
    const raw = JSON.stringify(history);
    expect(raw).not.toContain(QUESTION);
    expect(raw).not.toContain(ANSWER);
    expect(raw).not.toContain("navox_session");
    expect(raw).not.toContain(userId);
    expect(raw).not.toContain(workspaceId);
  }, 180_000);

  it("restarts a closed successful run when a goal later has a verifiable source", async () => {
    // A prior successful Temporal close must not cause redispatch to claim
    // success without starting a new run.
    worker = await Worker.create({
      connection: native,
      taskQueue,
      workflowsPath: assistantGoalWorkflowsPath(),
      activities: createGoalActivities(store),
    });
    const dispatcher = createTemporalGoalDispatcher(temporal, taskQueue);
    const goalId = randomUUID();
    const workflowId = goalWorkflowId(goalId);
    const goals = createAssistantGoalService({ store, dispatcher });

    await worker.runUntil(async () => {
      await dispatcher.dispatch({ goal_id: goalId });
      const first = await temporal.workflow.getHandle(workflowId).result();
      expect(first).toMatchObject({ outcome: "MISSING" });
      await expect(dispatcher.isActive({ goal_id: goalId })).resolves.toBe(
        false,
      );

      const { session, turn } = await seedTurn();
      await store.createGoal({
        id: goalId,
        session_id: session.id,
        scope,
        kind: "BRIEFING",
        source_turn_id: turn.id,
        action_id: null,
        created_at: new Date().toISOString(),
      });
      const retried = await goals.redispatch({ goal_id: goalId, scope });
      expect(retried.dispatched).toBe(true);
      expect(retried.goal.attempts).toBe(1);

      const second = await temporal.workflow.getHandle(workflowId).result();
      expect(second).toMatchObject({ outcome: "COMPLETED" });
      const stored = await store.readGoal({ goal_id: goalId, scope });
      expect(stored?.status).toBe("COMPLETED");
    });
  }, 180_000);
});
