import {
  AssistantError,
  type AssistantGoalDispatcher,
  type AssistantRuntime,
  createAssistantRuntime,
  createNavoxUpstream,
  createPostgresExecutor,
  createPostgresStore,
  createPurgeScheduler,
  type PurgeScheduler,
} from "@navox/assistant-runtime";
import { createLazyTemporalGoalDispatcher } from "@navox/assistant-runtime/temporal/client";
import { assistantTemporalConfig } from "@navox/assistant-runtime/temporal/config";

interface AssistantRuntimeGlobal {
  __navoxAssistantRuntime?: AssistantRuntime;
  __navoxAssistantClose?: () => Promise<void>;
  __navoxAssistantPurge?: PurgeScheduler;
  __navoxAssistantGoals?: AssistantGoalDispatcher | null;
}

const globalScope = globalThis as AssistantRuntimeGlobal;

let override: AssistantRuntime | null = null;

/** Server configuration for the upstream NavoX API. Never client input. */
export function assistantApiBaseUrl(): string {
  return (
    process.env.NAVOX_API_BASE_URL ??
    process.env.NEXT_PUBLIC_API_BASE_URL ??
    "http://localhost:8000/api/v1"
  );
}

/** Configured public origin for same-origin enforcement, when one is set. */
export function assistantOrigin(): string | null {
  return (
    process.env.NAVOX_ASSISTANT_ORIGIN ??
    process.env.NAVOX_PUBLIC_ORIGIN ??
    null
  );
}

/**
 * The durable goal dispatcher, or null when this deployment has no Temporal
 * target. It opens nothing until the first goal is recorded, so a build or a
 * read-only request never contacts Temporal.
 */
function assistantGoalDispatcher(): AssistantGoalDispatcher | null {
  if (globalScope.__navoxAssistantGoals !== undefined)
    return globalScope.__navoxAssistantGoals;
  const config = assistantTemporalConfig();
  const dispatcher = config.target
    ? createLazyTemporalGoalDispatcher(config)
    : null;
  globalScope.__navoxAssistantGoals = dispatcher;
  return dispatcher;
}

/**
 * Builds the runtime once per Node process. The pool is cached on `globalThis`
 * so a dev reload does not leak connections.
 */
export function getAssistantRuntime(): AssistantRuntime {
  if (override) return override;
  if (globalScope.__navoxAssistantRuntime)
    return globalScope.__navoxAssistantRuntime;

  const databaseUrl =
    process.env.NAVOX_ASSISTANT_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!databaseUrl) {
    throw new AssistantError(
      "misconfigured",
      "The assistant database connection is not configured for this deployment.",
    );
  }
  const executor = createPostgresExecutor(databaseUrl);
  const runtime = createAssistantRuntime({
    store: createPostgresStore(executor),
    upstream: createNavoxUpstream({ baseUrl: assistantApiBaseUrl() }),
    goalDispatcher: assistantGoalDispatcher(),
  });
  // Bounded, non-overlapping retention purge. The timer is unref'd, so row
  // expiry is an access bound with scheduled cleanup, not an exact deadline.
  const configuredInterval = Number(
    process.env.NAVOX_ASSISTANT_PURGE_INTERVAL_MS ?? "",
  );
  const scheduler = createPurgeScheduler({
    purge: () => runtime.purgeExpired(),
    intervalMs:
      Number.isFinite(configuredInterval) && configuredInterval > 0
        ? configuredInterval
        : undefined,
    onError: () => {
      // A failed pass leaves rows for the next tick; requests are unaffected.
    },
  });
  scheduler.start();
  globalScope.__navoxAssistantRuntime = runtime;
  globalScope.__navoxAssistantPurge = scheduler;
  globalScope.__navoxAssistantClose = () => executor.close();
  return runtime;
}

/** Test seam. Production code always builds the runtime from server config. */
export function setAssistantRuntimeForTests(
  runtime: AssistantRuntime | null,
): void {
  override = runtime;
}
