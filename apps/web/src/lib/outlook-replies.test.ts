import { expect, it } from "vitest";
import { readOutlookDraft } from "./outlook";
import { outlookDraftGroups } from "./outlook-replies";

const id = "11111111-1111-4111-8111-111111111111";
const base = {
  id,
  revision: 1,
  payload_hash: "a".repeat(64),
  connection_id: id,
  source_message_id: "source",
  sender: "owner@example.com",
  to: "teacher@example.com",
  subject: "Reply",
  body: "Thanks",
  status: "completed",
  expires_at: null,
  verified_at: "2026-10-24T10:01:00Z",
};

it("treats older receipts with no reply metadata as unchecked, separate from Waiting", () => {
  const old = readOutlookDraft(base);
  const grouped = outlookDraftGroups([
    old,
    readOutlookDraft({ ...base, status: "uncertain", verified_at: null }),
  ]);
  expect(grouped.unchecked).toHaveLength(1);
  expect(grouped.pending).toHaveLength(1);
  expect(grouped.waiting).toHaveLength(0);
  expect(grouped.replied).toHaveLength(0);
  expect(grouped.sentSources.has("source")).toBe(true);
});

it("rejects Waiting without a complete check and replies without a strictly later receipt", () => {
  const waiting = {
    ...base,
    sent_at: "2026-10-24T10:00:00Z",
    reply_state: "waiting",
    reply_checked_at: "2026-10-24T10:02:00Z",
    reply_scan_count: 0,
    reply_scan_complete: true,
  };
  expect(readOutlookDraft(waiting).reply_state).toBe("waiting");
  expect(() =>
    readOutlookDraft({ ...waiting, reply_scan_complete: false }),
  ).toThrow();
  expect(() => readOutlookDraft({ ...waiting, verified_at: null })).toThrow();
  expect(() => readOutlookDraft({ ...waiting, status: "uncertain" })).toThrow();
  expect(() =>
    readOutlookDraft({
      ...waiting,
      reply_state: "replied",
      reply_received_at: waiting.sent_at,
    }),
  ).toThrow();
  expect(() =>
    readOutlookDraft({
      ...waiting,
      reply_state: "replied",
      reply_received_at: null,
    }),
  ).toThrow();
});
