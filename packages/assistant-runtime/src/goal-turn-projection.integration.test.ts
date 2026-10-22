import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { Client } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  deriveTurnGoalProjection,
  verifyProjectedTurnGoal,
} from "./goal-turn-projection";
import { GOAL_DETAIL } from "./goals";
import {
  type AssistantScope,
  type AssistantStore,
  createPostgresStore,
  type SqlExecutor,
} from "./store";
import { createGoalActivities } from "./temporal/activities";

/** Source-only integration checks. The database must be explicitly disposable. */
const connectionString = process.env.ASSISTANT_TEST_DATABASE_URL;
const suite = connectionString ? describe : describe.skip;
const migrations = [
  "../migrations/0001_assistant_runtime.sql",
  "../migrations/0002_assistant_goals.sql",
  "../migrations/0003_goal_turn_projections.sql",
].map((path) => readFileSync(new URL(path, import.meta.url), "utf8"));
const PRIVATE = "PRIVATE-projection-integration-question-and-answer";
const AT = "2026-10-22T12:01:00.000Z";
type TurnInput = Parameters<AssistantStore["insertTurn"]>[0];

suite("content-free turn projections against disposable PostgreSQL", () => {
  const client = new Client({ connectionString });
  const store = createPostgresStore({
    query: async (text, values) => {
      const result = await client.query(text, values ? [...values] : undefined);
      return { rows: result.rows };
    },
  } satisfies SqlExecutor);
  const activities = createGoalActivities(store);
  const own: AssistantScope = {
    user_id: randomUUID(),
    workspace_id: randomUUID(),
  };
  const foreign: AssistantScope = {
    user_id: randomUUID(),
    workspace_id: randomUUID(),
  };

  beforeAll(async () => {
    await client.connect();
    for (const sql of migrations) await client.query(sql);
    for (const scope of [own, foreign]) {
      await client.query("INSERT INTO users (id, email) VALUES ($1, $2)", [
        scope.user_id,
        `goal-projection-${scope.user_id}@example.test`,
      ]);
      await client.query("INSERT INTO workspaces (id, name) VALUES ($1, $2)", [
        scope.workspace_id,
        `Disposable goal projection ${scope.workspace_id}`,
      ]);
    }
  });

  afterAll(async () => {
    const users = [own.user_id, foreign.user_id];
    await client.query(
      "DELETE FROM assistant_runtime_sessions WHERE user_id = ANY($1::uuid[])",
      [users],
    );
    await client.query(
      "DELETE FROM assistant_sessions WHERE user_id = ANY($1::uuid[])",
      [users],
    );
    await client.query("DELETE FROM workspaces WHERE id = ANY($1::uuid[])", [
      [own.workspace_id, foreign.workspace_id],
    ]);
    await client.query("DELETE FROM users WHERE id = ANY($1::uuid[])", [users]);
    await client.end();
  });

  async function seedSession(scope = own) {
    const navoxSessionId = randomUUID();
    await client.query(
      `INSERT INTO assistant_sessions
         (id, workspace_id, user_id, next_sequence, mode, status)
       VALUES ($1, $2, $3, 1, 'PERSONAL', 'active')`,
      [navoxSessionId, scope.workspace_id, scope.user_id],
    );
    return (
      await store.createSession({
        id: randomUUID(),
        navox_session_id: navoxSessionId,
        scope,
        created_at: "2026-10-22T12:00:00.000Z",
        expires_at: "2026-11-21T12:00:00.000Z",
      })
    ).id;
  }

  async function turnInput(sessionId: string, scope = own): Promise<TurnInput> {
    return {
      id: randomUUID(),
      session_id: sessionId,
      scope,
      sequence: await store.reserveSequence({
        session_id: sessionId,
        scope,
        now: AT,
      }),
      modality: "TEXT",
      state: "READY",
      request_id: randomUUID(),
      request_fingerprint: "a".repeat(64),
      question: PRIVATE,
      response_text: PRIVATE,
      plan: {
        version: 1,
        intents: [
          {
            kind: "today.read",
            capability_id: "today.read",
            question: PRIVATE,
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
        blocks: [{ kind: "ANSWER", text: PRIVATE }],
      },
      action_refs: [],
      created_at: AT,
    };
  }

  /** Simulates an unchanged legacy writer, which knows nothing about v1 proofs. */
  async function insertLegacyTurn(input: TurnInput) {
    await client.query(
      `INSERT INTO assistant_runtime_turns
         (id, session_id, workspace_id, user_id, sequence, modality, state,
          request_id, request_fingerprint, question, response_text, plan,
          decision, presentation, action_refs, created_at)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)`,
      [
        input.id,
        input.session_id,
        input.scope.workspace_id,
        input.scope.user_id,
        input.sequence,
        input.modality,
        input.state,
        input.request_id,
        input.request_fingerprint,
        input.question,
        input.response_text,
        JSON.stringify(input.plan),
        JSON.stringify(input.decision),
        JSON.stringify(input.presentation),
        JSON.stringify(input.action_refs),
        input.created_at,
      ],
    );
  }

  async function goalFor(input: TurnInput) {
    return (
      await store.createGoal({
        id: randomUUID(),
        session_id: input.session_id,
        scope: input.scope,
        kind: "BRIEFING",
        source_turn_id: input.id,
        action_id: null,
        created_at: "2026-10-22T12:02:00.000Z",
      })
    ).goal;
  }

  async function proofRows(turnId: string) {
    return (
      await client.query(
        "SELECT * FROM assistant_runtime_turn_goal_projections WHERE turn_id = $1",
        [turnId],
      )
    ).rows;
  }

  it("captures one owned proof atomically without any plaintext projection columns", async () => {
    const input = await turnInput(await seedSession());
    await store.insertTurn(input);
    const goal = await goalFor(input);
    const rows = await proofRows(input.id);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      turn_id: input.id,
      session_id: input.session_id,
      workspace_id: own.workspace_id,
      user_id: own.user_id,
      ...deriveTurnGoalProjection(input),
    });
    const columns = await client.query<{ column_name: string }>(
      `SELECT column_name FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'assistant_runtime_turn_goal_projections'`,
    );
    expect(columns.rows.map((row) => row.column_name).sort()).toEqual([
      "classification",
      "contract_version",
      "created_at",
      "session_id",
      "turn_id",
      "user_id",
      "workspace_id",
    ]);
    const check = await store.readGoalVerificationProjection({
      goal_id: goal.id,
    });
    expect(check?.source_owned).toBe(true);
    expect(check?.turn).toEqual({
      state: "READY",
      projection: { contract_version: 1, classification: "BRIEFING" },
    });
    expect(JSON.stringify(rows)).not.toContain(PRIVATE);
    expect(JSON.stringify(check)).not.toContain(PRIVATE);
  });

  it("leaves legacy turns unprojected while the unchanged worker can still verify them", async () => {
    const input = await turnInput(await seedSession());
    await insertLegacyTurn(input);
    const goal = await goalFor(input);
    expect(await proofRows(input.id)).toEqual([]);
    expect(
      (await store.readGoalVerificationProjection({ goal_id: goal.id }))?.turn,
    ).toEqual({ state: "READY", projection: null });
    expect(verifyProjectedTurnGoal("BRIEFING", null)).toEqual({
      available: false,
      outcome: null,
    });
    expect(
      (await store.readGoal({ goal_id: goal.id, scope: own }))?.status,
    ).toBe("PENDING");
    const legacy = await store.readGoalSourceCheck({ goal_id: goal.id });
    expect(legacy?.source_owned).toBe(true);
    expect(legacy?.turn?.plan).toEqual(input.plan);
    expect(legacy?.turn?.decision).toEqual(input.decision);
    expect(legacy?.turn?.presentation).toEqual(input.presentation);
    expect((await activities.verifyGoal({ goal_id: goal.id })).outcome).toBe(
      "COMPLETED",
    );
    // Even a completed legacy goal must never be used to invent new proof.
    expect(
      (await store.readGoalVerificationProjection({ goal_id: goal.id }))?.turn
        ?.projection,
    ).toBeNull();
    expect(await proofRows(input.id)).toEqual([]);
  });

  it.each(["state", "plan", "decision", "presentation"] as const)(
    "invalidates proof when the saved %s semantics change",
    async (field) => {
      const input = await turnInput(await seedSession());
      await store.insertTurn(input);
      const goal = await goalFor(input);
      if (field === "state") {
        await client.query(
          "UPDATE assistant_runtime_turns SET state = 'UNAVAILABLE' WHERE id = $1",
          [input.id],
        );
      } else if (field === "plan") {
        await client.query(
          `UPDATE assistant_runtime_turns SET plan = jsonb_set(plan,
             '{intents,0,requires_clarification}', 'true') WHERE id = $1`,
          [input.id],
        );
      } else if (field === "decision") {
        await client.query(
          `UPDATE assistant_runtime_turns SET decision = jsonb_set(decision,
             '{target}', '"untrusted.target"') WHERE id = $1`,
          [input.id],
        );
      } else {
        await client.query(
          `UPDATE assistant_runtime_turns SET presentation = jsonb_set(presentation,
             '{blocks}', $2::jsonb) WHERE id = $1`,
          [
            input.id,
            JSON.stringify([
              { kind: "NOTICE", state: "UNAVAILABLE", text: PRIVATE },
            ]),
          ],
        );
      }
      expect(await proofRows(input.id)).toEqual([]);
      const check = await store.readGoalVerificationProjection({
        goal_id: goal.id,
      });
      expect(check?.source_owned).toBe(true);
      expect(check?.turn?.projection).toBeNull();
      expect((await activities.verifyGoal({ goal_id: goal.id })).outcome).toBe(
        "FAILED",
      );
      expect(
        (await store.readGoal({ goal_id: goal.id, scope: own }))?.completed_at,
      ).toBeNull();
    },
  );

  it("preserves proof for question/answer-only and no-op semantic edits", async () => {
    const input = await turnInput(await seedSession());
    await store.insertTurn(input);
    const goal = await goalFor(input);
    const before = await proofRows(input.id);
    await client.query(
      `UPDATE assistant_runtime_turns SET question = 'Changed display question',
         response_text = 'Changed display answer', state = state WHERE id = $1`,
      [input.id],
    );
    expect(await proofRows(input.id)).toEqual(before);
    expect(
      (await store.readGoalVerificationProjection({ goal_id: goal.id }))?.turn
        ?.projection,
    ).toEqual({ contract_version: 1, classification: "BRIEFING" });
    expect((await activities.verifyGoal({ goal_id: goal.id })).outcome).toBe(
      "COMPLETED",
    );
  });

  it.each(["session", "workspace", "user"] as const)(
    "rejects a projection with a mismatched %s via the composite foreign key",
    async (field) => {
      const input = await turnInput(await seedSession());
      await insertLegacyTurn(input);
      const otherSession = await seedSession();
      await expect(
        client.query(
          `INSERT INTO assistant_runtime_turn_goal_projections
             (turn_id, session_id, workspace_id, user_id, contract_version, classification, created_at)
           VALUES ($1,$2,$3,$4,1,'BRIEFING',$5)`,
          [
            input.id,
            field === "session" ? otherSession : input.session_id,
            field === "workspace" ? foreign.workspace_id : own.workspace_id,
            field === "user" ? foreign.user_id : own.user_id,
            AT,
          ],
        ),
      ).rejects.toMatchObject({ code: "23503" });
      expect(await proofRows(input.id)).toEqual([]);
    },
  );

  it("invalidates proof when a turn moves to another valid owner/session", async () => {
    const input = await turnInput(await seedSession());
    await store.insertTurn(input);
    const goal = await goalFor(input);
    const foreignSession = await seedSession(foreign);
    await client.query(
      `UPDATE assistant_runtime_turns
          SET session_id = $2, workspace_id = $3, user_id = $4 WHERE id = $1`,
      [input.id, foreignSession, foreign.workspace_id, foreign.user_id],
    );
    expect(await proofRows(input.id)).toEqual([]);
    const check = await store.readGoalVerificationProjection({
      goal_id: goal.id,
    });
    expect(check?.source_owned).toBe(false);
    expect(check?.turn).toBeNull();
    expect((await activities.verifyGoal({ goal_id: goal.id })).outcome).toBe(
      "SOURCE_NOT_OWNED",
    );
    expect(
      (await store.readGoal({ goal_id: goal.id, scope: own }))?.status,
    ).toBe("PENDING");
  });

  it("refuses a corrupted goal that points to another owner's saved turn", async () => {
    const ownSession = await seedSession();
    const input = await turnInput(await seedSession(foreign), foreign);
    await store.insertTurn(input);
    const goalId = randomUUID();
    await client.query(
      `INSERT INTO assistant_runtime_goals
         (id, session_id, workspace_id, user_id, kind, status, dispatch_state,
          source_turn_id, action_id, detail, attempts, verify_attempts,
          created_at, updated_at, completed_at)
       VALUES ($1,$2,$3,$4,'BRIEFING','PENDING','NOT_DISPATCHED',$5,NULL,$6,0,0,$7,$7,NULL)`,
      [
        goalId,
        ownSession,
        own.workspace_id,
        own.user_id,
        input.id,
        GOAL_DETAIL.pendingDispatch,
        AT,
      ],
    );
    const check = await store.readGoalVerificationProjection({
      goal_id: goalId,
    });
    expect(check?.source_owned).toBe(false);
    expect(check?.turn).toBeNull();
    expect(JSON.stringify(check)).not.toContain(PRIVATE);
    expect((await activities.verifyGoal({ goal_id: goalId })).outcome).toBe(
      "SOURCE_NOT_OWNED",
    );
    expect(
      (await store.readGoal({ goal_id: goalId, scope: own }))?.status,
    ).toBe("PENDING");
  });

  it.each(["turn", "session"] as const)(
    "cascades projection and goal erasure when their %s is removed",
    async (target) => {
      const input = await turnInput(await seedSession());
      await store.insertTurn(input);
      const goal = await goalFor(input);
      if (target === "turn") {
        await client.query(
          "DELETE FROM assistant_runtime_turns WHERE id = $1",
          [input.id],
        );
      } else {
        expect(
          await store.deleteSession({
            session_id: input.session_id,
            scope: own,
          }),
        ).toBe(true);
      }
      expect(await proofRows(input.id)).toEqual([]);
      expect(await store.readGoal({ goal_id: goal.id, scope: own })).toBeNull();
      expect(
        await store.readGoalVerificationProjection({ goal_id: goal.id }),
      ).toBeNull();
    },
  );

  it.each([
    { version: 2, classification: "BRIEFING" },
    { version: 1, classification: PRIVATE },
  ])(
    "rejects unsupported proof versions and classifications in PostgreSQL",
    async (value) => {
      const input = await turnInput(await seedSession());
      await insertLegacyTurn(input);
      await expect(
        client.query(
          `INSERT INTO assistant_runtime_turn_goal_projections
           (turn_id, session_id, workspace_id, user_id, contract_version, classification, created_at)
         VALUES ($1,$2,$3,$4,$5,$6,$7)`,
          [
            input.id,
            input.session_id,
            own.workspace_id,
            own.user_id,
            value.version,
            value.classification,
            AT,
          ],
        ),
      ).rejects.toMatchObject({ code: "23514" });
      expect(await proofRows(input.id)).toEqual([]);
    },
  );

  it("rolls back the saved turn when its narrowly scoped projection insert fails", async () => {
    const input = await turnInput(await seedSession());
    const name = `goal_projection_test_${randomUUID().replaceAll("-", "")}`;
    // This trigger affects only this test's UUID. Each SQL call autocommits:
    // wrapping the whole store call in a test transaction would conceal a
    // broken two-query implementation that saved its turn before proof failed.
    await client.query(
      `CREATE FUNCTION "${name}"() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
         IF NEW.turn_id = '${input.id}'::uuid THEN
           RAISE EXCEPTION 'Synthetic projection insert failure' USING ERRCODE = '23514';
         END IF;
         RETURN NEW;
       END;
       $$`,
    );
    try {
      await client.query(
        `CREATE TRIGGER "${name}" BEFORE INSERT ON assistant_runtime_turn_goal_projections
         FOR EACH ROW EXECUTE FUNCTION "${name}"()`,
      );
      await expect(store.insertTurn(input)).rejects.toMatchObject({
        code: "23514",
      });
      expect(
        (
          await client.query(
            "SELECT id FROM assistant_runtime_turns WHERE id = $1",
            [input.id],
          )
        ).rows,
      ).toEqual([]);
      expect(await proofRows(input.id)).toEqual([]);
    } finally {
      await client.query(
        `DROP TRIGGER IF EXISTS "${name}" ON assistant_runtime_turn_goal_projections`,
      );
      await client.query(`DROP FUNCTION IF EXISTS "${name}"()`);
    }
  });
});
