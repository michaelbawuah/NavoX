import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GuidedSetup } from "./guided-setup";

const props = {
  connections: [],
  paused: false,
  itemCount: 0,
  onConnectionsChanged: vi.fn(),
  onRefresh: vi.fn(),
};

describe("guided first use", () => {
  afterEach(() => vi.restoreAllMocks());

  it("offers a real task route without starting OAuth or sync on render", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    const markup = renderToStaticMarkup(createElement(GuidedSetup, props));
    expect(markup).toContain("Connect an account");
    expect(markup).toContain("Choose access &amp; sync");
    expect(markup).toContain('href="/planner#capture-heading"');
    expect(markup).toContain("Sending email needs separate permission");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not present missing connection data as a new account", () => {
    const markup = renderToStaticMarkup(
      createElement(GuidedSetup, { ...props, connectionsUnavailable: true }),
    );
    expect(markup).toContain("Retry connection status");
    expect(markup).not.toContain(">Connect Google</button>");
  });

  it("keeps consent explicit after identity-only linking", () => {
    const markup = renderToStaticMarkup(
      createElement(GuidedSetup, {
        ...props,
        connections: [
          {
            id: "google-1",
            status: "active",
            external_email: "owner@example.test",
            granted_scopes: ["openid"],
          },
        ],
      }),
    );
    expect(markup).toContain(
      "Reading Gmail and Calendar requires a separate choice",
    );
    expect(markup).toContain("Choose Gmail &amp; Calendar read access");
    expect(markup).not.toContain("Sync Gmail");
  });
  it("keeps first sync disabled until account readiness has been checked", () => {
    const markup = renderToStaticMarkup(
      createElement(GuidedSetup, {
        ...props,
        connections: [
          {
            id: "google-1",
            status: "active",
            external_email: "owner@example.test",
            granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
          },
        ],
      }),
    );
    expect(markup).toContain("Checking whether sync is available");
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>Sync Gmail<\/button>/);
    expect(markup).toContain("Add my own task");
  });
});
