import { afterEach, describe, expect, it, vi } from "vitest";
import {
  isActiveThreadWatch,
  reminderAt,
  safeThreadProviderUrl,
  sortedThreadWatches,
  type ThreadWatch,
  threadWatchReplyTarget,
  threadWatchStatusLabel,
  threadWatchTime,
  watchEligibility,
} from "./thread-watch";

const now = Date.parse("2026-10-17T12:00:00Z");
const account = {
  id: "mailbox-owned",
  status: "active",
  granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
};
const action = {
  id: "action-owned",
  status: "completed",
  verified_at: "2026-10-17T11:00:00Z",
  payload: { connection_id: account.id },
  result: { message_id: "message-owned", thread_id: "thread-owned" },
};
const watch: ThreadWatch = {
  id: "watch-owned",
  action_id: action.id,
  connection_id: account.id,
  commitment_id: "outgoing-commitment",
  compose_commitment_id: "incoming-commitment",
  recipient_email: "maya@example.com",
  sender_email: "owner@example.com",
  subject: "Project outline",
  remind_at: "2026-10-19T12:00:00Z",
  status: "watching",
  reason: null,
  last_checked_at: null,
  replied_at: null,
  next_check_at: "2026-10-17T12:30:00Z",
  provider_url:
    "https://mail.google.com/mail/u/?authuser=owner%40example.com#all/thread-owned",
  created_at: "2026-10-17T12:00:00Z",
  updated_at: "2026-10-17T12:00:00Z",
};

afterEach(() => vi.unstubAllEnvs());

describe("explicit email-watch eligibility", () => {
  it("requires the verified send, actual receipt and its own readable mailbox", () => {
    expect(watchEligibility(action, [account])).toBe("eligible");
    for (const changed of [
      { ...action, status: "executing" },
      { ...action, status: "uncertain" },
      { ...action, verified_at: null },
    ])
      expect(watchEligibility(changed, [account])).toBe("unverified");
    expect(
      watchEligibility({ ...action, result: { message_id: "message-owned" } }, [
        account,
      ]),
    ).toBe("missing_thread");
    expect(
      watchEligibility(action, [{ ...account, id: "other-mailbox" }]),
    ).toBe("needs_access");
    expect(watchEligibility(action, [{ ...account, status: "revoked" }])).toBe(
      "needs_access",
    );
    expect(
      watchEligibility(action, [
        {
          ...account,
          granted_scopes: ["https://www.googleapis.com/auth/gmail.send"],
        },
      ]),
    ).toBe("needs_access");
  });

  it("does not infer a reply target from an outgoing manual message", () => {
    const due = { ...watch, status: "due" as const };
    expect(threadWatchReplyTarget(due, ["incoming-commitment"])).toBe(
      "incoming-commitment",
    );
    expect(threadWatchReplyTarget(due, ["outgoing-commitment"])).toBeNull();
    expect(
      threadWatchReplyTarget({ ...due, compose_commitment_id: null }, [
        "outgoing-commitment",
      ]),
    ).toBeNull();
    expect(threadWatchReplyTarget(watch, ["incoming-commitment"])).toBeNull();
    expect(
      threadWatchReplyTarget({ ...due, status: "needs_access" }, [
        "incoming-commitment",
      ]),
    ).toBeNull();
  });
});

describe("reminder times", () => {
  it("converts an explicit delay to an aware UTC time", () => {
    expect(reminderAt("1", "", now)).toBe("2026-10-18T12:00:00.000Z");
    expect(reminderAt("2", "", now)).toBe("2026-10-19T12:00:00.000Z");
    expect(reminderAt("7", "", now)).toBe("2026-10-24T12:00:00.000Z");
  });

  it("uses the displayed local device time for a custom date", () => {
    vi.stubEnv("TZ", "America/New_York");
    expect(reminderAt("custom", "2026-10-20T15:30", now)).toBe(
      "2026-10-20T19:30:00.000Z",
    );
  });

  it("uses the saved account zone even when the device is elsewhere", () => {
    vi.stubEnv("TZ", "Europe/London");
    expect(
      reminderAt("custom", "2026-10-20T15:30", now, "America/New_York"),
    ).toBe("2026-10-20T19:30:00.000Z");
    expect(
      reminderAt("custom", "2026-10-20T15:30", now, "America/Los_Angeles"),
    ).toBe("2026-10-20T22:30:00.000Z");
    expect(
      threadWatchTime("2026-10-20T19:30:00Z", "America/New_York"),
    ).toContain("3:30 PM");
    expect(
      threadWatchTime("2026-10-20T19:30:00Z", "America/Los_Angeles"),
    ).toContain("12:30 PM");
  });

  it("requires an unambiguous saved-zone clock time across clock changes", () => {
    expect(() =>
      reminderAt(
        "custom",
        "2027-03-14T02:30",
        Date.parse("2027-03-01T12:00:00Z"),
        "America/New_York",
      ),
    ).toThrow("skipped by a clock change");
    expect(() =>
      reminderAt("custom", "2026-11-01T01:30", now, "America/New_York"),
    ).toThrow("happens twice");
  });

  it("rejects missing, impossible, past and out-of-range times", () => {
    for (const custom of [
      "",
      "2026-02-30T10:00",
      "2026-10-16T12:00",
      "2026-12-20T15:30",
    ])
      expect(() => reminderAt("custom", custom, now)).toThrow();
    expect(() => reminderAt("2", "", Number.NaN)).toThrow();
  });

  it("rejects a local time skipped by daylight saving", () => {
    vi.stubEnv("TZ", "America/New_York");
    expect(() =>
      reminderAt(
        "custom",
        "2027-03-14T02:30",
        Date.parse("2027-03-01T12:00:00Z"),
      ),
    ).toThrow("valid local date and time");
  });
});

describe("saved watch presentation", () => {
  it("uses server status even when the reminder time is past", () => {
    const past = { ...watch, remind_at: "2020-01-01T00:00:00Z" };
    expect(threadWatchStatusLabel(past.status)).toBe("Watching for a reply");
    expect(isActiveThreadWatch(past)).toBe(true);
    expect(isActiveThreadWatch({ ...watch, status: "replied" })).toBe(false);
    expect(isActiveThreadWatch({ ...watch, status: "cancelled" })).toBe(false);
  });

  it("surfaces due watches before active watches without mutating the list", () => {
    const due = { ...watch, id: "due", status: "due" as const };
    const failed = { ...watch, id: "failed", status: "check_failed" as const };
    const input = [watch, failed, due];
    expect(sortedThreadWatches(input).map((item) => item.id)).toEqual([
      "due",
      "failed",
      watch.id,
    ]);
    expect(input[0]).toBe(watch);
  });

  it("opens only safe Gmail thread links", () => {
    expect(safeThreadProviderUrl(watch.provider_url)).toBe(watch.provider_url);
    for (const url of [
      "javascript:alert(1)",
      "https://mail.google.com.evil.example/mail/u/0/#all/thread",
      "https://evil.example/#all/thread",
      "http://mail.google.com/mail/u/0/#all/thread",
      "https://owner:secret@mail.google.com/mail/u/0/#all/thread",
      "https://mail.google.com/mail/u/0/?redirect=https://evil.example#all/thread",
      "https://mail.google.com/mail/u/0/#all/thread/extra",
      "https://mail.google.com/mail/u/0/#settings",
    ])
      expect(safeThreadProviderUrl(url)).toBeNull();
  });
});
