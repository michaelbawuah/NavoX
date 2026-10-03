import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  TodayWorkspace,
  type TodayWorkspaceView,
  workspaceItemGroups,
} from "./today-workspace";

const account = {
  id: "owner-1",
  email: "owner@example.test",
  display_name: "Michael Awuah",
  timezone: "America/New_York",
  workspace: {
    id: "workspace-1",
    name: "Personal",
    workspace_type: "personal",
  },
};

function renderView(view: TodayWorkspaceView = "today") {
  return renderToStaticMarkup(
    createElement(TodayWorkspace, {
      view,
      account,
      connections: [],
      message: "",
      onConnectionsChanged: vi.fn(async () => {}),
      onSignOut: vi.fn(async () => {}),
    }),
  );
}

describe("workspace destinations", () => {
  it("does not advertise an empty connection list when its state is unavailable", () => {
    const markup = renderToStaticMarkup(
      createElement(TodayWorkspace, {
        account,
        connections: [],
        connectionsUnavailable: true,
        message: "Connected apps could not be loaded.",
        onConnectionsChanged: vi.fn(async () => {}),
        onSignOut: vi.fn(async () => {}),
      }),
    );
    expect(markup).toContain("Connected apps could not be loaded");
    expect(markup).not.toContain("Connect your apps");
    expect(markup).toContain("Upcoming agenda");
  });
  it("keeps the daily agenda visible and treats loading as unconfirmed data", () => {
    const markup = renderView();
    expect(markup).toContain("Upcoming agenda");
    expect(markup).toContain("Loading your agenda");
    expect(markup).toContain('aria-busy="true"');
    expect(markup).not.toContain("You’re caught up");
    expect(markup).toContain('href="/connections"');
    expect(markup).toContain('href="/planner"');
    expect(markup.match(/<main\b/g)).toHaveLength(1);
    expect(markup).not.toContain("Generate draft");
    expect(markup).not.toContain("Send with Gmail");
  });

  it("puts draft and approval controls in the Inbox without a hidden email-actions accordion", () => {
    const markup = renderView("inbox");
    expect(markup).toContain('id="drafts"');
    expect(markup).toContain('id="approvals"');
    expect(markup).toContain("Generate draft");
    expect(markup).toContain("Send with Gmail");
    expect(markup).toContain("Needs reply");
    expect(markup).toContain("Follow-ups");
    expect(markup).not.toContain("<summary>Email actions</summary>");
    expect(markup).not.toContain("Approve exact send");
  });

  it("offers task capture directly in the Planner and preserves all commitment types", () => {
    const markup = renderView("planner");
    expect(markup).toContain("Tasks &amp; events");
    expect(markup).toContain('id="capture-heading"');
    expect(markup).toContain("What needs to happen?");
    for (const type of [
      "task",
      "deadline",
      "meeting",
      "follow_up",
      "promise",
      "renewal",
    ]) {
      expect(markup).toContain(`<option value="${type}"`);
    }
    expect(markup).not.toContain("Generate draft");
    expect(markup).not.toContain("Send with Gmail");
  });

  it("retains connection and preference controls at their own destinations without fetching on server render", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    try {
      const connections = renderView("connections");
      const settings = renderView("settings");
      expect(connections).toContain("Connected apps");
      expect(connections).toContain("Bring your work into focus");
      expect(settings).toContain("AI preferences");
      expect(settings).toContain("Local time and weather");
      expect(settings).toContain("Pause assistance");
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      fetchMock.mockRestore();
    }
  });
});

describe("real Today source routing", () => {
  const gmailSource = {
    provider: "google",
    source_type: "gmail_message",
    external_resource_id: "message-1",
    connection_id: "google-account-1",
    evidence_id: "evidence-1",
  };
  const calendarSource = {
    provider: "google",
    source_type: "calendar_event",
    external_resource_id: "event-1",
    connection_id: "google-account-1",
  };

  it("routes a Gmail request to Inbox and keeps it out of Planner", () => {
    const items = [
      { id: "reply-request", type: "task", sources: [gmailSource] },
      { id: "manual-task", type: "task", sources: [] },
      { id: "calendar-meeting", type: "meeting", sources: [calendarSource] },
    ];
    const groups = workspaceItemGroups(items);
    expect(groups.inbox.map((item) => item.id)).toEqual(["reply-request"]);
    expect(groups.planner.map((item) => item.id)).toEqual([
      "manual-task",
      "calendar-meeting",
    ]);
  });

  it("keeps email-derived meetings and deadlines in Planner while excluding renewals", () => {
    const items = [
      { id: "email-meeting", type: "meeting", sources: [gmailSource] },
      { id: "email-deadline", type: "deadline", sources: [gmailSource] },
      { id: "email-follow-up", type: "follow_up", sources: [gmailSource] },
      { id: "renewal", type: "renewal", sources: [] },
    ];
    const groups = workspaceItemGroups(items);
    expect(groups.inbox.map((item) => item.id)).toEqual([
      "email-meeting",
      "email-deadline",
      "email-follow-up",
    ]);
    expect(groups.planner.map((item) => item.id)).toEqual([
      "email-meeting",
      "email-deadline",
    ]);
  });
});
