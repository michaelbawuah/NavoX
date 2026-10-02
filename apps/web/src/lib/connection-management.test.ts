import { describe, expect, it } from "vitest";
import {
  type ConnectionSource,
  type ConnectorEntry,
  canManageLifecycle,
  canOpenConnector,
  canReconnect,
  catalogAvailability,
  connectionPage,
  formatConnectionTime,
  googleAuthorizationUrl,
  healthLabel,
  lifecycleConfirmation,
  type ManagedConnection,
  matchesConnection,
  sourceStatus,
  visibleConnectorCatalog,
} from "./connection-management";

const source: ConnectionSource = {
  id: "gmail",
  name: "Gmail",
  authorized: true,
  health: "CONNECTED",
  last_synced_at: null,
  freshness: "never_synced",
  syncing: false,
  retry_at: null,
  can_sync: true,
};

describe("connection status", () => {
  it("does not call an authorized but unsynced source up to date", () => {
    expect(sourceStatus(source)).toBe("No completed sync yet");
    expect(sourceStatus({ ...source, freshness: "stale" })).toContain(
      "out of date",
    );
    expect(sourceStatus({ ...source, syncing: true })).toBe("Sync in progress");
  });

  it("distinguishes permission, pause, auth failure and cooldown", () => {
    expect(sourceStatus({ ...source, authorized: false })).toContain(
      "not granted",
    );
    expect(
      sourceStatus({ ...source, health: "PAUSED", syncing: true }),
    ).toContain("Paused");
    expect(sourceStatus({ ...source, health: "AUTH_EXPIRED" })).toBe(
      "Reconnect needed",
    );
    expect(
      sourceStatus({ ...source, retry_at: "2026-10-01T12:00:00Z" }),
    ).toContain("cooldown");
    expect(healthLabel("DISCONNECTED")).toBe("Disconnected");
  });

  it("handles missing and invalid dates without inventing a time", () => {
    expect(formatConnectionTime(null)).toBe("Not yet recorded");
    expect(formatConnectionTime("not a date")).toBe("Unknown time");
  });

  it("searches actual labels and accounts without requiring provider metadata", () => {
    const connection = {
      name: "Google Workspace",
      account_label: "owner@example.com",
      health: "CONNECTED",
      permissions: [{ label: "Read calendar events" }],
    } as ManagedConnection;
    expect(matchesConnection(connection, " OWNER@EXAMPLE ")).toBe(true);
    expect(matchesConnection(connection, "calendar")).toBe(true);
    expect(matchesConnection(connection, "Canvas")).toBe(false);
  });
});

describe("connection lifecycle availability", () => {
  const connection = {
    health: "CONNECTED",
    can_disconnect: false,
    can_delete_data: false,
  } as ManagedConnection;

  it("keeps unfinished destructive operations unavailable by default", () => {
    expect(canManageLifecycle(connection, "disconnect")).toBe(false);
    expect(canManageLifecycle(connection, "delete-data")).toBe(false);
    expect(
      canManageLifecycle(
        { ...connection, can_delete_data: true },
        "delete-data",
      ),
    ).toBe(false);
  });

  it("requires a disconnected state and API capability before deletion", () => {
    expect(
      canManageLifecycle({ ...connection, can_disconnect: true }, "disconnect"),
    ).toBe(true);
    const disconnected = {
      ...connection,
      health: "DISCONNECTED" as const,
      can_disconnect: true,
      can_delete_data: true,
    };
    expect(canManageLifecycle(disconnected, "disconnect")).toBe(false);
    expect(canManageLifecycle(disconnected, "delete-data")).toBe(true);
    expect(lifecycleConfirmation("disconnect")).toBe("DISCONNECT");
    expect(lifecycleConfirmation("delete-data")).toBe("DELETE");
  });

  it("shows setup pending and planned as separate statuses", () => {
    expect(catalogAvailability("setup_pending")).toBe("Setup pending");
    expect(catalogAvailability("planned")).toBe("Planned");
    expect(catalogAvailability("available")).toBe("Available");
  });

  it("only enables explicitly implemented setup routes when available", () => {
    const entry = { id: "mcp", availability: "available" } as ConnectorEntry;
    expect(canOpenConnector(entry)).toBe(true);
    expect(canOpenConnector({ ...entry, id: "generic-rest-api" })).toBe(true);
    expect(canOpenConnector({ ...entry, id: "canvas-lms" })).toBe(false);
    expect(canOpenConnector({ ...entry, id: "future-connector" })).toBe(false);
    expect(canOpenConnector({ ...entry, availability: "setup_pending" })).toBe(
      false,
    );
    expect(
      canReconnect({
        ...connection,
        connector_id: "mcp",
        can_reauthorize: true,
      }),
    ).toBe(false);
    expect(
      canReconnect({
        ...connection,
        connector_id: "generic-rest-api",
        can_reauthorize: true,
      }),
    ).toBe(true);
  });

  it("keeps deferred Canvas out of the connection browse catalog", () => {
    const entry = {
      id: "canvas-lms",
      availability: "available",
    } as ConnectorEntry;
    expect(
      visibleConnectorCatalog([entry, { ...entry, id: "google-workspace" }]),
    ).toEqual([{ ...entry, id: "google-workspace" }]);
  });
});

describe("bounded connection pages", () => {
  it("caps the visible list and clamps stale pages after filtering", () => {
    const values = Array.from({ length: 20 }, (_, index) => index);
    expect(connectionPage(values, 1).items).toEqual([6, 7, 8, 9, 10, 11]);
    expect(connectionPage([1], 9)).toEqual({ items: [1], page: 0, pages: 1 });
    expect(connectionPage([], -4)).toEqual({ items: [], page: 0, pages: 1 });
    expect(connectionPage(values, Number.NaN, 0).items).toEqual([0]);
  });
});

describe("explicit OAuth navigation", () => {
  it("allows only Google's authorization endpoint", () => {
    const url = "https://accounts.google.com/o/oauth2/v2/auth?state=opaque";
    expect(googleAuthorizationUrl(url)).toBe(url);
  });

  it.each([
    "javascript:alert(1)",
    "https://accounts.google.com.evil.example/o/oauth2/v2/auth",
    "http://accounts.google.com/o/oauth2/v2/auth",
    "https://user:password@accounts.google.com/o/oauth2/v2/auth",
    "https://accounts.google.com:444/o/oauth2/v2/auth",
    "https://accounts.google.com/unexpected",
    null,
  ])("rejects an unexpected redirect: %s", (url) => {
    expect(() => googleAuthorizationUrl(url)).toThrow();
  });
});
