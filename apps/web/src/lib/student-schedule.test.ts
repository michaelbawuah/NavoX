import { afterEach, describe, expect, it, vi } from "vitest";
import {
  addStudentDays,
  readStudentCourse,
  readStudentLookup,
  readStudentMeeting,
  readStudentProfile,
  readStudentTimetable,
  StudentRequestError,
  studentClock,
  studentCourseDraft,
  studentDateInZone,
  studentFixedDraft,
  studentMeetingDraft,
  studentRequest,
} from "./student-schedule";

const profile = {
  school_name: null,
  lms: "none",
  display_name: null,
  timezone: "America/New_York",
  term_start: null,
  term_end: null,
  revision: 0,
};
const meeting = {
  id: "meeting-1",
  course_id: "course-1",
  kind: "lecture" as const,
  weekdays: [0, 2],
  start_time: "10:10",
  end_time: "11:00",
  timezone: "America/New_York",
  building: "Science Center",
  room: "120",
  instructor: null,
  walk_minutes: 8,
  dates: null,
  revision: 2,
};
const course = {
  id: "course-1",
  code: "CS 100",
  name: "Algorithms",
  term_start: "2026-08-24",
  term_end: "2026-12-10",
  professor: null,
  archived: false,
  revision: 1,
  meetings: [meeting],
};
const occurrence = {
  id: "meeting-1:2026-10-19",
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
  walk_minutes: 8,
  cancelled: false,
  exception: false,
};
const table = {
  server_now: "2026-10-19T14:20:00Z",
  timezone: "America/New_York",
  date: "2026-10-19",
  days: 7,
  courses: [course],
  fixed_blocks: [],
  breaks: [],
  exceptions: [],
  occurrences: [occurrence],
  fixed_occurrences: [],
  conflicts: [],
  warnings: [],
};
afterEach(() => vi.restoreAllMocks());

describe("student schedule boundaries", () => {
  it("accepts an empty any-school profile without making up school or term data", () => {
    expect(readStudentProfile(profile)).toEqual(profile);
  });
  it("rejects an unknown time zone instead of falling back to the computer zone", () => {
    expect(() =>
      readStudentProfile({ ...profile, timezone: "Made/Up" }),
    ).toThrow("time zone");
  });
  it("requires aware server timestamps and a complete timetable response", () => {
    expect(() =>
      readStudentTimetable({ ...table, server_now: "2026-10-19T10:20:00" }),
    ).toThrow("time");
    expect(() =>
      readStudentTimetable({ ...table, fixed_occurrences: undefined }),
    ).toThrow("could not be read");
    expect(readStudentTimetable(table).occurrences[0].walk_minutes).toBe(8);
  });
  it("preserves exact imported dates and explicit walking estimates", () => {
    expect(
      readStudentMeeting({ ...meeting, dates: ["2026-10-19", "2026-11-02"] }),
    ).toMatchObject({ dates: ["2026-10-19", "2026-11-02"], walk_minutes: 8 });
  });
  it("rejects unknown class kinds and malformed times", () => {
    expect(() =>
      readStudentCourse({
        ...course,
        meetings: [{ ...meeting, kind: "unknown" }],
      }),
    ).toThrow("unsupported");
    expect(() =>
      readStudentMeeting({ ...meeting, start_time: "25:00" }),
    ).toThrow("time");
  });
  it("does not turn a failed or malformed lookup into no classes", () => {
    expect(() => readStudentLookup({ reason: "no_classes" })).toThrow();
  });
  it("uses the server instant in the saved zone across DST and different local dates", () => {
    expect(studentDateInZone("2026-11-01T03:30:00Z", "America/New_York")).toBe(
      "2026-10-31",
    );
    expect(studentClock("2026-11-01T05:30:00Z", "America/New_York")).toBe(
      "1:30 AM",
    );
    expect(studentClock("2026-11-01T06:30:00Z", "America/New_York")).toBe(
      "1:30 AM",
    );
    expect(addStudentDays("2026-10-31", 2)).toBe("2026-11-02");
    expect(addStudentDays("2026-12-31", 1)).toBe("2027-01-01");
  });
  it("strips response-only fields from edit payloads while preserving all editable details", () => {
    const {
      id: _id,
      revision: _revision,
      archived: _archived,
      meetings: _meetings,
      ...editable
    } = course;
    expect(studentCourseDraft(course)).toEqual(editable);
    expect(studentMeetingDraft(meeting)).toEqual({
      kind: "lecture",
      weekdays: [0, 2],
      start_time: "10:10",
      end_time: "11:00",
      timezone: "America/New_York",
      building: "Science Center",
      room: "120",
      instructor: null,
      walk_minutes: 8,
    });
    expect(
      studentFixedDraft({
        id: "shift-1",
        revision: 4,
        title: "Library",
        weekdays: [1],
        start_time: "17:00",
        end_time: "20:00",
        timezone: "America/New_York",
        term_start: "2026-08-24",
        term_end: "2026-12-10",
      } as Parameters<typeof studentFixedDraft>[0]),
    ).not.toHaveProperty("id");
  });
});

describe("student request recovery", () => {
  it("uses authenticated no-store reads and never starts a provider sync", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(profile)));
    expect(await studentRequest("/profile", readStudentProfile)).toEqual(
      profile,
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/student/profile"),
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        cache: "no-store",
      }),
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it.each([400, 401, 403, 409, 422])(
    "keeps a known %s rejection distinct from an unknown write",
    async (status) => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(JSON.stringify({ detail: "Review the saved values." }), {
          status,
        }),
      );
      await expect(
        studentRequest("/profile", readStudentProfile, "PUT", profile),
      ).rejects.toMatchObject({
        uncertain: false,
        message: "Review the saved values.",
      });
    },
  );
  it("blocks repeat writes after a server error or lost response", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("{}", { status: 503 }))
      .mockRejectedValueOnce(new TypeError("lost response"));
    await expect(
      studentRequest("/courses", readStudentCourse, "POST", {}),
    ).rejects.toMatchObject({ uncertain: true });
    await expect(
      studentRequest("/courses", readStudentCourse, "POST", {}),
    ).rejects.toMatchObject({ uncertain: true });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it("treats malformed write success as unknown and malformed read as a read failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}"));
    await expect(
      studentRequest("/profile", readStudentProfile, "PUT", profile),
    ).rejects.toMatchObject({ uncertain: true });
    await expect(
      studentRequest("/profile", readStudentProfile),
    ).rejects.toBeInstanceOf(StudentRequestError);
  });
});
