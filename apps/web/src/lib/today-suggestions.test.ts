import { describe, expect, it } from "vitest";
import {
  defaultAlarm,
  type StudyOpenings,
  type StudyPlan,
  type StudyPreferences,
  type StudyTask,
} from "./student-planning";
import type { StudentTimetable } from "./student-schedule";
import { earlyClassAlarm, lateNightStudyMove } from "./today-suggestions";

const timezone = "America/New_York";
const now = Date.parse("2026-10-24T07:00:00Z");
const task = {
  id: "task",
  status: "active",
  due_at: "2026-10-24T23:00:00Z",
} as StudyTask;
const plan: StudyPlan = {
  revision: 1,
  needs_review: false,
  review_notice: null,
  has_more_history: false,
  blocks: [
    {
      id: "block",
      task_id: "task",
      plan_id: "plan",
      title: "Lab report",
      start_at: "2026-10-24T12:30:00Z",
      end_at: "2026-10-24T13:30:00Z",
      minutes: 60,
      status: "planned",
      revision: 1,
      actual_minutes: 0,
      outcome_at: null,
      focus_window: false,
    },
  ],
};
const openings: StudyOpenings = {
  timezone,
  checked_at: "2026-10-24T07:00:00Z",
  expires_at: "2026-10-24T07:01:00Z",
  calendar_checked: true,
  routine_confirmed: true,
  warnings: [],
  openings: [
    {
      start_at: "2026-10-24T18:00:00Z",
      end_at: "2026-10-24T20:00:00Z",
      minutes: 120,
    },
  ],
};
describe("late-night study suggestion", () => {
  it("uses a verified later window and leaves the saved block intact", () => {
    const result = lateNightStudyMove(plan, [task], openings, now, timezone);
    expect(result?.start_at).toBe("2026-10-24T18:00:00.000Z");
    expect(result?.block.start_at).toBe("2026-10-24T12:30:00Z");
  });
  it("accepts the informational travel-buffer note from a complete live calendar check", () => {
    expect(
      lateNightStudyMove(
        plan,
        [task],
        {
          ...openings,
          warnings: [
            "Calendar meetings include your 15-minute travel buffer on both sides.",
          ],
        },
        now,
        timezone,
      )?.start_at,
    ).toBe("2026-10-24T18:00:00.000Z");
  });
  it.each([
    { calendar_checked: false },
    { routine_confirmed: false },
    { warnings: ["Could not check one calendar"] },
    { expires_at: "2026-10-24T06:59:00Z" },
    {
      openings: [
        {
          start_at: "2026-10-24T18:00:00Z",
          end_at: "2026-10-24T18:30:00Z",
          minutes: 30,
        },
      ],
    },
  ])(
    "does not propose from incomplete, stale or too-short availability: %j",
    (change) => {
      expect(
        lateNightStudyMove(
          plan,
          [task],
          { ...openings, ...change },
          now,
          timezone,
        ),
      ).toBeNull();
    },
  );
  it("respects the deadline, plan review state and student-local day", () => {
    expect(
      lateNightStudyMove(
        plan,
        [{ ...task, due_at: "2026-10-24T18:30:00Z" }],
        openings,
        now,
        timezone,
      ),
    ).toBeNull();
    expect(
      lateNightStudyMove(
        { ...plan, needs_review: true },
        [task],
        openings,
        now,
        timezone,
      ),
    ).toBeNull();
    expect(
      lateNightStudyMove(
        plan,
        [task],
        {
          ...openings,
          openings: [
            {
              start_at: "2026-10-25T18:00:00Z",
              end_at: "2026-10-25T20:00:00Z",
              minutes: 120,
            },
          ],
        },
        now,
        timezone,
      ),
    ).toBeNull();
  });
});

const prefs: StudyPreferences = {
  timezone,
  revision: 1,
  wake_time: "08:00",
  sleep_time: "23:00",
  max_study_minutes: 90,
  break_minutes: 10,
  focus_windows: [],
  never_schedule: [],
  alarm: { ...defaultAlarm, enabled: true, prep_minutes: 30 },
};
const alarm = {
  day: "2026-10-25",
  enabled: true,
  scheduled_at: "2026-10-25T12:00:00Z",
  reason: "Saved wake time",
  first_class_at: "2026-10-25T12:40:00Z",
  first_class_title: "CS 2110",
};
const table: StudentTimetable = {
  timezone,
  server_now: "2026-10-25T01:00:00Z",
  date: alarm.day,
  days: 1,
  courses: [],
  fixed_blocks: [],
  breaks: [],
  exceptions: [],
  fixed_occurrences: [],
  conflicts: [],
  warnings: [],
  occurrences: [
    {
      id: "exam",
      meeting_id: "meeting",
      course_id: "course",
      course_code: "CS 2110",
      course_name: "Programming",
      kind: "exam",
      start_at: "2026-10-25T12:40:00Z",
      end_at: "2026-10-25T14:00:00Z",
      timezone,
      building: "Statler",
      room: "101",
      instructor: null,
      walk_minutes: 15,
      cancelled: false,
      exception: false,
    },
  ],
};
describe("early-class or exam wake suggestion", () => {
  it("uses a real exam time plus saved prep/walk and proposes smart mode without changing preferences", () => {
    const result = earlyClassAlarm(
      prefs,
      table,
      alarm,
      alarm.day,
      Date.parse(table.server_now),
    );
    expect(result?.scheduled_at).toBe("2026-10-25T11:55:00.000Z");
    expect(result?.first.kind).toBe("exam");
    expect(result?.preferences.alarm?.smart).toBe(true);
    expect(prefs.alarm?.smart).toBe(false);
  });
  it("does not invent an exam from deadlines or an unknown walk time", () => {
    expect(
      earlyClassAlarm(
        prefs,
        { ...table, occurrences: [] },
        alarm,
        alarm.day,
        now,
      ),
    ).toBeNull();
    expect(
      earlyClassAlarm(
        prefs,
        {
          ...table,
          occurrences: [{ ...table.occurrences[0], walk_minutes: null }],
        },
        alarm,
        alarm.day,
        now,
      ),
    ).toBeNull();
    expect(
      earlyClassAlarm(
        prefs,
        {
          ...table,
          occurrences: [{ ...table.occurrences[0], cancelled: true }],
        },
        alarm,
        alarm.day,
        now,
      ),
    ).toBeNull();
  });
  it("preserves skipped, disabled and already-smart alarms and honors clock warnings", () => {
    for (const change of [
      { skip_date: alarm.day },
      { enabled: false },
      { smart: true },
    ])
      expect(
        earlyClassAlarm(
          { ...prefs, alarm: { ...defaultAlarm, enabled: true, ...change } },
          table,
          alarm,
          alarm.day,
          now,
        ),
      ).toBeNull();
    expect(
      earlyClassAlarm(
        prefs,
        { ...table, warnings: ["Clock change"] },
        alarm,
        alarm.day,
        now,
      ),
    ).toBeNull();
    expect(
      earlyClassAlarm(
        prefs,
        table,
        { ...alarm, scheduled_at: "2026-10-25T11:00:00Z" },
        alarm.day,
        now,
      ),
    ).toBeNull();
  });
});
