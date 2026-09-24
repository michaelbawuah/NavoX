export type ConnectorClass =
  | "OAUTH_API"
  | "TOKEN_API"
  | "WEBHOOK"
  | "POLLING"
  | "MCP"
  | "GENERIC_API"
  | "BROWSER_ASSISTED"
  | "IMPORT";

export type ConnectorHealthState =
  | "CONNECTED"
  | "DEGRADED"
  | "AUTH_EXPIRED"
  | "RATE_LIMITED"
  | "SYNC_FAILED"
  | "PAUSED"
  | "DISCONNECTED";

export type ConnectorTrustLevel =
  | "NAVOX_FIRST_PARTY"
  | "NAVOX_VERIFIED"
  | "WORKSPACE_PRIVATE"
  | "USER_PRIVATE"
  | "THIRD_PARTY_VERIFIED"
  | "UNVERIFIED";

export interface AuthMethod {
  kind: "oauth2" | "api_token" | "webhook_secret" | "mcp" | "none";
  label: string;
  scopes: string[];
}

export interface CapabilityDefinition {
  name: string;
  description: string;
  sensitive: boolean;
}

export interface ConnectorManifest {
  id: string;
  version: string;
  displayName: string;
  category: string;
  connectorClass: ConnectorClass;
  auth: AuthMethod[];
  resourceTypes: string[];
  capabilities: {
    read: CapabilityDefinition[];
    write: CapabilityDefinition[];
    events: string[];
    incrementalSync: boolean;
  };
  requiredSecrets: string[];
  rateLimitStrategy: "provider_headers" | "fixed_backoff" | "none";
  minimumNavoxConnectorApiVersion: "1";
}

export interface CanonicalResource<T extends Record<string, unknown> = Record<string, unknown>> {
  resourceId: string;
  workspaceId: string;
  connectorConnectionId: string;
  provider: string;
  resourceType: string;
  externalId: string;
  externalParentId?: string;
  version?: string;
  canonical: T;
  providerMetadata: Record<string, unknown>;
  sourceUrl?: string;
  createdAt?: string;
  updatedAt?: string;
  retrievedAt: string;
}

export interface ConnectorHealth {
  state: ConnectorHealthState;
  checkedAt: string;
  reasonCode?: string;
  retryAfterSeconds?: number;
}

export interface SyncRequest {
  connectionId: string;
  workspaceId: string;
  cursor?: string;
  limit: number;
  capabilities: string[];
}

export interface SyncPage {
  resources: CanonicalResource[];
  nextCursor?: string;
  hasMore: boolean;
}

export interface NavoXConnector {
  getManifest(): ConnectorManifest;
  health(connectionId: string): Promise<ConnectorHealth>;
  sync(request: SyncRequest): Promise<SyncPage>;
}
