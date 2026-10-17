import { describe, expect, it } from "vitest";
import {
  canPrepareDailyReply,
  DAILY_FOCUS_STALE_AFTER_MS,
  type DailyFocusItem,
  type DailyFocusPayload,
  dailyFocusAction,
  dailyFocusCompletedCount,
  dailyFocusDate,
  dailyFocusFreshness,
  dailyFocusSourceContext,
  selectDailyFocus,
} from "./daily-focus";

const now = Date.parse("2026-10-17T12:00:00Z");
const gmail = {
  provider: "google",
  source_type: "gmail_message",
  external_resource_id: "message-1",
  connection_id: "google-account-1",
  evidence_id: "evidence-1",
};

function item(
  id: string,
  values: Partial<DailyFocusItem> = {},
): DailyFocusItem {
  return {
    id,
    type: "task",
    title: id,
    description: null,
    status: "confirmed",
    priority: 3,
    score: 50,
    due_at: null,
    created_by: "user",
    sources: [],
    ...values,
  };
}

function payload(values: Partial<DailyFocusPayload> = {}): DailyFocusPayload {
  return {
    generated_at: new Date(now).toISOString(),
    timezone: "America/New_York",
    needs_attention: [],
    coming_up: [],
    waiting_on: [],
    completed_recently: [],
    renewals: [],
    set_aside: [],
    ...values,
  };
}

describe("daily focus selection", () => {
  it("keeps at most three distinct items in the server's score/date/title order", () => {
    const renewal = item("renewal", { type: "renewal", score: 80 });
    const first = item("first", { score: 100, priority: 1 });
    const second = item("second", {
      score: 90,
      due_at: "2026-10-17T10:00:00Z",
    });
    const third = item("third", { score: 90, due_at: "2026-10-17T11:00:00Z" });
    const data = payload({
      needs_attention: [renewal, first],
      coming_up: [third, second],
      renewals: [renewal],
    });
    expect(selectDailyFocus(data).map((value) => value.id)).toEqual([
      "first",
      "second",
      "third",
    ]);
    expect(data.needs_attention).toEqual([renewal, first]);
  });

  it("lets completed, cancelled, and dismissed duplicates win over an active copy", () => {
    const done = item("done");
    const cancelled = item("cancelled");
    const dismissed = item("dismissed");
    expect(
      selectDailyFocus(
        payload({
          needs_attention: [done, cancelled, dismissed, item("remaining")],
          coming_up: [
            { ...cancelled, status: "cancelled" },
            { ...dismissed, status: "dismissed" },
          ],
          completed_recently: [{ ...done, status: "completed" }],
        }),
      ).map((value) => value.id),
    ).toEqual(["remaining"]);
  });

  it("excludes waiting and set-aside items even when they also appear in attention", () => {
    const waiting = item("waiting", { status: "waiting_on_external" });
    const hidden = item("set-aside", { status: "candidate" });
    expect(
      selectDailyFocus(
        payload({
          needs_attention: [
            { ...waiting, status: "confirmed" },
            hidden,
            item("snoozed", { status: "waiting" }),
            item("unknown", { status: "unexpected" }),
          ],
          waiting_on: [waiting],
          set_aside: [hidden],
        }),
      ),
    ).toEqual([]);
  });

  it("uses valid dates ahead of invalid or missing dates without inventing urgency", () => {
    expect(
      selectDailyFocus(
        payload({
          coming_up: [
            item("z invalid", { due_at: "not-a-date" }),
            item("a missing"),
            item("valid", { due_at: "2026-10-20T12:00:00Z" }),
          ],
        }),
      ).map((value) => value.id),
    ).toEqual(["valid", "a missing", "z invalid"]);
  });

  it("treats non-finite scores safely and preserves real zero-work states", () => {
    expect(selectDailyFocus(null)).toEqual([]);
    expect(selectDailyFocus(payload())).toEqual([]);
    expect(
      selectDailyFocus(
        payload({
          needs_attention: [
            item("invalid", { score: Number.NaN }),
            item("real"),
          ],
        }),
      ).map((value) => value.id),
    ).toEqual(["real", "invalid"]);
  });
});

describe("daily focus actions and provenance", () => {
  it("requires review before preparing candidate emails and routes renewals to review", () => {
    expect(
      dailyFocusAction(
        item("candidate", { status: "candidate", sources: [gmail] }),
      ),
    ).toBe("review");
    expect(
      dailyFocusAction(item("renewal", { type: "renewal", sources: [gmail] })),
    ).toBe("renewal");
    expect(dailyFocusAction(item("email", { sources: [gmail] }))).toBe("draft");
    expect(dailyFocusAction(item("task"))).toBe("plan");
  });

  it("does not promise a Gmail reply for generic email knowledge or absent source IDs", () => {
    expect(
      canPrepareDailyReply(
        item("knowledge", { sources: [{ ...gmail, source_type: "EMAIL" }] }),
      ),
    ).toBe(false);
    expect(
      canPrepareDailyReply(
        item("missing", {
          sources: [{ ...gmail, external_resource_id: null }],
        }),
      ),
    ).toBe(false);
    expect(
      dailyFocusAction(item("email", { sources: [gmail] }), () => false),
    ).toBe("plan");
  });

  it("counts source records per connected account rather than repeated evidence passages", () => {
    expect(
      dailyFocusSourceContext(
        item("email", {
          sources: [
            gmail,
            { ...gmail, evidence_id: "evidence-2" },
            { ...gmail, connection_id: "other-account" },
          ],
        }),
      ),
    ).toEqual({ count: 2, label: "Gmail" });
    expect(dailyFocusSourceContext(item("manual"))).toEqual({
      count: 0,
      label: "You added this",
    });
  });

  it("counts only distinct recorded completions without crediting the assistant", () => {
    const done = item("done", { status: "completed" });
    expect(
      dailyFocusCompletedCount(
        payload({
          completed_recently: [
            done,
            done,
            item("cancelled", { status: "cancelled" }),
          ],
        }),
      ),
    ).toBe(1);
    expect(dailyFocusCompletedCount(null)).toBe(0);
  });
});

describe("daily focus update times", () => {
  it("detects stale or unverifiable updates instead of treating cached data as current", () => {
    expect(
      dailyFocusFreshness(
        new Date(now - DAILY_FOCUS_STALE_AFTER_MS).toISOString(),
        now,
      ),
    ).toBe("current");
    expect(
      dailyFocusFreshness(
        new Date(now - DAILY_FOCUS_STALE_AFTER_MS - 1).toISOString(),
        now,
      ),
    ).toBe("stale");
    expect(dailyFocusFreshness("bad-date", now)).toBe("unknown");
    expect(
      dailyFocusFreshness(new Date(now + 6 * 60_000).toISOString(), now),
    ).toBe("unknown");
    expect(dailyFocusFreshness(new Date(now).toISOString(), Number.NaN)).toBe(
      "unknown",
    );
  });

  it("renders real dates in the supplied timezone and safely falls back to UTC", () => {
    const date = "2026-10-17T12:00:00Z";
    expect(dailyFocusDate(date, "America/New_York")).toMatch(/8:00/);
    expect(dailyFocusDate(date, "invalid/timezone")).toMatch(/12:00.*UTC/);
    expect(dailyFocusDate("not-a-date", "invalid/timezone")).toBeNull();
    expect(dailyFocusDate(null, "UTC")).toBeNull();
  });
});
