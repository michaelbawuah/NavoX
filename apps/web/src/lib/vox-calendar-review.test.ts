import { expect, it } from "vitest";
import {
  parseVoxCalendarReview,
  VOX_CALENDAR_REVIEW_TOPIC,
} from "./vox-calendar-review";

const review = {
  kind: "calendar-event",
  event_id: "11111111-1111-4111-8111-111111111111",
  status: "pending",
  voice_approval_available: false,
  destination: "/connections#calendar",
};
const encode = (value: unknown) =>
  new TextEncoder().encode(JSON.stringify(value));
it("accepts only the bounded metadata contract for calendar activity", () => {
  expect(VOX_CALENDAR_REVIEW_TOPIC).toBe("navox.vox.calendar-review.v1");
  for (const status of [
    "pending",
    "executing",
    "uncertain",
    "created",
    "changed",
    "undoing",
    "undone",
    "failed",
  ])
    expect(parseVoxCalendarReview(encode({ ...review, status }))).toEqual({
      ...review,
      status,
    });
});
it.each([
  null,
  [],
  {},
  { ...review, kind: "other" },
  { ...review, event_id: "not-an-event" },
  { ...review, status: "approved" },
  { ...review, voice_approval_available: "true" },
  { ...review, destination: "https://example.test/connections" },
  { ...review, destination: "/connections#calendar?private=title" },
  { ...review, title: "Private meeting title" },
  { ...review, source_conversation_id: review.event_id },
])("rejects invalid or expanded metadata: %j", (value) => {
  expect(parseVoxCalendarReview(encode(value))).toBeNull();
});
it("rejects malformed, oversized and invalid UTF-8 packets", () => {
  expect(parseVoxCalendarReview(new Uint8Array([0xc3, 0x28]))).toBeNull();
  expect(parseVoxCalendarReview(new TextEncoder().encode("{"))).toBeNull();
  expect(parseVoxCalendarReview(new Uint8Array(1025))).toBeNull();
});
