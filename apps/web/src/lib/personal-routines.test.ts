import { describe, expect, it } from "vitest";
import {
  canEnableRoutine,
  type RoutineConfig,
  type RoutineResult,
  readRoutineList,
  readRoutinePreview,
  readRoutineResult,
  readRoutineRuns,
  routineConfigError,
  routineConfigKey,
  routineConfiguration,
  routineSchedule,
  routineTemplate,
  routineTime,
} from "./personal-routines";

const now = Date.parse("2026-10-17T12:00:00Z");
const config = routineTemplate("morning_priorities", "America/New_York");
const result: RoutineResult = {
  checked_at: "2026-10-17T12:00:00Z",
  basis: "saved_data",
  headline: "Your saved priorities for today.",
  items: [
    {
      id: "item-1",
      title: "Review the launch",
      status: "confirmed",
      due_at: null,
      kind: "commitment",
    },
  ],
  notice: "From saved tasks only. Connected apps were not refreshed.",
};
const preview = {
  preview_token: "preview-1",
  expires_at: "2026-10-17T12:30:00Z",
  result,
  configKey: routineConfigKey(config),
};

describe("personal routine review gates", () => {
  it("requires a current preview of every exact schedule field before enabling", () => {
    expect(canEnableRoutine(config, null, now)).toBe(false);
    expect(canEnableRoutine(config, preview, now)).toBe(true);
    const changes: Partial<RoutineConfig>[] = [
      { name: "Changed name" },
      { recipe: "no_reply_check" },
      { timezone: "Europe/London" },
      { hour: 9 },
      { minute: 15 },
      { days_of_week: [0, 2] },
    ];
    for (const change of changes)
      expect(canEnableRoutine({ ...config, ...change }, preview, now)).toBe(
        false,
      );
    expect(
      canEnableRoutine(config, preview, Date.parse(preview.expires_at)),
    ).toBe(false);
    expect(canEnableRoutine(config, preview, Number.NaN)).toBe(false);
  });

  it("canonicalizes day order and whitespace without mutating the editor", () => {
    const draft = {
      ...config,
      name: ` ${config.name} `,
      days_of_week: [4, 2, 0, 2],
    };
    const normalized = routineConfiguration(draft);
    expect(normalized.name).toBe(config.name);
    expect(normalized.days_of_week).toEqual([0, 2, 4]);
    expect(draft.days_of_week).toEqual([4, 2, 0, 2]);
    expect(routineConfigKey(draft)).toBe(routineConfigKey(normalized));
  });

  it("starts Friday catch-up on Friday while all templates stay disabled until a write", () => {
    expect(routineTemplate("friday_catch_up", "UTC")).toMatchObject({
      hour: 16,
      minute: 0,
      days_of_week: [4],
    });
    expect(routineTemplate("no_reply_check", "UTC").days_of_week).toEqual([
      0, 1, 2, 3, 4,
    ]);
    expect(routineTemplate("morning_priorities", "UTC")).not.toHaveProperty(
      "enabled",
    );
  });

  it.each([
    { name: " " },
    { name: "x".repeat(101) },
    { timezone: "Mars/Olympus" },
    { hour: 24 },
    { minute: 60 },
    { hour: 1.5 },
    { days_of_week: [] },
    { days_of_week: [7] },
  ])("rejects invalid configuration %j before a preview or save", (change) => {
    expect(routineConfigError({ ...config, ...change })).toBeTruthy();
  });
});

describe("saved routine response boundaries", () => {
  const routine = {
    ...config,
    id: "routine-1",
    enabled: true,
    schedule_status: "scheduled",
    next_run_at: "2026-10-19T12:00:00Z",
    last_run_at: null,
    last_result: result,
    created_at: "2026-10-17T12:00:00Z",
  };

  it("accepts saved-data results and keeps pause and dispatch-failure status truthful", () => {
    expect(readRoutinePreview(preview).result).toEqual(result);
    expect(readRoutineList({ routines: [routine], paused: true })).toEqual({
      routines: [routine],
      paused: true,
    });
    expect(
      readRoutineList({
        routines: [
          {
            ...routine,
            enabled: false,
            schedule_status: "dispatch_failed",
            next_run_at: null,
          },
        ],
        paused: false,
      }).routines[0],
    ).toMatchObject({
      enabled: false,
      schedule_status: "dispatch_failed",
      next_run_at: null,
    });
  });

  it("rejects fabricated live-data status and unbounded response items", () => {
    expect(() =>
      readRoutineResult({ ...result, basis: "live_gmail" }),
    ).toThrow();
    expect(() =>
      readRoutineResult({ ...result, items: Array(16).fill(result.items[0]) }),
    ).toThrow();
    expect(() =>
      readRoutineList({ routines: [routine], paused: "false" }),
    ).toThrow();
    expect(() =>
      readRoutineList({
        routines: [{ ...routine, days_of_week: [8] }],
        paused: false,
      }),
    ).toThrow();
    expect(() =>
      readRoutinePreview({ ...preview, expires_at: "unknown" }),
    ).toThrow();
  });

  it("accepts skipped history without inventing a result", () => {
    const skipped = {
      id: "run-1",
      scheduled_for: result.checked_at,
      checked_at: result.checked_at,
      status: "skipped",
      result: null,
      notice: "NavoX is paused.",
    };
    expect(readRoutineRuns({ runs: [skipped] })).toEqual([skipped]);
    expect(() =>
      readRoutineRuns({ runs: [{ ...skipped, status: "sent" }] }),
    ).toThrow();
    expect(() => readRoutineRuns({ runs: Array(21).fill(skipped) })).toThrow();
  });

  it("formats schedule days using Monday-zero and renders missing times safely", () => {
    expect(routineSchedule(routineTemplate("friday_catch_up", "UTC"))).toBe(
      "Fri at 16:00 · UTC",
    );
    expect(routineTime("invalid")).toBe("Time unavailable");
    expect(routineTime(result.checked_at, "invalid-zone")).toBe(
      "Time unavailable",
    );
    expect(routineTime(result.checked_at, "UTC")).toContain("2026");
  });
});
