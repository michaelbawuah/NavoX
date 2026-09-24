import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ManagedConnection } from "../lib/connection-management";
import { RestCredentialPanel } from "./rest-credential-panel";

const connection = {
  id: "connection-id",
  connector_id: "generic-rest-api",
  name: "Approved service",
  account_label: "Account",
  health: "AUTH_EXPIRED",
  agent_paused: false,
  permissions: [],
  sources: [],
  can_pause: true,
  can_resume: false,
  can_reauthorize: false,
  can_disconnect: true,
  can_delete_data: false,
} satisfies ManagedConnection;

describe("REST credential replacement", () => {
  it("requires the backend reconnect capability and hides the entered token", () => {
    const markup = renderToStaticMarkup(
      createElement(RestCredentialPanel, {
        apiBaseUrl: "/api/v1",
        connection,
        onClose: vi.fn(),
        onReconnected: vi.fn(),
      }),
    );
    expect(markup).toContain('type="password"');
    expect(markup).toMatch(/disabled=""[^>]*>Reconnect service/);
    expect(markup).toContain("A paused connection stays paused");
    expect(markup).toContain(
      "An active connection starts a sync after replacement",
    );
  });
});
