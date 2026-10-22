import { describe, expect, it } from "vitest";
import { selectAvailabilityOptions } from "./student-availability-options";
import type {
  AvailabilityOpening,
  AvailabilityResult,
} from "./student-calendar-api";

const now = Date.parse("2026-10-22T08:00:00Z");
function opening(start = "09:00", end = "10:00"): AvailabilityOpening {
  return {
    start_at: `2026-10-22T${start}:00Z`,
    end_at: `2026-10-22T${end}:00Z`,
    minutes:
      (Date.parse(`2026-10-22T${end}:00Z`) -
        Date.parse(`2026-10-22T${start}:00Z`)) /
      60_000,
    reasons: [],
    unchecked_source_ids: [],
    unknown_travel_record_ids: [],
    displaced_study_block_ids: [],
  };
}
function result(openings: AvailabilityOpening[]): AvailabilityResult {
  return {
    timezone: "UTC",
    checked_at: new Date(now).toISOString(),
    scope: "provided_owned_sources_only",
    status: "complete",
    confirmed_openings: openings,
    provisional_openings: [],
    busy: [],
    warnings: [],
    changes_applied: false,
  };
}

describe("positioned availability choices", () => {
  it("fits the requested duration and its own ten-minute buffers inside each confirmed opening", () => {
    const options = selectAvailabilityOptions(result([opening()]), 30, now);
    expect(options).toEqual([
      {
        start_at: "2026-10-22T09:10:00.000Z",
        end_at: "2026-10-22T09:40:00.000Z",
        reserved_start_at: "2026-10-22T09:00:00.000Z",
        reserved_end_at: "2026-10-22T09:50:00.000Z",
        duration_minutes: 30,
        buffer_before_minutes: 10,
        buffer_after_minutes: 10,
      },
    ]);
  });
  it("returns fewer than two when duration and buffers do not fit, including requested shorter meetings", () => {
    expect(
      selectAvailabilityOptions(result([opening("09:00", "09:49")]), 30, now),
    ).toEqual([]);
    expect(
      selectAvailabilityOptions(result([opening("09:00", "09:35")]), 15, now),
    ).toHaveLength(1);
    expect(
      selectAvailabilityOptions(result([opening("09:00", "09:34")]), 15, now),
    ).toEqual([]);
  });
  it("prefers separate windows, preserves input order and never returns overlapping reserved footprints", () => {
    const windows = [opening("13:00", "17:00"), opening("09:00", "12:00")];
    const snapshot = result(windows);
    const before = JSON.stringify(snapshot);
    const choices = selectAvailabilityOptions(snapshot, 30, now);
    expect(choices.map((option) => option.start_at)).toEqual([
      "2026-10-22T09:10:00.000Z",
      "2026-10-22T13:10:00.000Z",
    ]);
    expect(JSON.stringify(snapshot)).toBe(before);
  });
  it("can position a second distinct choice in one wide opening without inventing another gap", () => {
    const choices = selectAvailabilityOptions(
      result([opening("09:00", "10:40")]),
      30,
      now,
    );
    expect(choices).toHaveLength(2);
    expect(Date.parse(choices[0].reserved_end_at)).toBeLessThanOrEqual(
      Date.parse(choices[1].reserved_start_at),
    );
    expect(choices[1].start_at).toBe("2026-10-22T10:00:00.000Z");
    expect(choices[1].reserved_end_at).toBe("2026-10-22T10:40:00.000Z");
  });
  it("uses a whole-minute option when five-minute rounding would discard an otherwise fitting gap", () => {
    const choices = selectAvailabilityOptions(
      result([opening("09:02", "09:52")]),
      30,
      now,
    );
    expect(choices).toHaveLength(1);
    expect(choices[0].start_at).toBe("2026-10-22T09:12:00.000Z");
  });
  it("withholds unknown coverage, unknown travel, displaced work and unconfirmed routine", () => {
    for (const change of [
      { reasons: ["coverage_unknown" as const] },
      { unchecked_source_ids: ["calendar"] },
      { unknown_travel_record_ids: ["meeting"] },
      { displaced_study_block_ids: ["study"] },
    ])
      expect(
        selectAvailabilityOptions(
          result([{ ...opening(), ...change }]),
          30,
          now,
        ),
      ).toEqual([]);
    expect(
      selectAvailabilityOptions(
        { ...result([opening()]), status: "needs_routine" },
        30,
        now,
      ),
    ).toEqual([]);
    expect(
      selectAvailabilityOptions(
        { ...result([]), provisional_openings: [opening()] },
        30,
        now,
      ),
    ).toEqual([]);
  });
  it("does not propose elapsed time and handles offset instants without using device timezone", () => {
    const instant = Date.parse("2026-10-22T09:20:00Z");
    const source = {
      ...opening(),
      start_at: "2026-10-22T05:00:00-04:00",
      end_at: "2026-10-22T06:00:00-04:00",
    };
    const choices = selectAvailabilityOptions(result([source]), 15, instant);
    expect(choices[0].start_at).toBe("2026-10-22T09:30:00.000Z");
    expect(Date.parse(choices[0].reserved_start_at)).toBeGreaterThanOrEqual(
      instant,
    );
  });
  it("never exceeds two even with overlapping or duplicate input openings", () => {
    const choices = selectAvailabilityOptions(
      result([
        opening(),
        opening(),
        opening("10:00", "12:00"),
        opening("14:00", "18:00"),
      ]),
      30,
      now,
    );
    expect(choices).toHaveLength(2);
    expect(Date.parse(choices[0].reserved_end_at)).toBeLessThanOrEqual(
      Date.parse(choices[1].reserved_start_at),
    );
  });
});
