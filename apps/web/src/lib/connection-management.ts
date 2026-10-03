/** Content-free view contracts for the authenticated Connections experience. */
export type ConnectionHealth =
  | "CONNECTED"
  | "DEGRADED"
  | "AUTH_EXPIRED"
  | "RATE_LIMITED"
  | "SYNC_FAILED"
  | "PAUSED"
  | "DISCONNECTED";

export interface ConnectorEntry {
  id: string;
  name: string;
  category: string;
  description: string;
  availability: "available" | "setup_pending" | "planned";
  setup_label: string;
  authentication: string;
  read_capabilities: string[];
}

export interface ConnectionSource {
  id: string;
  name: string;
  authorized: boolean;
  health: ConnectionHealth;
  last_synced_at: string | null;
  freshness: "never_synced" | "fresh" | "stale" | "unknown";
  syncing: boolean;
  retry_at: string | null;
  can_sync: boolean;
}

export interface ManagedConnection {
  id: string;
  connector_id: string;
  name: string;
  account_label: string | null;
  health: ConnectionHealth;
  agent_paused: boolean;
  permissions: {
    name: string;
    label: string;
    mode: "read" | "write" | "event";
  }[];
  sources: ConnectionSource[];
  can_pause: boolean;
  can_resume: boolean;
  can_reauthorize: boolean;
  can_disconnect: boolean;
  can_delete_data: boolean;
}

export type LifecycleAction = "disconnect" | "delete-data";

export function canManageLifecycle(
  connection: ManagedConnection,
  action: LifecycleAction,
): boolean {
  return action === "disconnect"
    ? connection.can_disconnect === true && connection.health !== "DISCONNECTED"
    : connection.can_delete_data === true &&
        connection.health === "DISCONNECTED";
}

export function lifecycleConfirmation(action: LifecycleAction): string {
  return action === "disconnect" ? "DISCONNECT" : "DELETE";
}

export function catalogAvailability(
  availability: ConnectorEntry["availability"],
): string {
  if (availability === "available") return "Available";
  if (availability === "setup_pending") return "Setup pending";
  return "Planned";
}

const connectableIds = new Set([
  "google-workspace",
  "generic-import",
  "generic-rest-api",
  "mcp",
]);

/** Canvas onboarding is deferred from the current personal-assistant release. */
export function visibleConnectorCatalog(
  entries: ConnectorEntry[],
): ConnectorEntry[] {
  return entries.filter((entry) => entry.id !== "canvas-lms");
}

export function canOpenConnector(entry: ConnectorEntry): boolean {
  return entry.availability === "available" && connectableIds.has(entry.id);
}

export function canReconnect(connection: ManagedConnection): boolean {
  return (
    connection.can_reauthorize === true &&
    ["google-workspace", "canvas-lms", "generic-rest-api"].includes(
      connection.connector_id,
    )
  );
}

const healthLabels: Record<ConnectionHealth, string> = {
  CONNECTED: "Connected",
  DEGRADED: "Needs checking",
  AUTH_EXPIRED: "Reconnect needed",
  RATE_LIMITED: "Provider cooldown",
  SYNC_FAILED: "Sync needs attention",
  PAUSED: "Paused",
  DISCONNECTED: "Disconnected",
};

export function healthLabel(health: ConnectionHealth): string {
  return healthLabels[health] ?? "Unknown state";
}

export function sourceStatus(source: ConnectionSource): string {
  if (!source.authorized) return "Read access not granted";
  if (source.health === "PAUSED") return "Paused — saved knowledge is retained";
  if (source.health === "DISCONNECTED") return "Disconnected";
  if (source.syncing) return "Sync in progress";
  if (source.retry_at) return "Waiting for the provider cooldown";
  if (source.health !== "CONNECTED") return healthLabel(source.health);
  if (source.freshness === "never_synced") return "No completed sync yet";
  if (source.id === "snapshot")
    return "Imported snapshot processed — not a live source";
  if (source.freshness === "stale") return "Saved data may be out of date";
  if (source.freshness === "unknown") return "Freshness could not be verified";
  return "Recent sync recorded";
}

export function formatConnectionTime(value: string | null): string {
  if (!value) return "Not yet recorded";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Unknown time";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function matchesConnection(
  connection: ManagedConnection,
  query: string,
): boolean {
  const terms = query.trim().toLocaleLowerCase();
  const text = [
    connection.name,
    connection.account_label ?? "",
    healthLabel(connection.health),
    ...connection.permissions.map((permission) => permission.label),
  ].join(" ");
  return text.toLocaleLowerCase().includes(terms);
}

export function connectionPage<T>(items: T[], page: number, size = 6) {
  const pageSize = Number.isFinite(size) ? Math.max(1, Math.floor(size)) : 6;
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const selected = Number.isFinite(page)
    ? Math.min(pages - 1, Math.max(0, Math.floor(page)))
    : 0;
  return {
    items: items.slice(selected * pageSize, (selected + 1) * pageSize),
    page: selected,
    pages,
  };
}

/** A provider redirect is navigated only after an explicit connection action. */
export function googleAuthorizationUrl(value: unknown): string {
  if (typeof value !== "string")
    throw new Error("Invalid authorization response");
  const url = new URL(value);
  if (
    url.protocol !== "https:" ||
    url.hostname !== "accounts.google.com" ||
    (url.port !== "" && url.port !== "443") ||
    url.username ||
    url.password ||
    url.pathname !== "/o/oauth2/v2/auth" ||
    url.hash
  ) {
    throw new Error("Unexpected authorization destination");
  }
  return url.toString();
}
