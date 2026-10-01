import {
  Client,
  Connection,
  WorkflowIdConflictPolicy,
  WorkflowIdReusePolicy,
  WorkflowNotFoundError,
} from "@temporalio/client";
import type { AssistantGoalDispatcher } from "../goal-service";
import {
  ASSISTANT_GOAL_WORKFLOW_TYPE,
  GOAL_LIMITS,
  goalWorkflowId,
} from "../goals";
import type { AssistantTemporalConfig } from "./config";

/**
 * The Temporal client seam for bounded personal goals.
 *
 * A duplicate dispatch is never a second durable job: the workflow ID is
 * derived from the opaque goal ID and an active run is reused
 * (`USE_EXISTING`). A closed run may be restarted after an explicit bounded
 * redispatch so an approval or external verification can be observed. The
 * goal service refuses a completed goal and atomically caps total starts.
 * The start only ever carries the goal ID.
 */
export function createTemporalGoalDispatcher(
  client: Client,
  taskQueue: string,
): AssistantGoalDispatcher {
  const handleFor = (goalId: string) =>
    client.workflow.getHandle(goalWorkflowId(goalId));

  return {
    async isActive({ goal_id }) {
      // A read-only probe. It starts nothing and consumes no dispatch attempt,
      // so a request against a live run stays idempotent.
      try {
        const description = await handleFor(goal_id).describe();
        return description.status.name === "RUNNING";
      } catch (error) {
        if (error instanceof WorkflowNotFoundError) return false;
        throw error;
      }
    },

    async dispatch({ goal_id }) {
      await client.workflow.start(ASSISTANT_GOAL_WORKFLOW_TYPE, {
        taskQueue,
        workflowId: goalWorkflowId(goal_id),
        args: [{ goal_id }],
        workflowIdConflictPolicy: WorkflowIdConflictPolicy.USE_EXISTING,
        workflowIdReusePolicy: WorkflowIdReusePolicy.ALLOW_DUPLICATE,
        // A backstop only: the workflow itself bounds its own lifetime and
        // records a truthful outcome before this deadline is reached.
        workflowExecutionTimeout: GOAL_LIMITS.workflowLifetimeMs + 15 * 60_000,
      });
    },
  };
}

export async function connectAssistantGoalClient(
  config: AssistantTemporalConfig,
): Promise<Client> {
  const connection = await Connection.connect({
    address: config.target ?? "",
    connectTimeout: 5_000,
  });
  return new Client({
    connection,
    namespace: config.namespace ?? "default",
  });
}

/**
 * A dispatcher that connects on first use and drops a failed connection so the
 * next attempt can recover. Nothing is opened at import time, so the Next
 * server never talks to Temporal during a build.
 */
export function createLazyTemporalGoalDispatcher(
  config: AssistantTemporalConfig,
): AssistantGoalDispatcher {
  let cached: Promise<Client> | null = null;
  function client(): Promise<Client> {
    if (!cached) {
      cached = connectAssistantGoalClient(config).catch((error: unknown) => {
        cached = null;
        throw error;
      });
    }
    return cached;
  }
  return {
    async isActive(input) {
      const connected = await client();
      return createTemporalGoalDispatcher(connected, config.taskQueue).isActive(
        input,
      );
    },
    async dispatch({ goal_id }) {
      const connected = await client();
      await createTemporalGoalDispatcher(connected, config.taskQueue).dispatch({
        goal_id,
      });
    },
  };
}
