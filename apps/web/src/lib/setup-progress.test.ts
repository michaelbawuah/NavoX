import { afterEach, describe, expect, it, vi } from "vitest";
import type { ManagedConnection } from "./connection-management";
import {
  googleSetupAvailable,
  type SetupConnection,
  setupProgress,
  startGoogleSetup,
} from "./setup-progress";

const gmailScope = "https://www.googleapis.com/auth/gmail.readonly";
const identity: SetupConnection = {
  id: "google-1",
  status: "active",
  external_email: "person@example.test",
  granted_scopes: ["openid", "email"],
};
const readable = { ...identity, granted_scopes: [gmailScope] };
const options = {
  connections: [readable],
  paused: false,
  processingReady: true,
  itemCount: 0,
};
const managed: ManagedConnection = {
  id: identity.id,
  connector_id: "google-workspace",
  name: "Google",
  account_label: identity.external_email,
  health: "CONNECTED",
  agent_paused: false,
  permissions: [],
  sources: [
    {
      id: "gmail",
      name: "Gmail",
      authorized: true,
      health: "CONNECTED",
      last_synced_at: null,
      freshness: "never_synced",
      syncing: false,
      retry_at: null,
      can_sync: true,
    },
  ],
  can_pause: true,
  can_resume: false,
  can_reauthorize: true,
  can_disconnect: true,
  can_delete_data: false,
};

function sync(
  status: "queued" | "running" | "completed" | "failed" | "unavailable",
  connectionId = identity.id,
) {
  return {
    job: {
      connectionId,
      source: "gmail" as const,
      progress: {
        workflow_id: "sync-1",
        status,
        commitment_count: status === "completed" ? 0 : null,
        error: null,
      },
    },
  };
}

describe("first-use setup progress", () => {
  it("keeps linking identity separate from reading and processing", () => {
    expect(setupProgress({ ...options, connections: [] }).state).toBe(
      "connect",
    );
    const consent = setupProgress({ ...options, connections: [identity] });
    expect(consent.state).toBe("permission");
    expect(consent.hasReadAccess).toBe(false);
    const readyToSync = setupProgress(options);
    expect(readyToSync.state).toBe("not_started");
    expect(readyToSync.hasCompletedSync).toBe(false);
  });

  it("cannot mistake a connection fetch failure for an unconnected account", () => {
    const result = setupProgress({
      ...options,
      connections: [],
      connectionsUnavailable: true,
    });
    expect(result.state).toBe("connection_unavailable");
    expect(result.message).toContain("Retry before changing access");
  });

  it("requires a healthy account before using saved scopes", () => {
    expect(
      setupProgress({
        ...options,
        connections: [{ ...readable, status: "reauthorization_required" }],
      }).state,
    ).toBe("reconnect");
    expect(setupProgress({ ...options, paused: true }).state).toBe("paused");
    expect(setupProgress({ ...options, processingReady: null }).state).toBe(
      "checking",
    );
    expect(setupProgress({ ...options, processingReady: false }).state).toBe(
      "unavailable",
    );
    expect(
      setupProgress({
        ...options,
        processingReady: false,
        readinessMessage:
          "App syncing is not available for this account right now. You can still add a task yourself.",
      }).message,
    ).toContain("not available for this account");
  });

  it("shows an acknowledged queued sync before a worker updates saved connection status", () => {
    expect(
      setupProgress({
        ...options,
        syncs: sync("queued"),
        managedConnections: [managed],
      }).state,
    ).toBe("syncing");
    expect(
      setupProgress({
        ...options,
        managedConnections: [
          { ...managed, sources: [{ ...managed.sources[0], syncing: true }] },
        ],
      }).state,
    ).toBe("syncing");
  });

  it("does not offer unknown or failed syncs as completed", () => {
    expect(
      setupProgress({ ...options, syncs: sync("unavailable") }).state,
    ).toBe("unknown");
    const failure = setupProgress({
      ...options,
      syncs: sync("failed"),
      itemCount: 3,
    });
    expect(failure.state).toBe("failed");
    expect(failure.hasCompletedSync).toBe(false);
  });

  it("finishes first use after a successful sync with zero extracted items", () => {
    const result = setupProgress({ ...options, syncs: sync("completed") });
    expect(result.state).toBe("empty");
    expect(result.stage).toBe("review");
    expect(result.hasCompletedSync).toBe(true);
    expect(result.message).toContain("No actionable priorities");
  });

  it("recognizes a saved completed sync after the component reloads", () => {
    const result = setupProgress({
      ...options,
      managedConnections: [
        {
          ...managed,
          sources: [
            {
              ...managed.sources[0],
              last_synced_at: "2026-10-17T05:00:00Z",
              freshness: "fresh",
            },
          ],
        },
      ],
    });
    expect(result.state).toBe("empty");
    expect(result.hasCompletedSync).toBe(true);
  });

  it("offers real saved priorities without claiming an unsynced source was processed", () => {
    const result = setupProgress({ ...options, itemCount: 2 });
    expect(result.state).toBe("ready");
    expect(result.hasCompletedSync).toBe(false);
    expect(result.message).toContain("Review its source");
  });

  it("ignores old readouts from a different or disconnected account", () => {
    const other = {
      ...managed,
      id: "other-account",
      sources: [
        { ...managed.sources[0], last_synced_at: "2026-10-17T05:00:00Z" },
      ],
    };
    const result = setupProgress({
      ...options,
      managedConnections: [other],
      syncs: sync("completed", "other-account"),
    });
    expect(result.state).toBe("not_started");
    expect(result.hasCompletedSync).toBe(false);
  });
});

describe("explicit Google setup", () => {
  afterEach(() => vi.restoreAllMocks());

  it("requires an available Google catalog entry", () => {
    const entry = {
      id: "google-workspace",
      name: "Google",
      category: "apps",
      description: "Google",
      availability: "setup_pending" as const,
      setup_label: "Connect",
      authentication: "oauth",
      read_capabilities: [],
    };
    expect(googleSetupAvailable([entry])).toBe(false);
    expect(
      googleSetupAvailable([{ ...entry, availability: "available" }]),
    ).toBe(true);
  });

  it("only starts the identity connection flow and validates Google's redirect", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          authorization_url:
            "https://accounts.google.com/o/oauth2/v2/auth?client_id=example",
        }),
        { status: 200 },
      ),
    );
    expect(await startGoogleSetup("/api/v1")).toContain(
      "https://accounts.google.com/o/oauth2/v2/auth",
    );
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/connectors/google-workspace/connect");
    expect(init).toMatchObject({ method: "POST", credentials: "include" });
    expect(init?.body).not.toContain("gmail");
    expect(init?.body).not.toContain("send");
  });

  it("rejects an unexpected redirect even after a successful response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          authorization_url:
            "https://accounts.google.com.evil.test/o/oauth2/v2/auth",
        }),
        { status: 200 },
      ),
    );
    await expect(startGoogleSetup("/api/v1")).rejects.toThrow(
      "Unexpected authorization destination",
    );
  });
});
