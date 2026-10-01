#!/usr/bin/env node
/**
 * Applies the TypeScript-owned NavoXbot schema.
 *
 *   NAVOX_ASSISTANT_DATABASE_URL=postgres://... npm run migrate --workspace=@navox/assistant-runtime
 *
 * The SQL is idempotent, and it never touches the Python/Alembic head.
 */
import { readFileSync } from "node:fs";
import { Client } from "pg";

const connectionString =
  process.env.NAVOX_ASSISTANT_DATABASE_URL ??
  process.env.ASSISTANT_DATABASE_URL ??
  process.env.DATABASE_URL;
if (!connectionString) {
  console.error(
    "NAVOX_ASSISTANT_DATABASE_URL (or ASSISTANT_DATABASE_URL / DATABASE_URL) is required to migrate.",
  );
  process.exit(1);
}

const migrationPath = new URL(
  "../migrations/0001_assistant_runtime.sql",
  import.meta.url,
);
const sql = readFileSync(migrationPath, "utf8");

const client = new Client({ connectionString });
try {
  await client.connect();
  await client.query(sql);
  console.log(`applied ${migrationPath.pathname.split("/").pop()}`);
} catch (error) {
  console.error(
    `migration failed: ${error instanceof Error ? error.message : error}`,
  );
  process.exitCode = 1;
} finally {
  await client.end();
}
