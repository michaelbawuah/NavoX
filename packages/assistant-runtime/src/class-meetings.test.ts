import { describe, expect, it } from "vitest";
import {
  answerNextClass,
  nextClassMeeting,
  normalizeClassMeetings,
  parseClassSourceSnapshot,
} from "./class-meetings";

const now = new Date("2026-09-30T16:00:00Z");
const ids = [
  "00000000-0000-4000-8000-000000000001",
  "00000000-0000-4000-8000-000000000002",
  "00000000-0000-4000-8000-000000000003",
];
function source(index: number, updatedAt: string | null) {
  return {
    resource_id: ids[index],
    connection_id: "00000000-0000-4000-8000-000000000010",
    external_resource_id: `resource:${index}`,
    updated_at: updatedAt,
  };
}
function fixture(
  canvasAt = "2026-09-30T18:30:00Z",
  googleAt = "2026-09-30T19:00:00Z",
) {
  return {
    complete: true,
    courses: [
      {
        course_id: "42",
        course_name: "Economics 3120",
        course_code: "ECON 3120",
        section: "A",
        source: source(0, null),
      },
    ],
    events: [
      {
        provider: "canvas",
        course_id: "42",
        title: "ECON 3120 Lecture",
        start_at: canvasAt,
        end_at: "2026-09-30T20:00:00Z",
        location: "Room 1",
        meeting_url: null,
        status: "SCHEDULED",
        explicit_class_meeting: true,
        all_day: false,
        fresh_until: "2026-10-01T00:00:00Z",
        source: source(1, "2026-09-29T12:00:00Z"),
      },
      {
        provider: "google",
        course_id: null,
        title: "ECON 3120",
        start_at: googleAt,
        end_at: "2026-09-30T20:00:00Z",
        location: "Room 1",
        meeting_url: null,
        status: "SCHEDULED",
        explicit_class_meeting: true,
        all_day: false,
        fresh_until: "2026-10-01T00:00:00Z",
        source: source(2, "2026-09-30T14:00:00Z"),
      },
    ],
  };
}

describe("next class normalization", () => {
  it("retains both sources and the 2:30/3:00 conflict, preferring the newer explicit update", () => {
    const meetings = normalizeClassMeetings(
      parseClassSourceSnapshot(fixture(), now),
      now,
    );
    expect(nextClassMeeting(meetings, now)).toMatchObject({
      courseName: "Economics 3120",
      courseCode: "ECON 3120",
      startAt: "2026-09-30T19:00:00Z",
      conflictState: "TIME_CONFLICT",
      confidence: "CONFLICTED",
    });
    expect(meetings[0]?.sourceRefs).toHaveLength(3);
    const answer = answerNextClass(
      parseClassSourceSnapshot(fixture(), now),
      now,
      "America/New_York",
    );
    expect(answer.state).toBe("READY");
    expect(JSON.stringify(answer.blocks)).toContain("3:00 PM");
    expect(JSON.stringify(answer.blocks)).toContain("2:30 PM");
    expect(JSON.stringify(answer.blocks)).toContain("updated more recently");
  });

  it("offers only the schedule-authoritative event for guarded navigation", () => {
    const data = fixture();
    const google = data.events.at(1);
    if (!google) throw new Error("Missing Calendar fixture");
    Object.assign(google.source, { navigation_available: true });
    const answer = answerNextClass(
      parseClassSourceSnapshot(data, now),
      now,
      "America/New_York",
    );
    expect(answer.blocks).toContainEqual({
      kind: "CLASS_NAVIGATION",
      label: "Open Calendar",
      connection_id: google.source.connection_id,
      resource_id: google.source.resource_id,
    });
  });

  it("does not infer a meeting from a Canvas course alone", () => {
    const data = fixture();
    data.events = [];
    expect(
      nextClassMeeting(
        normalizeClassMeetings(parseClassSourceSnapshot(data, now), now),
        now,
      ),
    ).toBeNull();
  });

  it("merges agreeing meeting times and retains a Canvas room omitted by Calendar", () => {
    const data = fixture("2026-09-30T18:30:00Z", "2026-09-30T18:30:00Z");
    const google = data.events.at(1);
    if (!google) throw new Error("Missing Calendar fixture");
    Object.assign(google, { location: null });
    const meeting = nextClassMeeting(
      normalizeClassMeetings(parseClassSourceSnapshot(data, now), now),
      now,
    );
    expect(meeting).toMatchObject({
      confidence: "CORROBORATED",
      conflictState: "NONE",
      location: "Room 1",
    });
  });

  it("uses a single explicit Canvas or Calendar meeting without fabricating a course listing", () => {
    for (const index of [0, 1]) {
      const data = fixture();
      data.courses = [];
      const event = data.events.at(index);
      if (!event) throw new Error("Missing event fixture");
      data.events = [event];
      const meeting = nextClassMeeting(
        normalizeClassMeetings(parseClassSourceSnapshot(data, now), now),
        now,
      );
      expect(meeting?.confidence).toBe("SINGLE_SOURCE");
      expect(meeting?.courseName).toContain("ECON 3120");
    }
  });

  it("keeps an unresolved conflict visible but does not select it as next class", () => {
    const data = fixture();
    const canvas = data.events.at(0);
    if (!canvas) throw new Error("Missing Canvas fixture");
    canvas.source.updated_at = null;
    const meetings = normalizeClassMeetings(
      parseClassSourceSnapshot(data, now),
      now,
    );
    expect(meetings[0]?.conflictState).toBe("UNRESOLVED");
    expect(nextClassMeeting(meetings, now)).toBeNull();
  });

  it("does not pair a course lab with a separate lecture", () => {
    const data = fixture();
    const google = data.events.at(1);
    if (!google) throw new Error("Missing Calendar fixture");
    google.title = "ECON 3120 Lab";
    const meetings = normalizeClassMeetings(
      parseClassSourceSnapshot(data, now),
      now,
    );
    expect(meetings).toHaveLength(2);
    expect(meetings.every((meeting) => meeting.conflictState === "NONE")).toBe(
      true,
    );
  });

  it("excludes a cancelled meeting from next class", () => {
    const data = fixture();
    const google = data.events.at(1);
    if (!google) throw new Error("Missing Calendar fixture");
    google.status = "CANCELLED";
    const meetings = normalizeClassMeetings(
      parseClassSourceSnapshot(data, now),
      now,
    );
    expect(meetings[0]?.conflictState).toBe("STATUS_CONFLICT");
    expect(nextClassMeeting(meetings, now)).toBeNull();
    expect(
      answerNextClass(
        parseClassSourceSnapshot(data, now),
        now,
        "America/New_York",
      ).state,
    ).toBe("CLARIFY");
  });

  it("refuses incomplete source coverage", () => {
    expect(() =>
      parseClassSourceSnapshot({ ...fixture(), complete: false }, now),
    ).toThrow();
  });
});
