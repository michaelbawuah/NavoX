// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { InboxMeetingSource } from "../lib/inbox-meeting-proposal";
import type { AvailabilitySnapshot } from "../lib/student-calendar-api";
import { CALENDAR_ACCESS_CHANGED_EVENT } from "../lib/student-calendar-privacy";
import { InboxMeetingCheck } from "./inbox-meeting-check";

const mocks = vi.hoisted(() => ({
  owner: "workspace:student",
  api: { account: vi.fn(), preferences: vi.fn(), preview: vi.fn() },
}));
vi.mock("../lib/student-calendar-api", async (original) => ({
  ...(await original<typeof import("../lib/student-calendar-api")>()),
  createStudentCalendarApi: () => mocks.api,
}));
vi.mock("../lib/account-session", () => ({
  observeAccountSession: (callbacks: {
    restored: (access: {
      account: { id: string; workspace: { id: string } };
    }) => void;
  }) => {
    let live = true;
    const refresh = () => {
      const [workspace, user] = mocks.owner.split(":");
      void Promise.resolve().then(() => {
        if (live)
          callbacks.restored({
            account: { id: user, workspace: { id: workspace } },
          });
      });
    };
    refresh();
    return {
      refresh,
      dispose: () => {
        live = false;
      },
    };
  },
}));

const now = Date.parse("2026-10-24T12:00:00Z");
const source: InboxMeetingSource = {
  accountKey: "workspace:student",
  key: "google:item:source",
  subject: "Office hours",
  body: "Would Monday at 2 PM work for a 30-minute meeting?",
  sentAt: "2026-10-24T10:00:00Z",
  truncated: false,
};
const preferences = {
  wake_time: "07:00",
  sleep_time: "23:00",
  focus_windows: [],
  never_schedule: [],
  max_study_minutes: 90,
  break_minutes: 15,
  wake_times: Array(7).fill("07:00"),
  travel_minutes: 0,
  alarm: {
    enabled: false,
    days: [0, 1, 2, 3, 4],
    smart: false,
    prep_minutes: 30,
    sound: "gentle" as const,
    snooze_minutes: 5 as const,
    skip_date: null,
  },
};
function snapshot(): AvailabilitySnapshot {
  return {
    input: {
      start_at: "2026-10-26T17:50:00Z",
      end_at: "2026-11-03T04:59:00Z",
      user_id: "student",
      workspace_id: "workspace",
      timezone: "America/New_York",
      now: new Date(now).toISOString(),
      preferences,
      routine_confirmed: true,
      required_source_ids: ["student", "google-calendar"],
      coverage: [],
      busy: [],
      study_blocks: [],
      uncertainties: [],
      duration_minutes: 30,
      include_short_slots: true,
    },
    result: {
      timezone: "America/New_York",
      checked_at: new Date(now).toISOString(),
      scope: "provided_owned_sources_only",
      status: "complete",
      confirmed_openings: [
        {
          start_at: "2026-10-26T19:10:00Z",
          end_at: "2026-10-26T20:00:00Z",
          minutes: 50,
          reasons: [],
          unchecked_source_ids: [],
          unknown_travel_record_ids: [],
          displaced_study_block_ids: [],
        },
        {
          start_at: "2026-10-27T12:00:00Z",
          end_at: "2026-10-27T14:00:00Z",
          minutes: 120,
          reasons: [],
          unchecked_source_ids: [],
          unknown_travel_record_ids: [],
          displaced_study_block_ids: [],
        },
      ],
      provisional_openings: [],
      busy: [
        { start_at: "2026-10-26T17:00:00Z", end_at: "2026-10-26T19:00:00Z" },
      ],
      warnings: [],
      changes_applied: false,
    },
    checked_at: new Date(now).toISOString(),
    expires_at: new Date(now + 60_000).toISOString(),
    revision: {
      student_revision: "a".repeat(64),
      planning_revision: 1,
      preferences_revision: 5,
      availability_generation: 1,
      availability_revision: 1,
    },
    source_summary: [
      { source_id: "google-calendar", state: "complete", calendar_count: 1 },
    ],
    changes_applied: false,
  };
}
let host: HTMLDivElement, root: Root;
const onUseTimes = vi.fn();
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
  vi.setSystemTime(now);
  mocks.owner = "workspace:student";
  mocks.api.account.mockReset().mockImplementation(async () => mocks.owner);
  mocks.api.preferences.mockReset().mockResolvedValue({
    timezone: "America/New_York",
    revision: 5,
    preferences,
  });
  mocks.api.preview.mockReset().mockResolvedValue(snapshot());
  onUseTimes.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
async function render(
  value: InboxMeetingSource | null = source,
  accountKey = "workspace:student",
) {
  await act(async () =>
    root.render(
      <InboxMeetingCheck
        accountKey={accountKey}
        timezone="America/New_York"
        disabled={false}
        source={value}
        onUseTimes={onUseTimes}
      />,
    ),
  );
}
function button(text: string) {
  return [...host.querySelectorAll("button")].find(
    (element) => element.textContent === text,
  )!;
}
async function reviewedCheck() {
  await act(async () => {
    for (const box of host.querySelectorAll<HTMLInputElement>(
      'input[type="checkbox"]',
    ))
      box.click();
  });
  await act(async () => button("Check this time").click());
}
function edit(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
describe("meeting review linked to an opened email", () => {
  it("shows source evidence before an explicit review and performs no availability request on read", async () => {
    await render();
    expect(host.querySelector("blockquote")?.textContent).toBe(source.body);
    expect(
      host.querySelector<HTMLInputElement>('input[type="datetime-local"]')
        ?.value,
    ).toBe("2026-10-26T14:00");
    expect(button("Check this time").disabled).toBe(true);
    expect(mocks.api.preferences).not.toHaveBeenCalled();
    expect(mocks.api.preview).not.toHaveBeenCalled();
    expect(onUseTimes).not.toHaveBeenCalled();
  });
  it("checks fresh saved preferences, shows the actual clash and offers at most two real alternatives", async () => {
    await render();
    await reviewedCheck();
    expect(mocks.api.account).toHaveBeenCalledTimes(2);
    expect(mocks.api.preferences).toHaveBeenCalledTimes(1);
    expect(mocks.api.preview).toHaveBeenCalledWith(
      expect.objectContaining({
        start_at: "2026-10-26T17:50:00.000Z",
        duration_minutes: 30,
        preferences_revision: 5,
        expected_timezone: "America/New_York",
        routine_confirmed: true,
        travel_assumption: "zero",
      }),
      expect.any(AbortSignal),
    );
    expect(host.textContent).toContain("conflicts with your saved schedule");
    expect(host.textContent).toContain("Two checked alternatives");
    await act(async () =>
      button("Use these times in reply instructions").click(),
    );
    expect(onUseTimes).toHaveBeenCalledTimes(1);
    expect(onUseTimes.mock.calls[0][0]).toContain(
      "Mon, Oct 26, 3:20 PM–3:50 PM or Tue, Oct 27, 8:10 AM–8:40 AM",
    );
    expect(onUseTimes.mock.calls[0][0]).toContain(
      "Do not invent personal reasons",
    );
    expect(host.textContent).toContain("No calendar event is created");
  });
  it("retires the review and checked choices after a correction", async () => {
    await render();
    await reviewedCheck();
    act(() =>
      edit(
        host.querySelector<HTMLInputElement>('input[type="datetime-local"]')!,
        "2026-10-27T15:00",
      ),
    );
    expect(button("Check this time").disabled).toBe(true);
    expect(host.textContent).not.toContain("Two checked alternatives");
    expect(onUseTimes).not.toHaveBeenCalled();
  });
  it("does not start calendar reads after the source changed while preferences were loading", async () => {
    let finish!: (value: unknown) => void;
    mocks.api.preferences.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    await render();
    await reviewedCheck();
    const signal = mocks.api.preferences.mock.calls[0][0] as AbortSignal;
    await render({
      ...source,
      key: "google:other:source",
      body: "Can we meet on Tuesday at 3pm?",
    });
    expect(signal.aborted).toBe(true);
    await act(async () =>
      finish({ timezone: "America/New_York", revision: 5, preferences }),
    );
    expect(mocks.api.preview).not.toHaveBeenCalled();
    expect(
      host.querySelector<HTMLInputElement>('input[type="datetime-local"]')
        ?.value,
    ).toBe("2026-10-27T15:00");
    expect(button("Check this time").disabled).toBe(true);
  });
  it.each(["source", "owner"])(
    "discards late availability after a %s switch",
    async (change) => {
      let finish!: (value: AvailabilitySnapshot) => void;
      mocks.api.preview.mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
      await render();
      await reviewedCheck();
      const signal = mocks.api.preview.mock.calls[0][1] as AbortSignal;
      if (change === "owner") {
        mocks.owner = "workspace:new";
        await render(source, mocks.owner);
      } else
        await render({
          ...source,
          key: "google:other:source",
          body: "Can we meet on Tuesday at 3pm?",
        });
      expect(signal.aborted).toBe(true);
      await act(async () => finish(snapshot()));
      expect(host.textContent).not.toContain(
        "conflicts with your saved schedule",
      );
      expect(host.textContent).not.toContain("Two checked alternatives");
      expect(host.textContent).not.toContain(source.body);
      expect(onUseTimes).not.toHaveBeenCalled();
      expect(button("Check this time").disabled).toBe(true);
    },
  );
  it("never suggests unconfirmed openings as free alternatives", async () => {
    const result = snapshot();
    result.result.confirmed_openings = [];
    result.result.status = "partial";
    mocks.api.preview.mockResolvedValue(result);
    await render();
    await reviewedCheck();
    expect(host.textContent).toContain("No confirmed alternative");
    expect(
      [...host.querySelectorAll("button")].some((element) =>
        element.textContent?.startsWith("Use "),
      ),
    ).toBe(false);
  });
  it("retires checked options on expiration or a calendar access change", async () => {
    await render();
    await reviewedCheck();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(host.textContent).not.toContain("Two checked alternatives");
    expect(host.textContent).toContain("fresh check");
    mocks.api.preview.mockResolvedValue({
      ...snapshot(),
      expires_at: new Date(Date.now() + 60_000).toISOString(),
    });
    await act(async () => button("Check this time").click());
    expect(host.textContent).toContain("Two checked alternatives");
    act(() => window.dispatchEvent(new Event(CALENDAR_ACCESS_CHANGED_EVENT)));
    expect(host.textContent).not.toContain("Two checked alternatives");
    expect(button("Check this time").disabled).toBe(true);
  });
  it("stops when the saved timezone changed instead of checking in the old zone", async () => {
    mocks.api.preferences.mockResolvedValue({
      timezone: "America/Los_Angeles",
      revision: 6,
      preferences,
    });
    await render();
    await reviewedCheck();
    expect(host.textContent).toContain("Your timezone changed");
    expect(mocks.api.preview).not.toHaveBeenCalled();
  });
});
