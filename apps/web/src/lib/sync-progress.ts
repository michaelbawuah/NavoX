export type SyncSource = "gmail" | "calendar";
export type SyncState =
  | "queued"
  | "running"
  | "retrying"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out"
  | "unavailable";

export interface SyncProgress {
  workflow_id: string;
  status: SyncState;
  commitment_count: number | null;
  error: {
    code: string;
    provider_code?: string;
    http_status?: number;
    retry_after_seconds?: number;
  } | null;
}

const syncStates: SyncState[] = [
  "queued",
  "running",
  "retrying",
  "completed",
  "failed",
  "cancelled",
  "timed_out",
  "unavailable",
];

const aiErrorMessages = {
  authentication_failed:
    "The AI service rejected NavoX's credentials. Check the configured AI key before retrying.",
  permission_denied:
    "The AI service denied this request. Check the configured AI project's permissions before retrying.",
  model_unavailable:
    "The configured AI model is unavailable to this project. Check the model setting before retrying.",
  quota_exhausted:
    "The AI service reported an exhausted quota. Check the AI project's usage limits before retrying.",
  rate_limited:
    "The AI service is limiting requests. Wait before retrying this sync.",
  invalid_schema:
    "The AI service rejected NavoX's extraction schema. Report this diagnostic for a fix.",
  unsupported_parameter:
    "The AI model rejected a request parameter. Check the model configuration and report this diagnostic.",
  invalid_request:
    "The AI service rejected the request. Report this diagnostic before retrying.",
  provider_unavailable:
    "The AI service is temporarily unavailable. Try this sync again later.",
  timeout:
    "The AI request timed out before a complete response was received. Check provider connectivity and response time before retrying.",
  transport_error:
    "The connection to the AI service failed. Check network access from NavoX's processing worker before retrying.",
  incomplete_response:
    "The AI service returned an unfinished response. Report this diagnostic if it persists.",
  invalid_response:
    "The AI service returned an unreadable response. Report this diagnostic if it persists.",
  provider_error:
    "The AI service could not process this source. Check the configured service, then retry.",
} as const;

function isAIProviderCode(
  value: unknown,
): value is keyof typeof aiErrorMessages {
  return typeof value === "string" && Object.hasOwn(aiErrorMessages, value);
}

export function isSyncTerminal(status: SyncState): boolean {
  return ["completed", "failed", "cancelled", "timed_out"].includes(status);
}

export function unavailableSync(workflowId: string): SyncProgress {
  return {
    workflow_id: workflowId,
    status: "unavailable",
    commitment_count: null,
    error: null,
  };
}

function validatedProgress(payload: unknown, workflowId: string): SyncProgress {
  if (!payload || typeof payload !== "object") {
    return unavailableSync(workflowId);
  }
  const value = payload as Partial<SyncProgress>;
  if (
    value.workflow_id !== workflowId ||
    !syncStates.includes(value.status as SyncState)
  ) {
    return unavailableSync(workflowId);
  }
  return {
    workflow_id: workflowId,
    status: value.status as SyncState,
    commitment_count:
      typeof value.commitment_count === "number" &&
      Number.isSafeInteger(value.commitment_count) &&
      value.commitment_count >= 0
        ? value.commitment_count
        : null,
    error:
      value.error && typeof value.error.code === "string"
        ? {
            code: value.error.code,
            ...(value.error.code === "provider_request_failed" &&
            isAIProviderCode(value.error.provider_code)
              ? { provider_code: value.error.provider_code }
              : {}),
            ...(typeof value.error.retry_after_seconds === "number" &&
            Number.isSafeInteger(value.error.retry_after_seconds) &&
            value.error.retry_after_seconds > 0
              ? { retry_after_seconds: value.error.retry_after_seconds }
              : {}),
          }
        : null,
  };
}

/** Observe an existing job only. Checking progress never queues another sync. */
export function monitorSync({
  workflowId,
  read,
  onProgress,
  onTerminal,
  onPause,
}: {
  workflowId: string;
  read: (workflowId: string, signal: AbortSignal) => Promise<unknown>;
  onProgress: (progress: SyncProgress) => void;
  onTerminal: (progress: SyncProgress) => void;
  onPause: () => void;
}): () => void {
  let stopped = false;
  let unavailableCount = 0;
  let checks = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;

  async function poll() {
    controller = new AbortController();
    const timeout = setTimeout(() => controller?.abort(), 15_000);
    let progress: SyncProgress;
    try {
      progress = validatedProgress(
        await read(workflowId, controller.signal),
        workflowId,
      );
    } catch {
      progress = unavailableSync(workflowId);
    } finally {
      clearTimeout(timeout);
    }
    if (stopped) return;
    checks += 1;
    onProgress(progress);
    if (isSyncTerminal(progress.status)) {
      stopped = true;
      onTerminal(progress);
      return;
    }
    unavailableCount =
      progress.status === "unavailable" ? unavailableCount + 1 : 0;
    // Pause after repeated failures or a long-running session. The owner can
    // resume status checks without submitting duplicate source-processing work.
    if (unavailableCount >= 3 || checks >= 200) {
      stopped = true;
      onPause();
      return;
    }
    timer = setTimeout(() => void poll(), 3_000);
  }

  void poll();
  return () => {
    stopped = true;
    clearTimeout(timer);
    controller?.abort();
  };
}

export function syncErrorHelp(
  source: SyncSource,
  code?: string,
  providerCode?: string,
): { message: string; reconnect: boolean } {
  const name = source === "gmail" ? "Gmail" : "Google Calendar";
  switch (code) {
    case "google_api_disabled":
      return {
        message: `Enable the ${name} API in the Google Cloud project used by NavoX, then retry this sync.`,
        reconnect: false,
      };
    case "google_scope_missing":
    case "google_authentication_failed":
      return {
        message:
          "Google could not authorize this read. Reconnect read access, then retry this sync.",
        reconnect: true,
      };
    case "google_permission_denied":
      return {
        message:
          "Google rejected this read. Check account or Workspace access policies; reconnect read access if your permissions changed.",
        reconnect: true,
      };
    case "google_token_unavailable":
      return {
        message:
          "Google credentials are unavailable. Check NavoX's Google credential configuration and reconnect read access.",
        reconnect: true,
      };
    case "google_rate_limited":
      return {
        message:
          "Google is limiting requests. Wait a little, then retry this sync.",
        reconnect: false,
      };
    case "google_daily_limit_exceeded":
      return {
        message: `Google reported a daily limit for this request. Check the ${name} API quotas in NavoX's Google Cloud project, then retry after the limit resets or the configuration is corrected.`,
        reconnect: false,
      };
    case "google_quota_exceeded":
      return {
        message: `Google reported an exhausted quota. Check which ${name} API quota metric and limit were reached in NavoX's Google Cloud project before retrying.`,
        reconnect: false,
      };
    case "google_provider_unavailable":
    case "google_transport_error":
      return {
        message: "Google could not be reached. Try this sync again shortly.",
        reconnect: false,
      };
    case "google_invalid_response":
      return {
        message:
          "Google returned an unreadable response. Retry this sync; if it continues, check the processing diagnostics.",
        reconnect: false,
      };
    case "provider_request_failed":
      return {
        message: isAIProviderCode(providerCode)
          ? aiErrorMessages[providerCode]
          : aiErrorMessages.provider_error,
        reconnect: false,
      };
    case "extraction_validation_failed":
      return {
        message:
          "NavoX could not validate the extracted information. Review the processing diagnostics before retrying.",
        reconnect: false,
      };
    default:
      return {
        message:
          "The sync could not finish. Try again; if it keeps failing, check the processing diagnostics.",
        reconnect: false,
      };
  }
}

export function syncProgressMessage(progress: SyncProgress): string {
  switch (progress.status) {
    case "queued":
      return "Queued · waiting for the processing worker.";
    case "running":
      return "Processing · reading and reviewing this source.";
    case "retrying":
      if (
        progress.error?.retry_after_seconds &&
        [
          "google_rate_limited",
          "google_daily_limit_exceeded",
          "google_quota_exceeded",
        ].includes(progress.error.code)
      ) {
        return "Retrying · waiting for the cooldown before the next attempt.";
      }
      return "Retrying · the previous attempt did not finish.";
    case "completed":
      return progress.commitment_count === null
        ? "Sync complete · source processing finished."
        : `Sync complete · ${progress.commitment_count} ${progress.commitment_count === 1 ? "commitment" : "commitments"} processed.`;
    case "failed":
      return "Sync failed.";
    case "cancelled":
      return "Sync cancelled. You can start it again.";
    case "timed_out":
      return "Sync timed out. Check the worker, then try again.";
    case "unavailable":
      return "Sync status is temporarily unavailable. The job may still be running.";
  }
}
