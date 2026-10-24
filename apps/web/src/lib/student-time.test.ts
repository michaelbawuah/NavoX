import { describe, expect, it } from "vitest";
import { studentLocalTime } from "./student-time";

describe("one student-local clock", () => {
  it.each([
    ["2026-10-24T07:00:00Z", "America/New_York", "2026-10-24", 3, "Still up"],
    [
      "2026-10-24T16:00:00Z",
      "America/New_York",
      "2026-10-24",
      12,
      "Good afternoon",
    ],
    [
      "2026-10-25T03:00:00Z",
      "America/New_York",
      "2026-10-24",
      23,
      "Good evening",
    ],
    [
      "2026-10-24T07:00:00Z",
      "America/Los_Angeles",
      "2026-10-24",
      0,
      "Still up",
    ],
    [
      "2026-10-24T06:59:00Z",
      "America/Los_Angeles",
      "2026-10-23",
      23,
      "Good evening",
    ],
    ["2026-10-24T08:59:00Z", "America/New_York", "2026-10-24", 4, "Still up"],
    [
      "2026-10-24T09:00:00Z",
      "America/New_York",
      "2026-10-24",
      5,
      "Good morning",
    ],
    [
      "2026-10-24T20:59:00Z",
      "America/New_York",
      "2026-10-24",
      16,
      "Good afternoon",
    ],
    [
      "2026-10-24T21:00:00Z",
      "America/New_York",
      "2026-10-24",
      17,
      "Good evening",
    ],
    ["2026-03-08T06:59:00Z", "America/New_York", "2026-03-08", 1, "Still up"],
    ["2026-03-08T07:00:00Z", "America/New_York", "2026-03-08", 3, "Still up"],
    ["2026-11-01T05:30:00Z", "America/New_York", "2026-11-01", 1, "Still up"],
    ["2026-11-01T06:30:00Z", "America/New_York", "2026-11-01", 1, "Still up"],
    [
      "2026-03-08T10:00:00Z",
      "America/Los_Angeles",
      "2026-03-08",
      3,
      "Still up",
    ],
  ])(
    "%s in %s uses the local day and greeting",
    (instant, zone, date, hour, greeting) => {
      expect(studentLocalTime(instant, zone)).toMatchObject({
        date,
        hour,
        greeting,
      });
    },
  );
  it("formats a truthful local header without exposing UTC", () => {
    const local = studentLocalTime("2026-10-24T07:43:00Z", "America/New_York");
    expect(local.clock).toBe("3:43 AM");
    expect(local.header).toBe("Sat, Oct 24");
  });
});
