/**
 * Server configuration for the bounded assistant-goal worker and client.
 *
 * Every value comes from the process environment. The assistant Temporal
 * target is deliberately separate from the SPEC-001/003 agent queue so the
 * personal assistant keeps its own queue, namespace and worker.
 */
export interface AssistantTemporalConfig {
  /** gRPC `host:port` of the Temporal frontend, or null when unconfigured. */
  target: string | null;
  namespace: string | null;
  taskQueue: string;
}

export const DEFAULT_GOAL_TASK_QUEUE = "navox-assistant-goals";

function trimmed(value: string | undefined): string | null {
  const text = (value ?? "").trim();
  return text.length > 0 ? text : null;
}

export function assistantTemporalConfig(
  env: NodeJS.ProcessEnv = process.env,
): AssistantTemporalConfig {
  return {
    target:
      trimmed(env.NAVOX_ASSISTANT_TEMPORAL_TARGET) ??
      trimmed(env.TEMPORAL_TARGET),
    namespace:
      trimmed(env.NAVOX_ASSISTANT_TEMPORAL_NAMESPACE) ??
      trimmed(env.TEMPORAL_NAMESPACE),
    taskQueue:
      trimmed(env.NAVOX_ASSISTANT_TEMPORAL_TASK_QUEUE) ??
      DEFAULT_GOAL_TASK_QUEUE,
  };
}

/**
 * The PostgreSQL connection the worker uses for its own goal rows. It is the
 * same database the Next runtime writes; the worker never reads a cookie or a
 * source payload, only its own bounded rows.
 */
export function assistantDatabaseUrl(
  env: NodeJS.ProcessEnv = process.env,
): string | null {
  return (
    trimmed(env.NAVOX_ASSISTANT_DATABASE_URL) ??
    trimmed(env.ASSISTANT_DATABASE_URL) ??
    trimmed(env.DATABASE_URL)
  );
}
