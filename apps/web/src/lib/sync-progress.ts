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
  error: { code: string; http_status?: number } | null;
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
        ? { code: value.error.code }
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
        message:
          "The AI service could not process this source. Check the configured service, then retry.",
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
