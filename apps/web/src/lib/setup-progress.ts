import type { GoogleConnection } from "./account-access";
import {
  type ConnectorEntry,
  googleAuthorizationUrl,
  type ManagedConnection,
} from "./connection-management";
import type { SyncProgress, SyncSource } from "./sync-progress";

export type SetupConnection = Pick<
  GoogleConnection,
  "id" | "status" | "external_email" | "granted_scopes"
>;
export type SetupStage = "connect" | "access" | "review";
export type SetupState =
  | "connect"
  | "connection_unavailable"
  | "reconnect"
  | "permission"
  | "paused"
  | "checking"
  | "unavailable"
  | "not_started"
  | "syncing"
  | "unknown"
  | "failed"
  | "empty"
  | "ready";
export interface SetupProgress {
  stage: SetupStage;
  state: SetupState;
  title: string;
  message: string;
  hasReadAccess: boolean;
  hasCompletedSync: boolean;
}
export type SetupSyncs = Record<
  string,
  { connectionId: string; source: SyncSource; progress: SyncProgress }
>;

export function hasGoogleReadAccess(connection: SetupConnection): boolean {
  return connection.granted_scopes.some((scope) =>
    [
      "https://www.googleapis.com/auth/gmail.readonly",
      "https://www.googleapis.com/auth/calendar.events.readonly",
      "https://www.googleapis.com/auth/calendar.readonly",
    ].includes(scope),
  );
}

/** Account identity, permission, and completed processing are separate facts. */
export function setupProgress({
  connections,
  managedConnections = [],
  syncs = {},
  paused,
  processingReady,
  readinessMessage,
  itemCount,
  connectionsUnavailable = false,
}: {
  connections: SetupConnection[];
  managedConnections?: ManagedConnection[];
  syncs?: SetupSyncs;
  paused: boolean;
  processingReady: boolean | null;
  readinessMessage?: string;
  itemCount: number;
  connectionsUnavailable?: boolean;
}): SetupProgress {
  const active = connections.filter(
    (connection) => connection.status === "active",
  );
  const readable = active.filter(hasGoogleReadAccess);
  const activeIds = new Set(readable.map((connection) => connection.id));
  const sources = managedConnections
    .filter((connection) => activeIds.has(connection.id))
    .flatMap((connection) => connection.sources)
    .filter(
      (source) =>
        source.authorized && ["gmail", "calendar"].includes(source.id),
    );
  const progress = Object.values(syncs)
    .filter((sync) => activeIds.has(sync.connectionId))
    .map((sync) => sync.progress);
  const hasCompletedSync =
    progress.some((sync) => sync.status === "completed") ||
    sources.some((source) => source.last_synced_at !== null);
  const base = { hasReadAccess: readable.length > 0, hasCompletedSync };
  const result = (
    state: SetupState,
    stage: SetupStage,
    title: string,
    message: string,
  ): SetupProgress => ({ ...base, state, stage, title, message });
  if (connectionsUnavailable)
    return result(
      "connection_unavailable",
      "connect",
      "Check your connected apps",
      "Your connection status could not be loaded. Retry before changing access.",
    );
  if (connections.length === 0)
    return result(
      "connect",
      "connect",
      "Make NavoX useful for your day",
      "Connect Google to find real requests, deadlines, and meetings—or start with a task of your own.",
    );
  if (active.length === 0)
    return result(
      "reconnect",
      "connect",
      "Your Google connection needs attention",
      "Reconnect or resume your account before reading new information. Your saved tasks are still available.",
    );
  if (paused)
    return result(
      "paused",
      "access",
      "New source reading is paused",
      "Resume NavoX to sync your apps. You can still work with saved items and add tasks.",
    );
  if (readable.length === 0)
    return result(
      "permission",
      "access",
      "Choose what NavoX can read",
      "Your Google account is linked. Reading Gmail and Calendar requires a separate choice from you.",
    );
  if (
    progress.some((sync) =>
      ["queued", "running", "retrying"].includes(sync.status),
    ) ||
    sources.some((source) => source.syncing)
  )
    return result(
      "syncing",
      "access",
      "Finding what needs your attention",
      "NavoX is reading the sources you selected. Your day will refresh when processing finishes.",
    );
  if (progress.some((sync) => sync.status === "unavailable"))
    return result(
      "unknown",
      "access",
      "Check your sync progress",
      "The status check could not finish. Your sync may still be running; check it before starting another.",
    );
  if (
    progress.some((sync) =>
      ["failed", "cancelled", "timed_out"].includes(sync.status),
    ) ||
    sources.some((source) =>
      ["SYNC_FAILED", "AUTH_EXPIRED"].includes(source.health),
    )
  )
    return result(
      "failed",
      "access",
      "A source needs another look",
      "One sync did not finish. Review the message below before retrying. Saved items remain available.",
    );
  if (processingReady === false)
    return result(
      "unavailable",
      "access",
      "App syncing is not available right now",
      readinessMessage ??
        "You can still add a task and use your saved items. Try refreshing access later.",
    );
  if (itemCount > 0)
    return result(
      "ready",
      "review",
      "Your day is ready to review",
      "Start with a priority below. Review its source, choose a next action, and track the result.",
    );
  if (hasCompletedSync)
    return result(
      "empty",
      "review",
      "Your sources have been checked",
      "No actionable priorities are showing right now. Add a task, or sync again when something new arrives.",
    );
  if (processingReady === null)
    return result(
      "checking",
      "access",
      "Checking whether sync is available",
      "Your read permission is saved. NavoX is checking before offering your first sync.",
    );
  return result(
    "not_started",
    "access",
    "Start your first sync",
    "Read access is ready. Choose Sync Gmail or Sync Calendar below to find your first priorities.",
  );
}

export function googleSetupAvailable(entries: ConnectorEntry[]): boolean {
  return entries.some(
    (entry) =>
      entry.id === "google-workspace" && entry.availability === "available",
  );
}

/** Same identity-only connection flow used by the full Connections panel. */
export async function startGoogleSetup(apiBaseUrl: string): Promise<string> {
  const response = await fetch(
    `${apiBaseUrl}/connectors/google-workspace/connect`,
    {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: crypto.randomUUID() }),
      signal: AbortSignal.timeout(15_000),
    },
  );
  if (!response.ok) {
    if (response.status === 401)
      throw new Error("Your session expired. Sign in again.");
    if (response.status === 503)
      throw new Error(
        "Google connection is temporarily unavailable. You can add tasks for now.",
      );
    throw new Error("Google could not be connected. Please try again.");
  }
  const payload = (await response.json()) as { authorization_url?: unknown };
  return googleAuthorizationUrl(payload.authorization_url);
}
