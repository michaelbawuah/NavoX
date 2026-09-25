export type HealthStatus = "live" | "ready" | "not_ready";

export interface HealthResponse {
  status: HealthStatus;
  service: "navox-api";
  version: string;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  workspace_type: "personal" | string;
}

export interface AuthenticatedAccount {
  id: string;
  email: string;
  display_name: string | null;
  workspace: WorkspaceSummary;
}

export interface GoogleConnection {
  id: string;
  provider: "google";
  status: "active" | "needs_reauthorization" | string;
  granted_scopes: string[];
  last_checked_at: string | null;
  last_error: string | null;
}

export type * from "./subscriptions";
