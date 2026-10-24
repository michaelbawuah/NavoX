import { describe, expect, it } from "vitest";
import type { StudyTask } from "./student-planning";
import type { StudentTimetable } from "./student-schedule";
import { dayRows, dueSoon, readHomeItems, relativeTime } from "./today-home";

const now = Date.parse("2026-10-24T00:00:00Z"),
  zone = "America/New_York";
const item = (id: string, status = "active", type = "task") => ({
  id,
  title: id,
  status,
  type,
  due_at: "2026-10-24T01:00:00Z",
  sources: [],
});
const feed = {
  completed_recently: [],
  needs_attention: [],
  coming_up: [],
  waiting_on: [],
};
describe("Today data projection", () => {
  it("deduplicates and lets completed records suppress older active copies", () => {
    expect(
      readHomeItems({
        ...feed,
        completed_recently: [item("finished", "completed")],
        needs_attention: [
          item("finished"),
          item("one"),
          item("uncertain", "candidate"),
        ],
        coming_up: [item("one"), item("subscription", "active", "renewal")],
      }).map((i) => i.id),
    ).toEqual(["one"]);
  });
  it("rejects malformed feeds instead of treating failed data as an empty day", () => {
    expect(() => readHomeItems({})).toThrow();
    expect(() =>
      readHomeItems({ ...feed, coming_up: [{ title: "missing identity" }] }),
    ).toThrow();
  });
  it("uses the student's local date instead of the UTC date", () => {
    const table: StudentTimetable = {
      server_now: new Date(now).toISOString(),
      timezone: zone,
      date: "2026-10-23",
      days: 1,
      courses: [],
      fixed_blocks: [],
      breaks: [],
      exceptions: [],
      conflicts: [],
      warnings: [],
      occurrences: [],
      fixed_occurrences: [
        {
          id: "today",
          fixed_block_id: "shift-1",
          title: "Evening shift",
          start_at: "2026-10-24T01:00:00Z",
          end_at: "2026-10-24T02:00:00Z",
        },
        {
          id: "tomorrow",
          fixed_block_id: "shift-2",
          title: "Tomorrow",
          start_at: "2026-10-24T13:00:00Z",
          end_at: "2026-10-24T14:00:00Z",
        },
      ],
    };
    expect(dayRows(table, null, [], [], now, zone).map((r) => r.id)).toEqual([
      "today",
    ]);
  });
  it("orders repeated local times by their real instant across DST", () => {
    const rows = dayRows(
      null,
      null,
      [],
      [
        {
          ...item("later", "active", "meeting"),
          due_at: "2026-11-01T06:30:00Z",
        },
        {
          ...item("earlier", "active", "meeting"),
          due_at: "2026-11-01T05:30:00Z",
        },
      ],
      Date.parse("2026-11-01T04:00:00Z"),
      zone,
    );
    expect(rows.map((r) => r.id)).toEqual(["earlier", "later"]);
  });
  it("does not duplicate a task imported from a commitment", () => {
    const task = {
      id: "task",
      title: "Assignment",
      status: "active",
      due_at: "2026-10-24T01:00:00Z",
      course_id: "course",
      source_item_id: "source",
    } as StudyTask;
    expect(
      dueSoon([task], [item("source"), item("other")], now).map((i) => i.id),
    ).toEqual(["task", "other"]);
  });
  it("caps due work at three eligible items and excludes overdue or distant dates", () => {
    const rows = [
      item("a"),
      item("b"),
      item("c"),
      item("d"),
      { ...item("past"), due_at: "2026-10-22T00:00:00Z" },
      { ...item("future"), due_at: "2026-11-03T00:00:00Z" },
    ];
    expect(dueSoon([], rows, now).map((i) => i.id)).toEqual(["a", "b", "c"]);
  });
  it("uses plain local time and never a UTC label", () => {
    expect(relativeTime("2026-10-24T00:15:00Z", now, zone)).toBe("in 15 min");
    expect(relativeTime("2026-10-24T02:00:00Z", now, zone)).toContain("10:00");
    expect(relativeTime("2026-10-24T02:00:00Z", now, zone)).not.toContain(
      "UTC",
    );
  });
});
