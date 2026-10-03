import { describe, expect, it } from "vitest";
import { consumerTodaySections, todaySections } from "./today-sections";

describe("Today section selection", () => {
  it("uses three sections and removes resolved duplicates from attention", () => {
    const now = Date.parse("2026-10-03T12:00:00Z");
    const reply: {
      id: string;
      type: string;
      status: string;
      due_at: string | null;
    } = { id: "reply", type: "reply", status: "confirmed", due_at: null };
    const resolved = { ...reply, status: "completed" };
    const renewal = {
      id: "renewal",
      type: "renewal",
      status: "confirmed",
      due_at: "2026-10-04T12:00:00Z",
    };
    const later = { ...renewal, id: "later", due_at: "2026-10-20T12:00:00Z" };
    const sections = consumerTodaySections(
      {
        needs_attention: [reply],
        coming_up: [later],
        waiting_on: [],
        renewals: [renewal, later],
        completed_recently: [resolved],
      },
      now,
    );
    expect(sections["Needs your attention"]).toEqual([renewal]);
    expect(sections["Coming up"]).toEqual([later]);
    expect(sections.Updates).toEqual([resolved]);
    expect(Object.values(sections).flat()).toHaveLength(3);
    expect(Object.values(consumerTodaySections(null, now)).flat()).toEqual([]);
  });
  it("shows each commitment once while keeping urgent renewals in attention", () => {
    const renewal = { id: "r", type: "renewal", status: "confirmed" };
    const waiting = { id: "w", type: "task", status: "waiting" };
    const task = { id: "t", type: "task", status: "confirmed" };
    const sections = todaySections({
      needs_attention: [renewal, waiting],
      coming_up: [task],
      renewals: [renewal],
      waiting_on: [waiting],
    });
    expect(sections["Needs Attention"]).toEqual([renewal]);
    expect(sections["Waiting On"]).toEqual([waiting]);
    expect(sections["Coming Up"]).toEqual([task]);
    expect(Object.values(sections).flat()).toHaveLength(3);
  });
});
