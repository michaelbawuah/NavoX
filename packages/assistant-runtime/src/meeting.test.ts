import { describe, expect, it } from "vitest";
import { meetingBlocks, parseMeetingPrep } from "./meeting";
import type { TodayQueryResult } from "./today";

const ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const meeting = {
  commitment_id: ID,
  title: "Project review",
  starts_at: "2026-09-30T12:45:00Z",
  minutes_until: 45,
  description: "Discuss the plan",
  related_commitments: [{ id: OTHER, title: "Send recap", status: "open" }],
  prep_points: ["Review the plan", "Ask about the timeline"],
};
const today: TodayQueryResult = {
  intent: "meeting_prep",
  answer: "One meeting is coming up.",
  items: [
    {
      id: ID,
      type: "meeting",
      title: "Project review",
      description: null,
      status: "confirmed",
      due_at: meeting.starts_at,
      band: "briefing",
      sources: [],
    },
  ],
  supported_queries: [],
  details: ["Review the plan"],
};

describe("existing-service meeting preparation", () => {
  it("accepts a bounded response and binds it to the exact Today meeting", () => {
    const parsed = parseMeetingPrep(meeting);
    expect(parsed).toMatchObject({ commitment_id: ID });
    const blocks = meetingBlocks(today, parsed);
    expect(blocks.some((block) => block.kind === "MEETING_BRIEFING")).toBe(
      true,
    );
    expect(blocks.some((block) => block.kind === "DETAILS")).toBe(false);
  });

  it("qualifies malformed upstream facts and cross-snapshot mismatch", () => {
    expect(() =>
      parseMeetingPrep({ ...meeting, commitment_id: "not-id" }),
    ).toThrow();
    expect(() => parseMeetingPrep({ ...meeting, minutes_until: -1 })).toThrow();
    expect(() =>
      meetingBlocks(today, { ...meeting, commitment_id: OTHER }),
    ).toThrow();
    expect(() =>
      meetingBlocks(today, { ...meeting, starts_at: "2026-10-01T15:00:00Z" }),
    ).toThrow();
    expect(() => meetingBlocks(today, null)).toThrow();
  });

  it("keeps the owning service's no-meeting answer when both views are empty", () => {
    expect(
      meetingBlocks(
        { ...today, items: [], answer: "No upcoming meeting.", details: [] },
        null,
      ),
    ).toEqual([{ kind: "ANSWER", text: "No upcoming meeting." }]);
  });
});
