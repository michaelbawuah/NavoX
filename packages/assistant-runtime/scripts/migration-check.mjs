#!/usr/bin/env node
/**
 * SPEC-008 NavoXbot schema check (M1 runtime tables + M14B goals).
 *
 * Always: static assertions on the TypeScript-owned migration SQL (deterministic,
 * no database). When ASSISTANT_TEST_DATABASE_URL points at a disposable
 * PostgreSQL, the migration is applied and the resulting tables, foreign keys,
 * unique constraints and check constraints are verified against the live
 * catalog. The Alembic checker cannot assert tables outside its own head, so
 * this is the separate check for the NavoXbot schema.
 */
import { readFileSync } from "node:fs";

const migrationPaths = [
  "../migrations/0001_assistant_runtime.sql",
  "../migrations/0002_assistant_goals.sql",
].map((path) => new URL(path, import.meta.url));
const [runtimeSql, goalsSql] = migrationPaths.map((path) =>
  readFileSync(path, "utf8"),
);
const sql = runtimeSql;

const failures = [];
const passed = [];

function check(label, condition) {
  if (condition) passed.push(label);
  else failures.push(label);
}

const normalized = sql.replace(/\s+/g, " ");

check(
  "migration creates assistant_runtime_sessions",
  /CREATE TABLE IF NOT EXISTS assistant_runtime_sessions/.test(sql),
);
check(
  "migration creates assistant_runtime_turns",
  /CREATE TABLE IF NOT EXISTS assistant_runtime_turns/.test(sql),
);
check(
  "sessions reference the SPEC-005 assistant_sessions row",
  /FOREIGN KEY \(navox_session_id\) REFERENCES assistant_sessions \(id\) ON DELETE CASCADE/.test(
    normalized,
  ),
);
check(
  "sessions reference workspaces and users",
  /FOREIGN KEY \(workspace_id\) REFERENCES workspaces \(id\)/.test(
    normalized,
  ) && /FOREIGN KEY \(user_id\) REFERENCES users \(id\)/.test(normalized),
);
check(
  "turns are fenced by session, workspace and user",
  /FOREIGN KEY \(session_id, workspace_id, user_id\) REFERENCES assistant_runtime_sessions \(id, workspace_id, user_id\)/.test(
    normalized,
  ),
);
check(
  "one turn per (session, request_id) is enforced",
  /UNIQUE \(session_id, request_id\)/.test(normalized),
);
check(
  "one turn per (session, sequence) is enforced",
  /UNIQUE \(session_id, sequence\)/.test(normalized),
);
check(
  "durable request claims are keyed by session and request",
  /CREATE TABLE IF NOT EXISTS assistant_runtime_requests/.test(sql) &&
    /PRIMARY KEY \(session_id, request_id\)/.test(normalized),
);
check(
  "a claim only resolves together with its stored turn",
  /\(status = 'PENDING'\) = \(turn_id IS NULL\)/.test(normalized) &&
    /status IN \('PENDING', 'RESOLVED'\)/.test(normalized),
);
check(
  "retention is bounded to 30 days",
  /expires_at <= created_at \+ interval '30 days'/.test(normalized),
);
check(
  "stored question text is bounded",
  /char_length\(question\) BETWEEN 1 AND 500/.test(normalized),
);
check(
  "stored answer text is bounded",
  /char_length\(response_text\) <= 4000/.test(normalized),
);
check(
  "stored fingerprints are full sha-256 hex",
  /char_length\(request_fingerprint\) = 64/.test(normalized),
);
check(
  "turn state vocabulary matches the contracts",
  /state IN \('READY', 'CLARIFY', 'UNAVAILABLE', 'WITHHELD'\)/.test(normalized),
);
check(
  "turn modality vocabulary matches the contracts",
  /modality IN \('TEXT', 'VOICE'\)/.test(normalized),
);
check(
  "expiry is indexed for bounded purges",
  /ON assistant_runtime_sessions \(expires_at\)/.test(normalized),
);
check(
  "the migration does not alter the Alembic head",
  !/\balembic_version\b/.test(sql),
);
check(
  "the migration never drops a Python-owned table",
  !/\bDROP\s+(TABLE|COLUMN)\b/i.test(sql),
);
check(
  "no assistant_sessions columns are altered",
  !/ALTER TABLE assistant_sessions\b/i.test(sql),
);

// ---- M14B bounded personal goals -------------------------------------------

const goalsNormalized = goalsSql.replace(/\s+/g, " ");

check(
  "goals migration creates assistant_runtime_goals",
  /CREATE TABLE IF NOT EXISTS assistant_runtime_goals/.test(goalsSql),
);
check(
  "a goal is fenced by its owning session, workspace and user",
  /FOREIGN KEY \(session_id, workspace_id, user_id\) REFERENCES assistant_runtime_sessions \(id, workspace_id, user_id\)/.test(
    goalsNormalized,
  ),
);
check(
  "a goal names at most one saved turn or one SPEC-001/003 action",
  /\(source_turn_id IS NULL\) <> \(action_id IS NULL\)/.test(goalsNormalized),
);
check(
  "only a consequential-action goal may name an action",
  /\(kind = 'COMMUNICATION_ACTION'\) = \(action_id IS NOT NULL\)/.test(
    goalsNormalized,
  ),
);
check(
  "goal kinds match the shared contracts",
  /kind IN \('BRIEFING', 'MEETING_PREP', 'COMMUNICATION_ACTION'\)/.test(
    goalsNormalized,
  ),
);
check(
  "goal statuses match the shared contracts",
  /status IN \( 'PENDING', 'RUNNING', 'WAITING_FOR_USER', 'WAITING_FOR_EXTERNAL', 'COMPLETED', 'FAILED' \)/.test(
    goalsNormalized,
  ),
);
check(
  "a goal is completed only with a recorded completion instant",
  /\(status = 'COMPLETED'\) = \(completed_at IS NOT NULL\)/.test(
    goalsNormalized,
  ),
);
check(
  "dispatch and verification attempts are bounded",
  /attempts BETWEEN 0 AND 8/.test(goalsNormalized) &&
    /verify_attempts BETWEEN 0 AND 64/.test(goalsNormalized),
);
check(
  "one goal per saved turn is enforced by a partial unique index",
  /CREATE UNIQUE INDEX IF NOT EXISTS assistant_runtime_goals_turn_unique/.test(
    goalsSql,
  ),
);
check(
  "one goal per action is enforced by a partial unique index",
  /CREATE UNIQUE INDEX IF NOT EXISTS assistant_runtime_goals_action_unique/.test(
    goalsSql,
  ),
);
check(
  "the goals migration does not alter the Alembic head",
  !/\balembic_version\b/.test(goalsSql) &&
    !/\bDROP\s+(TABLE|COLUMN)\b/i.test(goalsSql),
);

for (const label of passed) console.log(`PASS: ${label}`);

const databaseUrl =
  process.env.ASSISTANT_TEST_DATABASE_URL ??
  process.env.NAVOX_ASSISTANT_DATABASE_URL;
if (!databaseUrl) {
  console.log(
    "SKIP: live schema check (ASSISTANT_TEST_DATABASE_URL is not set)",
  );
} else {
  const { Client } = await import("pg");
  const client = new Client({ connectionString: databaseUrl });
  try {
    await client.connect();
    for (const migration of [runtimeSql, goalsSql])
      await client.query(migration);
    const { rows: tables } = await client.query(
      "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('assistant_runtime_sessions','assistant_runtime_turns','assistant_runtime_requests','assistant_runtime_goals')",
    );
    check("live: all four runtime tables exist", tables.length === 4);

    const { rows: foreignKeys } = await client.query(
      `SELECT con.conname AS name, rel.relname AS target
         FROM pg_constraint con
         JOIN pg_class src ON src.oid = con.conrelid
         JOIN pg_class rel ON rel.oid = con.confrelid
        WHERE con.contype = 'f' AND src.relname LIKE 'assistant_runtime_%'`,
    );
    const fkTargets = foreignKeys.map((row) => `${row.name}->${row.target}`);
    check(
      "live: foreign keys land on assistant_sessions, workspaces and users",
      ["assistant_sessions", "workspaces", "users"].every((target) =>
        fkTargets.some((entry) => entry.endsWith(`->${target}`)),
      ),
    );

    const { rows: uniques } = await client.query(
      `SELECT con.conname AS name
         FROM pg_constraint con
         JOIN pg_class src ON src.oid = con.conrelid
        WHERE con.contype = 'u' AND src.relname LIKE 'assistant_runtime_%'`,
    );
    const uniqueNames = uniques.map((row) => row.name);
    check(
      "live: (session_id, request_id) and (session_id, sequence) are unique",
      uniqueNames.includes("assistant_runtime_turns_request_unique") &&
        uniqueNames.includes("assistant_runtime_turns_sequence_unique"),
    );

    const { rows: primaries } = await client.query(
      `SELECT con.conname AS name
         FROM pg_constraint con
         JOIN pg_class src ON src.oid = con.conrelid
        WHERE con.contype = 'p' AND src.relname = 'assistant_runtime_requests'`,
    );
    check(
      "live: request claims are keyed by session and request",
      primaries.length === 1,
    );

    const { rows: constraints } = await client.query(
      `SELECT con.conname AS name
         FROM pg_constraint con
         JOIN pg_class src ON src.oid = con.conrelid
        WHERE con.contype = 'c' AND src.relname LIKE 'assistant_runtime_%'`,
    );
    const checkNames = constraints.map((row) => row.name);
    for (const name of [
      "assistant_runtime_sessions_retention_bound",
      "assistant_runtime_turns_question_bound",
      "assistant_runtime_turns_answer_bound",
      "assistant_runtime_turns_fingerprint_length",
      "assistant_runtime_requests_resolution",
      "assistant_runtime_requests_fingerprint_length",
      "assistant_runtime_goals_status_valid",
      "assistant_runtime_goals_completion",
      "assistant_runtime_goals_exactly_one_source",
    ]) {
      check(`live: constraint ${name} exists`, checkNames.includes(name));
    }

    const { rows: goalIndexes } = await client.query(
      `SELECT indexname AS name FROM pg_indexes
        WHERE schemaname = 'public' AND tablename = 'assistant_runtime_goals'`,
    );
    const indexNames = goalIndexes.map((row) => row.name);
    check(
      "live: one goal per saved turn and per action is unique",
      indexNames.includes("assistant_runtime_goals_turn_unique") &&
        indexNames.includes("assistant_runtime_goals_action_unique"),
    );
    console.log("live schema check ran against ASSISTANT_TEST_DATABASE_URL");
  } catch (error) {
    failures.push(
      `live schema check failed: ${error instanceof Error ? error.message : error}`,
    );
  } finally {
    await client.end().catch(() => {});
  }
}

if (failures.length > 0) {
  for (const failure of failures) console.error(`FAIL: ${failure}`);
  process.exit(1);
}
console.log(`migration check passed (${passed.length} assertions)`);
