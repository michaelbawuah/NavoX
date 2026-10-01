import { fileURLToPath, pathToFileURL } from "node:url";
import { NativeConnection, Worker } from "@temporalio/worker";
import { createPostgresExecutor } from "../db";
import { AssistantError } from "../errors";
import { createPostgresStore } from "../store";
import { createGoalActivities } from "./activities";
import {
  type AssistantTemporalConfig,
  assistantDatabaseUrl,
  assistantTemporalConfig,
} from "./config";

/**
 * The bounded personal-goal worker.
 *
 * It registers exactly one workflow and its two activities, reads only its own
 * goal rows from the same PostgreSQL database as the Next runtime, and never
 * calls an AI or connector provider, approves an action, or executes one.
 */
export interface AssistantGoalWorkerOptions {
  config?: AssistantTemporalConfig;
  databaseUrl?: string | null;
}

export interface AssistantGoalWorkerHandle {
  worker: Worker;
  close: () => Promise<void>;
}

/** The workflow module the Temporal bundler compiles for this worker. */
export function assistantGoalWorkflowsPath(): string {
  return fileURLToPath(new URL("./workflows.ts", import.meta.url));
}

export async function createAssistantGoalWorker(
  options: AssistantGoalWorkerOptions = {},
): Promise<AssistantGoalWorkerHandle> {
  const config = options.config ?? assistantTemporalConfig();
  if (!config.target)
    throw new AssistantError(
      "misconfigured",
      "The assistant Temporal target is not configured.",
    );
  const databaseUrl =
    options.databaseUrl === undefined
      ? assistantDatabaseUrl()
      : options.databaseUrl;
  if (!databaseUrl)
    throw new AssistantError(
      "misconfigured",
      "The assistant database connection is not configured.",
    );

  const executor = createPostgresExecutor(databaseUrl);
  const connection = await NativeConnection.connect({
    address: config.target,
  });
  const worker = await Worker.create({
    connection,
    namespace: config.namespace ?? "default",
    taskQueue: config.taskQueue,
    workflowsPath: assistantGoalWorkflowsPath(),
    activities: createGoalActivities(createPostgresStore(executor)),
  });
  return {
    worker,
    close: async () => {
      await connection.close();
      await executor.close();
    },
  };
}

export async function main(): Promise<void> {
  const { worker, close } = await createAssistantGoalWorker();
  let stopping = false;
  const shutdown = () => {
    if (stopping) return;
    stopping = true;
    worker.shutdown();
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
  try {
    await worker.run();
  } finally {
    await close();
  }
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  void main().catch((error: unknown) => {
    console.error(
      `assistant goal worker failed: ${error instanceof Error ? error.message : error}`,
    );
    process.exitCode = 1;
  });
}
