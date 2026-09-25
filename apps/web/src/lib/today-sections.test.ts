import { describe, expect, it } from "vitest";
import { todaySections } from "./today-sections";

describe("Today section selection", () => {
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
