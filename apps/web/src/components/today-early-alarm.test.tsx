// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultAlarm, routineRequest } from "../lib/student-planning";
import { studentRequest } from "../lib/student-schedule";
import { TodayEarlyAlarm } from "./today-early-alarm";

vi.mock("../lib/student-planning", async (original) => ({
  ...(await original<typeof import("../lib/student-planning")>()),
  routineRequest: vi.fn(),
}));
vi.mock("../lib/student-schedule", async (original) => ({
  ...(await original<typeof import("../lib/student-schedule")>()),
  studentRequest: vi.fn(),
}));
const preferences = {
  timezone: "America/New_York",
  revision: 2,
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
const preview = {
  timezone: preferences.timezone,
  preview_hash: "a".repeat(64),
  native_ringing_available: false as const,
  alarm_occurrences: [
    { ...alarm, scheduled_at: "2026-10-25T11:55:00Z" },
    { ...alarm, day: "2026-10-26", scheduled_at: "2026-10-26T11:30:00Z" },
  ],
  previous_alarm_occurrences: [
    alarm,
    { ...alarm, day: "2026-10-26", scheduled_at: "2026-10-26T12:00:00Z" },
  ],
  warnings: [] as string[],
  replaced_blocks: [],
};
const timetable = {
  timezone: preferences.timezone,
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
      timezone: preferences.timezone,
      building: "Statler",
      room: "101",
      instructor: null,
      walk_minutes: 15,
      cancelled: false,
      exception: false,
    },
  ],
};
const props = {
  accountKey: "personal:student",
  alarm,
  timezone: preferences.timezone,
  now: Date.parse(timetable.server_now),
  onSaved: vi.fn(),
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.mocked(studentRequest).mockResolvedValue(timetable);
  vi.mocked(routineRequest).mockImplementation(async (_owner, path) =>
    path.endsWith("/preview") ? preview : preferences,
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});
function button(text: string) {
  const result = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
    (item) => item.textContent === text,
  );
  if (!result) throw new Error(`Missing ${text}`);
  return result;
}
async function click(text: string) {
  await act(async () => button(text).click());
}
describe("Today early exam alarm approval", () => {
  it("shows a real proposal without saving, then reviews every affected day before confirmation", async () => {
    await act(async () => root.render(<TodayEarlyAlarm {...props} />));
    expect(host.textContent).toContain("An early exam");
    expect(host.textContent).toContain("7:55 AM");
    expect(
      vi
        .mocked(routineRequest)
        .mock.calls.every(([, , , method]) => !method || method === "GET"),
    ).toBe(true);
    await click("Review alarm change");
    expect(host.textContent).toContain("2026-10-25");
    expect(host.textContent).toContain("2026-10-26");
    expect(props.onSaved).not.toHaveBeenCalled();
    const request = vi
      .mocked(routineRequest)
      .mock.calls.find(([, path]) => path.endsWith("/preview"))?.[4];
    await click("Approve these changes");
    const confirm = vi
      .mocked(routineRequest)
      .mock.calls.find(([, path]) => path.endsWith("/confirm"));
    expect(confirm?.[0]).toBe(props.accountKey);
    expect(confirm?.[4]).toEqual({
      ...(request as object),
      preview_hash: preview.preview_hash,
    });
    expect(props.onSaved).toHaveBeenCalledOnce();
  });
  it("cannot approve an uncertain smart alarm, and Keep never saves", async () => {
    vi.mocked(routineRequest).mockImplementation(async (_owner, path) =>
      path.endsWith("/preview")
        ? {
            ...preview,
            warnings: ["Walk time is missing for another class day"],
          }
        : preferences,
    );
    await act(async () => root.render(<TodayEarlyAlarm {...props} />));
    await click("Review alarm change");
    expect(button("Approve these changes").disabled).toBe(true);
    await click("Keep current settings");
    expect(
      vi
        .mocked(routineRequest)
        .mock.calls.some(([, path]) => path.endsWith("/confirm")),
    ).toBe(false);
    expect(host.textContent).toBe("");
  });
  it("does not expose a former account’s delayed preview after the owner changes", async () => {
    let resolve!: (value: unknown) => void;
    await act(async () => root.render(<TodayEarlyAlarm {...props} />));
    vi.mocked(routineRequest).mockImplementation(async (owner, path) => {
      if (path.endsWith("/preview"))
        return new Promise((done) => {
          resolve = done;
        });
      return owner === props.accountKey
        ? preferences
        : { ...preferences, alarm: { ...preferences.alarm, enabled: false } };
    });
    await click("Review alarm change");
    await act(async () =>
      root.render(<TodayEarlyAlarm {...props} accountKey="personal:other" />),
    );
    await act(async () => resolve(preview));
    expect(host.textContent).toBe("");
    expect(props.onSaved).not.toHaveBeenCalled();
  });
});
