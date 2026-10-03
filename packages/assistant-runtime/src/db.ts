import { Pool } from "pg";
import type { SqlExecutor, SqlResult } from "./store";

export interface PostgresExecutor extends SqlExecutor {
  close(): Promise<void>;
}

/**
 * Node-runtime PostgreSQL executor. The connection string comes from server
 * configuration only; a client can never supply it.
 */
export function createPostgresExecutor(
  connectionString: string,
): PostgresExecutor {
  const pool = new Pool({
    connectionString,
    max: 4,
    idleTimeoutMillis: 30_000,
  });
  return {
    async query<R = Record<string, unknown>>(
      text: string,
      values?: readonly unknown[],
    ): Promise<SqlResult<R>> {
      const result = await pool.query(text, values ? [...values] : undefined);
      return { rows: result.rows as R[] };
    },
    async close() {
      await pool.end();
    },
  };
}
