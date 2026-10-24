import { expect, it } from "vitest";
import { setupClassDays, setupWakeTimes } from "./routine-setup";
import type { StudyPreferences } from "./student-planning";
import type { StudentTimetable } from "./student-schedule";

const preferences = {
  wake_time: "08:00",
  wake_times: ["07:00", "08:00", "09:00", "08:00", "08:00", "10:00", "10:30"],
} as StudyPreferences;
it("derives actual upcoming local class days, including weekend classes, and excludes cancelled/past meetings", () => {
  const timetable = {
    timezone: "America/Los_Angeles",
    server_now: "2026-10-19T16:00:00Z",
    occurrences: [
      {
        start_at: "2026-10-21T17:00:00Z",
        end_at: "2026-10-21T18:00:00Z",
        cancelled: false,
      },
      {
        start_at: "2026-10-26T01:00:00Z",
        end_at: "2026-10-26T02:00:00Z",
        cancelled: false,
      },
      {
        start_at: "2026-10-19T13:00:00Z",
        end_at: "2026-10-19T14:00:00Z",
        cancelled: false,
      },
      {
        start_at: "2026-10-20T17:00:00Z",
        end_at: "2026-10-20T18:00:00Z",
        cancelled: true,
      },
    ],
  } as StudentTimetable;
  expect(setupClassDays(timetable)).toEqual([2, 6]);
  expect(setupWakeTimes(preferences, [2, 6], "06:30", "09:30")).toEqual([
    "09:30",
    "09:30",
    "06:30",
    "09:30",
    "09:30",
    "09:30",
    "06:30",
  ]);
});
it("does not flatten saved per-day times when the student only changes sleep or focus", () => {
  expect(setupWakeTimes(preferences, [0, 2], null, null)).toEqual(
    preferences.wake_times,
  );
  expect(setupWakeTimes(preferences, [0, 2], "06:00", null)).toEqual([
    "06:00",
    "08:00",
    "06:00",
    "08:00",
    "08:00",
    "10:00",
    "10:30",
  ]);
});
it("uses no invented class weekdays when the timetable is empty", () => {
  expect(
    setupWakeTimes(
      { wake_time: "08:00" } as StudyPreferences,
      [],
      null,
      "09:00",
    ),
  ).toEqual(Array(7).fill("09:00"));
});
