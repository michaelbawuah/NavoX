import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { productFeatures } from "../lib/product-features";
import {
  TodayWorkspace,
  type TodayWorkspaceView,
  workspaceCanDraftReply,
  workspaceItemGroups,
} from "./today-workspace";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

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

function renderView(view: TodayWorkspaceView = "today", connected = false) {
  return renderToStaticMarkup(
    createElement(TodayWorkspace, {
      view,
      account,
      connections: connected
        ? [
            {
              id: "google-1",
              provider: "google",
              external_email: "student@example.test",
              status: "active",
              granted_scopes: [
                "https://www.googleapis.com/auth/gmail.readonly",
              ],
              last_checked_at: null,
              last_error: null,
            },
          ]
        : [],
      message: "",
      onConnectionsChanged: vi.fn(async () => {}),
      onSignOut: vi.fn(async () => {}),
    }),
  );
}

describe("Home Vox feature entry", () => {
  const originalVox = productFeatures.studentVox;
  afterEach(() => {
    productFeatures.studentVox = originalVox;
  });
  it.each([false, true])(
    "leaves Vox ownership at the root; enabled=%s",
    (enabled) => {
      productFeatures.studentVox = enabled;
      const fetch = vi.spyOn(globalThis, "fetch");
      const markup = renderView();
      expect(markup).not.toContain('data-vox-launcher="home"');
      expect(markup).not.toContain("<dialog");
      expect(markup).toContain("Loading your day");
      expect(fetch).not.toHaveBeenCalled();
      fetch.mockRestore();
    },
  );
});

describe("workspace destinations", () => {
  it("waits for mailbox status before offering setup or a draft form", () => {
    const markup = renderView("inbox");
    expect(markup).toContain("Checking your email connections");
    expect(markup).not.toContain("Never miss an email that needs you");
    expect(markup).not.toContain("Connect Gmail");
    expect(markup).not.toContain('id="drafts"');
    expect(markup).not.toContain("Generate draft");
    expect(markup).not.toContain("Refresh");
  });

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
    expect(markup).toContain("Some of your day couldn’t load.");
    expect(markup).toContain("Try again");
    expect(markup).toContain('role="alert"');
    expect(markup).not.toContain("Connect your apps");
    expect(markup).not.toContain("Upcoming agenda");
  });
  it("does not fabricate empty sections or completion while Today is loading", () => {
    const markup = renderView();
    expect(markup).toContain("Loading your day");
    expect(markup).toContain('role="status"');
    for (const removed of [
      "Upcoming renewals",
      "Your daily focus",
      "Refresh",
      "Done for today",
      "Let’s set up NavoX",
    ])
      expect(markup).not.toContain(removed);
    expect(markup).toContain('href="/connections"');
    expect(markup).toContain('href="/planner"');
    expect(markup.match(/<main\b/g)).toHaveLength(1);
  });

  it("puts draft and approval controls in the Inbox without a hidden email-actions accordion", () => {
    const markup = renderView("inbox", true);
    expect(markup).toContain('id="drafts"');
    expect(markup).toContain('id="approvals"');
    expect(markup).toContain("Generate draft");
    expect(markup).toContain("Email approvals");
    expect(markup).not.toContain("Send with Gmail");
    expect(markup).toContain("Needs reply");
    expect(markup).toContain("Waiting for reply");
    expect(markup).not.toContain("<summary>Email actions</summary>");
    expect(markup).not.toContain("Approve exact send");
  });

  it("offers task capture directly in the Planner and preserves all commitment types", () => {
    const markup = renderView("planner");
    expect(markup).toContain("Tasks &amp; events");
    expect(markup).toContain('id="capture-heading"');
    expect(markup).toContain('id="add-task"');
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
      expect(connections).toContain("Your connections. Your control.");
      expect(connections).toContain("Bring your work into focus");
      expect(settings).toContain("AI preferences");
      expect(settings).not.toContain("Local time and weather");
      expect(settings).not.toContain("Pause assistance");
      expect(settings).toContain("Settings sections");
      expect(settings).toContain("What NavoX knows about me");
      expect(settings).toContain("Voice &amp; Noa");
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

describe("workspace reply entry", () => {
  const item = {
    type: "task",
    status: "confirmed",
    sources: [
      {
        provider: "google",
        source_type: "gmail_message",
        external_resource_id: "message-1",
        connection_id: "mailbox-1",
      },
    ],
  };
  const connection = {
    id: "mailbox-1",
    provider: "google" as const,
    external_email: "owner@example.test",
    status: "active",
    granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
    last_checked_at: null,
    last_error: null,
  };
  it("opens drafting only for the current readable source mailbox", () => {
    expect(workspaceCanDraftReply(item, [connection])).toBe(true);
    expect(
      workspaceCanDraftReply(item, [{ ...connection, id: "another-mailbox" }]),
    ).toBe(false);
    expect(
      workspaceCanDraftReply(item, [{ ...connection, status: "revoked" }]),
    ).toBe(false);
    expect(
      workspaceCanDraftReply(item, [{ ...connection, granted_scopes: [] }]),
    ).toBe(false);
    expect(
      workspaceCanDraftReply(
        {
          ...item,
          sources: [{ ...item.sources[0], external_resource_id: " " }],
        },
        [connection],
      ),
    ).toBe(false);
  });
  it("keeps suggestions, finished requests, and calendar work on their review path", () => {
    for (const status of ["candidate", "completed", "cancelled", "dismissed"])
      expect(workspaceCanDraftReply({ ...item, status }, [connection])).toBe(
        false,
      );
    for (const type of ["meeting", "deadline", "renewal"])
      expect(workspaceCanDraftReply({ ...item, type }, [connection])).toBe(
        false,
      );
  });
});
