import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { DailyFocusItem, DailyFocusPayload } from "../lib/daily-focus";
import { DailyFocus } from "./daily-focus";

const now = Date.parse("2026-10-17T12:00:00Z");
const task: DailyFocusItem = {
  id: "task-1",
  type: "task",
  title: "Finish the lab",
  description: null,
  status: "confirmed",
  score: 90,
  priority: 4,
  due_at: "2026-10-17T15:00:00Z",
  created_by: "user",
  sources: [],
};
const base: DailyFocusPayload = {
  generated_at: new Date(now).toISOString(),
  timezone: "America/New_York",
  needs_attention: [task],
  coming_up: [],
  renewals: [],
  waiting_on: [],
  completed_recently: [],
};

function render(
  values: {
    payload?: DailyFocusPayload | null;
    loading?: boolean;
    error?: string;
    paused?: boolean;
    busyItemId?: string | null;
  } = {},
) {
  return renderToStaticMarkup(
    createElement(DailyFocus, {
      payload: base,
      loading: false,
      paused: false,
      now,
      onPrepare: vi.fn(),
      onReview: vi.fn(),
      onRefresh: vi.fn(),
      ...values,
    }),
  );
}

describe("daily focus presentation", () => {
  it("shows actual task/date context and one primary action without making requests", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    try {
      const markup = render();
      expect(markup).toContain("Finish the lab");
      expect(markup).toContain("You added this");
      expect(markup).toContain("Due");
      expect(markup).toContain("11:00");
      expect(markup).toContain("Plan next step");
      expect(markup).toContain('dateTime="2026-10-17T12:00:00.000Z"');
      expect(markup).not.toContain("Mark complete");
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      fetchMock.mockRestore();
    }
  });

  it("renders distinct email, suggestion and renewal CTAs with their source count", () => {
    const gmail = {
      provider: "google",
      source_type: "gmail_message",
      external_resource_id: "message-1",
      connection_id: "account-1",
      evidence_id: "evidence-1",
    };
    const markup = render({
      payload: {
        ...base,
        needs_attention: [
          { ...task, id: "email", title: "Reply to Alex", sources: [gmail] },
          {
            ...task,
            id: "candidate",
            title: "Review a request",
            status: "candidate",
            sources: [gmail],
          },
          { ...task, id: "renewal", title: "Review trial", type: "renewal" },
        ],
      },
    });
    expect(markup).toContain("Draft reply");
    expect(markup).toContain("Review suggestion");
    expect(markup).toContain('href="/subscriptions"');
    expect(markup).toContain("Review renewal");
    expect(markup).toContain("View 1 source");
    expect(markup).toContain("Gmail");
  });

  it("does not claim the user is caught up during loading or a failed first load", () => {
    const loading = render({ payload: null, loading: true });
    expect(loading).toContain('aria-busy="true"');
    expect(loading).toContain("Finding your saved priorities");
    expect(loading).not.toContain("Nothing needs a next step");
    const failed = render({ payload: null, error: "network" });
    expect(failed).toContain('role="alert"');
    expect(failed).toContain("could not be loaded");
    expect(failed).not.toContain("Nothing needs a next step");
  });

  it("offers a truthful zero-priority state and recorded completion count", () => {
    const markup = render({
      payload: {
        ...base,
        needs_attention: [],
        completed_recently: [{ ...task, status: "completed" }],
      },
    });
    expect(markup).toContain("Nothing needs a next step here");
    expect(markup).toContain('href="/planner"');
    expect(markup).toContain("1 item marked complete recently");
    expect(markup).not.toContain("NavoX completed");
    expect(markup).not.toContain("Finish the lab");
  });

  it.each([
    {
      name: "paused",
      values: { paused: true },
      notice: "Assistance is paused",
    },
    { name: "busy", values: { busyItemId: task.id }, notice: "Preparing…" },
    {
      name: "failed refresh",
      values: { error: "network" },
      notice: "last saved items",
    },
    {
      name: "stale",
      values: { payload: { ...base, generated_at: "2026-10-17T10:00:00Z" } },
      notice: "more than 15 minutes old",
    },
    {
      name: "invalid time",
      values: {
        payload: { ...base, generated_at: "bad-date", timezone: "bad-zone" },
      },
      notice: "could not be verified",
    },
  ])(
    "disables preparation for $name while preserving the last saved facts",
    ({ values, notice }) => {
      const markup = render(values);
      expect(markup).toContain("Finish the lab");
      expect(markup).toContain(notice);
      expect(markup).toMatch(
        /<button[^>]*disabled=""[^>]*aria-label="Plan next step: Finish the lab"/,
      );
    },
  );
});
