// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { defaultAlarm } from "../lib/student-planning";
import { RoutineAlarmSettings } from "./routine-alarm-settings";

const prefs = {
  wake_time: "07:00",
  sleep_time: "23:00",
  wake_times: null,
  alarm: defaultAlarm,
  focus_windows: [],
  never_schedule: [],
  max_study_minutes: 90,
  break_minutes: 15,
  revision: 0,
  timezone: "America/New_York",
};
const row = {
  day: "2026-10-24",
  enabled: true,
  scheduled_at: "2026-10-24T11:00:00Z",
  reason: "Your saved wake time",
  first_class_at: null,
  first_class_title: null,
};
const preview = {
  preview_hash: "a".repeat(64),
  timezone: prefs.timezone,
  native_ringing_available: false,
  warnings: [],
  alarm_occurrences: [row],
  previous_alarm_occurrences: [{ ...row, enabled: false }],
  replaced_blocks: [
    {
      id: "11111111-1111-4111-8111-111111111111",
      task_id: "22222222-2222-4222-8222-222222222222",
      plan_id: "33333333-3333-4333-8333-333333333333",
      title: "Review calculus",
      start_at: "2026-10-24T13:00:00Z",
      end_at: "2026-10-24T14:00:00Z",
      minutes: 60,
      focus_window: false,
      revision: 1,
      status: "planned",
      actual_minutes: 0,
      outcome_at: null,
    },
  ],
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      Response.json(url.endsWith("/preview") ? preview : prefs),
    ),
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
async function mount() {
  await act(async () =>
    root.render(<RoutineAlarmSettings accountKey="workspace:student" />),
  );
}
async function review() {
  await act(async () =>
    host
      .querySelector("form")
      ?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
}
async function click(label: string) {
  const b = [...host.querySelectorAll("button")].find(
    (b) => b.textContent === label,
  );
  if (!b) throw Error(`Missing ${label}`);
  await act(async () => b.click());
}
it("reads back every affected block and confirms only after explicit approval", async () => {
  await mount();
  await review();
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("Review calculus");
  expect(host.textContent).toContain("Off →");
  expect(host.textContent).toContain("retire these future study blocks");
  await click("Approve these changes");
  const [url, options] = vi.mocked(fetch).mock.calls[2];
  expect(url).toMatch(/preferences\/confirm$/);
  expect(options?.headers).toMatchObject({
    "X-NavoX-Account": "workspace:student",
  });
  expect(JSON.parse(String(options?.body)).preview_hash).toBe(
    preview.preview_hash,
  );
  expect(host.textContent).toContain(
    "cannot ring while your browser is closed",
  );
});
it("cancel never saves, and unresolved time warnings disable approval", async () => {
  await mount();
  await review();
  await click("Keep current settings");
  expect(fetch).toHaveBeenCalledTimes(2);
  vi.mocked(fetch).mockResolvedValueOnce(
    Response.json({
      ...preview,
      warnings: ["Timetable clock change needs review"],
    }),
  );
  await review();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain(
    "clock change",
  );
  expect(
    [...host.querySelectorAll("button")].find(
      (b) => b.textContent === "Approve these changes",
    )?.disabled,
  ).toBe(true);
});
it("ignores an old account response after switching owners", async () => {
  let finish!: (r: Response) => void;
  vi.mocked(fetch).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await mount();
  await act(async () =>
    root.render(<RoutineAlarmSettings accountKey="new:student" />),
  );
  await act(async () =>
    finish(Response.json({ ...prefs, wake_time: "04:00" })),
  );
  expect(
    (host.querySelector('input[type="time"]') as HTMLInputElement).value,
  ).toBe("07:00");
});
