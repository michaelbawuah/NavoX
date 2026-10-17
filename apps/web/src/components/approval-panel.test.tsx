import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ApprovalPanel } from "./approval-panel";

const props = {
  agentPaused: false,
  commitments: [
    { id: "owned-request", title: "Reply to Maya", status: "active" },
  ],
  connections: [
    {
      id: "owned-mailbox",
      provider: "google" as const,
      external_email: "owner@example.com",
      status: "active",
      granted_scopes: ["https://www.googleapis.com/auth/gmail.send"],
      last_checked_at: null,
      last_error: null,
    },
  ],
  onStateChanged: vi.fn(),
};

describe("email approvals without a duplicate composer", () => {
  it("keeps the original composer available to existing callers", () => {
    const markup = renderToStaticMarkup(createElement(ApprovalPanel, props));
    expect(markup).toContain("Send with Gmail");
    expect(markup).toContain("<form");
    expect(markup).toContain("Prepare for approval");
  });

  it("shows the review destination instead of another email form in Inbox", () => {
    const markup = renderToStaticMarkup(
      createElement(ApprovalPanel, { ...props, showComposer: false }),
    );
    expect(markup).toContain("Email approvals");
    expect(markup).toContain(
      "Prepared emails and their send status appear here.",
    );
    expect(markup).not.toContain("<form");
    expect(markup).not.toContain("Prepare for approval");
    expect(markup).not.toContain("<textarea");
    expect(markup).not.toContain("Approve exact send");
  });

  it("retains the Gmail permission gate when sending has not been granted", () => {
    const markup = renderToStaticMarkup(
      createElement(ApprovalPanel, {
        ...props,
        showComposer: false,
        connections: [
          {
            ...props.connections[0],
            granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
          },
        ],
      }),
    );
    expect(markup).toContain("Gmail sending is off.");
    expect(markup).toContain("Enable Gmail sending");
    expect(markup).not.toContain("<form");
  });
});
