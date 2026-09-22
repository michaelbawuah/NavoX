import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  IntelligenceControls,
  IntelligenceFeedback,
  WorkspaceContext,
} from "./intelligence-controls";

const connection = {
  id: "connection-1",
  status: "active",
  external_email: "person@example.test",
  granted_scopes: ["openid"],
};
const readScopes = [
  "https://www.googleapis.com/auth/gmail.readonly",
  "https://www.googleapis.com/auth/calendar.events.readonly",
];

describe("connected understanding controls", () => {
  it("requires explicit read consent before offering source sync", () => {
    const markup = renderToStaticMarkup(
      createElement(IntelligenceControls, {
        connections: [connection],
        paused: false,
        onRefresh: vi.fn(),
      }),
    );
    expect(markup).toContain("Enable Gmail &amp; Calendar understanding");
    expect(markup).not.toContain("Sync Gmail");
    expect(markup).not.toContain("Sync Calendar");
    expect(markup).toContain("Email sending stays a separate");
  });

  it("keeps sync disabled when a previously authorized connection is unhealthy", () => {
    const markup = renderToStaticMarkup(
      createElement(IntelligenceControls, {
        connections: [
          {
            ...connection,
            status: "reauthorization_required",
            granted_scopes: readScopes,
          },
        ],
        paused: false,
        onRefresh: vi.fn(),
      }),
    );
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>Sync Gmail<\/button>/);
    expect(markup).toMatch(
      /<button[^>]*disabled=""[^>]*>Sync Calendar<\/button>/,
    );
  });

  it("honors agent pause for both sources", () => {
    const markup = renderToStaticMarkup(
      createElement(IntelligenceControls, {
        connections: [{ ...connection, granted_scopes: readScopes }],
        paused: true,
        onRefresh: vi.fn(),
      }),
    );
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>Sync Gmail<\/button>/);
    expect(markup).toMatch(
      /<button[^>]*disabled=""[^>]*>Sync Calendar<\/button>/,
    );
  });

  it("escapes external account text and makes no provider request on server render", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    try {
      const markup = renderToStaticMarkup(
        createElement(IntelligenceControls, {
          connections: [
            { ...connection, external_email: '<img src=x onerror="alert(1)">' },
          ],
          paused: false,
          onRefresh: vi.fn(),
        }),
      );
      expect(markup).toContain("&lt;img");
      expect(markup).not.toContain("<img");
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      fetchMock.mockRestore();
    }
  });

  it("offers bounded preference feedback without an approval action", () => {
    const markup = renderToStaticMarkup(
      createElement(IntelligenceFeedback, {
        commitmentId: "commitment-1",
        onRefresh: vi.fn(),
      }),
    );
    expect(markup).toContain("Your feedback never changes permissions");
    expect(markup).toContain("Incorrect");
    expect(markup).toContain("Too frequent");
    expect(markup).not.toContain("Approve");
  });

  it("keeps weather hidden until stored opt-in preferences are loaded", () => {
    const markup = renderToStaticMarkup(
      createElement(WorkspaceContext, {
        timezone: "America/New_York",
        onTimezoneChange: vi.fn(),
      }),
    );
    expect(markup).toContain("Loading local time");
    expect(markup).not.toContain("Weather unavailable");
    expect(markup).not.toContain("°");
  });
});
