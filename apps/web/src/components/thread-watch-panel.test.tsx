import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ThreadWatch } from "../lib/thread-watch";
import {
  ThreadWatchCard,
  ThreadWatchPanel,
  WatchSentEmail,
} from "./thread-watch-panel";

const watch: ThreadWatch = {
  id: "watch-owned",
  action_id: "action-owned",
  connection_id: "mailbox-owned",
  commitment_id: "request-owned",
  compose_commitment_id: "request-owned",
  recipient_email: "maya@example.com",
  sender_email: "owner@example.com",
  subject: "Project outline",
  remind_at: "2026-10-19T12:00:00Z",
  status: "due",
  reason: null,
  last_checked_at: "2026-10-19T12:01:00Z",
  replied_at: null,
  next_check_at: null,
  provider_url:
    "https://mail.google.com/mail/u/?authuser=owner%40example.com#all/thread-owned",
  created_at: "2026-10-17T12:00:00Z",
  updated_at: "2026-10-19T12:01:00Z",
};
const action = {
  id: watch.action_id,
  status: "completed",
  verified_at: "2026-10-17T11:00:00Z",
  payload: { connection_id: watch.connection_id },
  result: { message_id: "sent-owned", thread_id: "thread-owned" },
};
const connection = {
  id: watch.connection_id,
  status: "active",
  granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
};
afterEach(() => vi.unstubAllGlobals());

describe("email reply-watch interface", () => {
  it("shows a due reminder with the actual last check and an eligible draft route", () => {
    const markup = renderToStaticMarkup(
      createElement(ThreadWatchCard, {
        watch,
        eligibleCommitmentIds: ["request-owned"],
        onDraftReply: vi.fn(),
        onAction: vi.fn(),
      }),
    );
    expect(markup).toContain("No reply at last check");
    expect(markup).toContain('dateTime="2026-10-19T12:01:00Z"');
    expect(markup).toContain("Review Gmail before following up");
    expect(markup).toContain("Draft a reply");
    expect(markup).toContain("Open Gmail thread");
    expect(markup).not.toContain("Send automatically");
  });

  it("does not expose a draft route for a foreign or outgoing-only request", () => {
    for (const changed of [watch, { ...watch, compose_commitment_id: null }]) {
      const markup = renderToStaticMarkup(
        createElement(ThreadWatchCard, {
          watch: changed,
          eligibleCommitmentIds: ["other-request"],
          onDraftReply: vi.fn(),
        }),
      );
      expect(markup).not.toContain("Draft a reply");
    }
  });

  it("keeps failed and unauthorized checks unknown rather than claiming nobody replied", () => {
    for (const status of ["check_failed", "needs_access"] as const) {
      const markup = renderToStaticMarkup(
        createElement(ThreadWatchCard, {
          watch: { ...watch, status },
          onAction: vi.fn(),
        }),
      );
      expect(markup).toContain("Reply status is unknown");
      expect(markup).not.toContain("No reply at last check");
      expect(markup).toContain("Retry check");
    }
  });

  it("does not render an unsafe provider URL", () => {
    const markup = renderToStaticMarkup(
      createElement(ThreadWatchCard, {
        watch: { ...watch, provider_url: "https://evil.example" },
      }),
    );
    expect(markup).not.toContain("Open Gmail thread");
    expect(markup).not.toContain("evil.example");
  });

  it("allows cancelling while globally paused but disables provider checks", () => {
    const markup = renderToStaticMarkup(
      createElement(ThreadWatchCard, {
        watch,
        paused: true,
        onAction: vi.fn(),
      }),
    );
    expect(markup).toMatch(/disabled=""[^>]*>Check now/);
    expect(markup).toMatch(/<button type="button">Pause watch<\/button>/);
    expect(markup).toMatch(/<button type="button">Cancel watch<\/button>/);
  });

  it("removes checks and mutations from finished watches", () => {
    const markup = renderToStaticMarkup(
      createElement(ThreadWatchCard, {
        watch: {
          ...watch,
          status: "replied",
          replied_at: watch.last_checked_at,
        },
        onAction: vi.fn(),
        onReschedule: vi.fn(),
      }),
    );
    expect(markup).toContain("Reply detected");
    expect(markup).toContain("This watch has stopped");
    expect(markup).not.toContain("Check now");
    expect(markup).not.toContain("Cancel watch");
    expect(markup).not.toContain("Change reminder");
  });

  it("does no provider work or watch creation while rendering a saved-list surface", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const markup = renderToStaticMarkup(createElement(ThreadWatchPanel));
    expect(markup).toContain("Loading your saved email watches");
    expect(markup).not.toContain("No active email watches");
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("verified-send watch invitation", () => {
  it("offers nothing for an unverified or uncertain send", () => {
    for (const changed of [
      { ...action, status: "uncertain" },
      { ...action, verified_at: null },
    ]) {
      expect(
        renderToStaticMarkup(
          createElement(WatchSentEmail, {
            action: changed,
            connections: [connection],
          }),
        ),
      ).toBe("");
    }
  });

  it("explains missing read access and thread receipts without starting consent", () => {
    const missingAccess = renderToStaticMarkup(
      createElement(WatchSentEmail, { action, connections: [] }),
    );
    expect(missingAccess).toContain(
      "Enable Gmail read access for this sending account",
    );
    expect(missingAccess).toContain('href="/connections"');
    expect(missingAccess).not.toContain("Watch for a reply</button>");
    const missingThread = renderToStaticMarkup(
      createElement(WatchSentEmail, {
        action: { ...action, result: { message_id: "sent-owned" } },
        connections: [connection],
      }),
    );
    expect(missingThread).toContain(
      "no verified Gmail thread available to watch",
    );
  });

  it("requires an explicit choice and saved-state check before creating a watch", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const markup = renderToStaticMarkup(
      createElement(WatchSentEmail, { action, connections: [connection] }),
    );
    expect(markup).toContain("If there is no reply, remind me");
    expect(markup).toContain("In 1 day");
    expect(markup).toContain("In 2 days");
    expect(markup).toContain("In 7 days");
    expect(markup).toContain("At a date and time");
    expect(markup).toMatch(/disabled=""[^>]*>Watch for a reply/);
    expect(markup).toContain("No follow-up is sent automatically");
    expect(fetch).not.toHaveBeenCalled();
  });
});
