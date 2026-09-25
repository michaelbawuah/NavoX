import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ManagedConnection } from "../lib/connection-management";
import { ConnectionLifecycleControls } from "./connection-lifecycle-controls";

const connection = {
  id: "connection-1",
  connector_id: "generic-rest-api",
  name: "Example service",
  account_label: "private@example.com",
  health: "CONNECTED",
  agent_paused: false,
  permissions: [],
  sources: [],
  can_pause: true,
  can_resume: false,
  can_reauthorize: false,
  can_disconnect: false,
  can_delete_data: false,
} satisfies ManagedConnection;

describe("connection lifecycle controls", () => {
  it("never enables disconnected operations without explicit server capabilities", () => {
    const markup = renderToStaticMarkup(
      createElement(ConnectionLifecycleControls, {
        connection,
        pending: false,
        onConfirm: vi.fn(),
      }),
    );
    expect(markup).toMatch(/disabled=""[^>]*>Disconnect/);
    expect(markup).toMatch(/disabled=""[^>]*>Delete learned data/);
    expect(markup).toContain("separate step");
    expect(markup).not.toContain("Confirm deletion");
  });

  it("enables deletion only after disconnect when backend permits it", () => {
    const markup = renderToStaticMarkup(
      createElement(ConnectionLifecycleControls, {
        connection: {
          ...connection,
          health: "DISCONNECTED",
          can_disconnect: true,
          can_delete_data: true,
        },
        pending: false,
        onConfirm: vi.fn(),
      }),
    );
    expect(markup).toMatch(/disabled=""[^>]*>Disconnect/);
    expect(markup).toMatch(/<button[^>]*>Delete learned data<\/button>/);
    expect(markup).toContain("Learned data can be removed");
  });
});
