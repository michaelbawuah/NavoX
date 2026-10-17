import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type {
  StudentImportPreview,
  StudentLookup,
  StudentOccurrence,
  StudentProfile,
  StudentTimetable,
} from "../lib/student-schedule";
import {
  StudentCourseEditor,
  StudentDayAgenda,
  StudentSetupCard,
} from "./student-classes";
import { StudentDaySummary } from "./student-day-panel";
import { StudentTimeLookup } from "./student-time-lookup";
import { StudentImportReview } from "./student-timetable-import";

const occurrence: StudentOccurrence = {
  id: "monday-class",
  meeting_id: "meeting-1",
  course_id: "course-1",
  course_code: "CS 100",
  course_name: "Algorithms",
  kind: "lecture",
  start_at: "2026-10-19T10:10:00-04:00",
  end_at: "2026-10-19T11:00:00-04:00",
  timezone: "America/New_York",
  building: "Science Center",
  room: "120",
  instructor: null,
  walk_minutes: null,
  cancelled: false,
  exception: false,
};
const lookup: StudentLookup = {
  server_now: "2026-10-19T14:20:00Z",
  timezone: "America/New_York",
  mode: "now",
  date: "2026-10-19",
  at: "2026-10-19T14:20:00Z",
  occurrences: [occurrence],
  next_upcoming: null,
  reason: "matches",
  conflicts: [],
};
const profile: StudentProfile = {
  school_name: null,
  lms: "none",
  display_name: null,
  timezone: "America/New_York",
  term_start: null,
  term_end: null,
  revision: 0,
};
const table: StudentTimetable = {
  server_now: lookup.server_now,
  timezone: lookup.timezone,
  date: lookup.date,
  days: 1,
  courses: [],
  fixed_blocks: [],
  breaks: [],
  exceptions: [],
  occurrences: [occurrence],
  fixed_occurrences: [],
  conflicts: [],
  warnings: [],
};
const render = (component: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(component);

describe("student timetable presentation", () => {
  it("renders actual saved class, server time and zone without provider requests", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render(
        createElement(StudentDaySummary, {
          now: lookup,
          next: { ...lookup, mode: "next", occurrences: [] },
          onRefresh: vi.fn(),
        }),
      );
      expect(html).toContain("CS 100");
      expect(html).toContain("Science Center · 120");
      expect(html).toContain("Now · 10:20 AM");
      expect(html).toContain("America/New York");
      expect(html).toContain('href="/classes"');
      expect(html).not.toContain("walk");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it("shows a next-day fallback with its actual date rather than implying today", () => {
    const later = {
      ...occurrence,
      id: "tomorrow",
      start_at: "2026-10-20T10:10:00-04:00",
      end_at: "2026-10-20T11:00:00-04:00",
    };
    const html = render(
      createElement(StudentDaySummary, {
        now: { ...lookup, occurrences: [], reason: "no_classes" },
        next: {
          ...lookup,
          mode: "next",
          occurrences: [],
          next_upcoming: later,
        },
        onRefresh: vi.fn(),
      }),
    );
    expect(html).toContain("Tuesday, Oct 20");
    expect(html).toContain("No class at the moment");
  });
  it("keeps loading, failed lookup, break and empty states distinct", () => {
    const loading = render(
      createElement(StudentDaySummary, {
        now: null,
        next: null,
        loading: true,
        onRefresh: vi.fn(),
      }),
    );
    expect(loading).toContain("Checking the time");
    expect(loading).not.toContain("No class");
    const failed = render(
      createElement(StudentDaySummary, {
        now: null,
        next: null,
        error: "network",
        onRefresh: vi.fn(),
      }),
    );
    expect(failed).toContain('role="alert"');
    expect(failed).not.toContain("No class");
    const rest = render(
      createElement(StudentDaySummary, {
        now: { ...lookup, occurrences: [], reason: "break" },
        next: { ...lookup, mode: "next", occurrences: [], next_upcoming: null },
        onRefresh: vi.fn(),
      }),
    );
    expect(rest).toContain("class break");
    expect(rest).toContain("No next class saved");
  });
  it("renders both conflicting classes and warns without choosing one", () => {
    const html = render(
      createElement(StudentDaySummary, {
        now: {
          ...lookup,
          occurrences: [
            occurrence,
            { ...occurrence, id: "second", course_code: "ECE 200" },
          ],
          conflicts: [
            {
              left_id: occurrence.id,
              right_id: "second",
              kind: "class_class",
              start_at: occurrence.start_at,
              end_at: occurrence.end_at,
            },
          ],
        },
        next: { ...lookup, mode: "next", occurrences: [] },
        onRefresh: vi.fn(),
      }),
    );
    expect(html).toContain("CS 100");
    expect(html).toContain("ECE 200");
    expect(html).toContain("saved times overlap");
  });
  it("shows cancellation, one-day changes and shift overlaps on the agenda", () => {
    const html = render(
      createElement(StudentDayAgenda, {
        timetable: {
          ...table,
          occurrences: [{ ...occurrence, cancelled: true, exception: true }],
          fixed_occurrences: [
            {
              id: "shift",
              fixed_block_id: "s1",
              title: "Library desk",
              start_at: occurrence.start_at,
              end_at: occurrence.end_at,
            },
          ],
          conflicts: [
            {
              left_id: occurrence.id,
              right_id: "shift",
              kind: "class_fixed",
              start_at: occurrence.start_at,
              end_at: occurrence.end_at,
            },
          ],
        },
      }),
    );
    expect(html).toContain("Cancelled");
    expect(html).toContain("Library desk");
    expect(html).toContain("Overlaps another");
    expect(html).toContain("Monday, Oct 19");
  });
  it("does not invent class times on break days or an empty timetable", () => {
    const html = render(
      createElement(StudentDayAgenda, {
        timetable: {
          ...table,
          occurrences: [],
          breaks: [
            {
              id: "break",
              name: "Fall break",
              start_date: "2026-10-19",
              end_date: "2026-10-20",
              revision: 1,
            },
          ],
        },
      }),
    );
    expect(html).toContain("Fall break · classes excluded");
    expect(html).toContain("No classes or shifts saved");
    expect(html).not.toContain("CS 100");
  });
  it("offers skippable any-school setup without signing into a school or enabling location", () => {
    const html = render(
      createElement(StudentSetupCard, {
        profile,
        busy: false,
        onSave: vi.fn(),
        onChoose: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("every step is optional");
    expect(html).toContain("Skip for now");
    expect(html).toContain("School (optional)");
    expect(html).toContain("Any school works");
    expect(html).not.toContain("Cornell");
    expect(html).not.toContain("Turn on location");
  });
  it("leaves actual term dates blank in a new course instead of using sample dates", () => {
    const html = render(
      createElement(StudentCourseEditor, {
        profile,
        busy: false,
        onSave: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain('type="date"');
    expect(html).not.toContain("2026-");
    expect(html).toContain("Save course and add times");
  });
  it("shows bound import dates and times while allowing review of names and rooms", () => {
    const preview: StudentImportPreview = {
      preview_id: "p1",
      preview_hash: "a".repeat(64),
      expires_at: "2026-10-19T15:00:00Z",
      timezone: "America/New_York",
      candidates: [
        {
          id: "i1",
          course_code: "CS 100",
          course_name: "Algorithms",
          kind: "lecture",
          dates: ["2026-10-19", "2026-10-21"],
          start_time: "10:10",
          end_time: "11:00",
          timezone: "America/New_York",
          building: "Science Center",
          room: "120",
          instructor: null,
        },
      ],
      warnings: ["Check the room."],
    };
    const html = render(
      createElement(StudentImportReview, {
        preview,
        candidates: preview.candidates,
        selected: ["i1"],
        disabled: false,
        onEdit: vi.fn(),
        onSelect: vi.fn(),
      }),
    );
    expect(html).toContain("10:10–11:00");
    expect(html).toContain("2026-10-19 · 2026-10-21");
    expect(html).toContain('value="Algorithms"');
    expect(html).toContain('value="120"');
    expect(html).not.toContain('type="time"');
    expect(html).toContain("Check the room");
  });
  it("escapes class text rather than interpreting uploaded schedule markup", () => {
    const html = render(
      createElement(StudentDayAgenda, {
        timetable: {
          ...table,
          occurrences: [
            { ...occurrence, course_name: "<script>alert(1)</script>" },
          ],
        },
      }),
    );
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
  });
  it("starts time lookup as an explicit form without a browser-clock guessed class", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render(
        createElement(StudentTimeLookup, {
          date: "2026-10-19",
          timezone: "America/New_York",
        }),
      );
      expect(html).toContain("At a specific time");
      expect(html).toContain("Check saved classes");
      expect(html).toContain('value="2026-10-19"');
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
});
