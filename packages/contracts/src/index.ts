export type HealthStatus = "live" | "ready" | "not_ready";

export interface HealthResponse {
  status: HealthStatus;
  service: "navox-api";
  version: string;
}

