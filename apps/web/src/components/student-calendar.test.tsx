// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountSessionCallbacks } from "../lib/account-session";
import { selectAvailabilityOptions } from "../lib/student-availability-options";
import type { StudentAvailabilitySuggestionsApi } from "../lib/student-availability-suggestions-api";
import type {
  AvailabilityPreviewInput,
  AvailabilitySnapshot,
  CalendarView,
  StudentCalendarApi,
} from "../lib/student-calendar-api";
import { CalendarRequestError } from "../lib/student-calendar-api";
import { notifyCalendarAccessChanged } from "../lib/student-calendar-privacy";
import { StudentAvailability } from "./student-availability";
import {
  calendarConsent,
  StudentCalendarConnections,
} from "./student-calendar-connections";

const account = {
  id: "11111111-1111-4111-8111-111111111111",
  workspace: { id: "22222222-2222-4222-8222-222222222222" },
  email: "student@example.test",
  display_name: "Student",
};
const accountId = `${account.workspace.id}:${account.id}`;
const observers: AccountSessionCallbacks[] = [];
vi.mock("../lib/account-session", () => ({
  observeAccountSession(callbacks: AccountSessionCallbacks) {
    observers.push(callbacks);
    callbacks.checking();
    callbacks.restored({
      account: {
        ...account,
        workspace: {
          ...account.workspace,
          name: "Personal",
          workspace_type: "personal",
        },
      },
      connections: [],
      connectionsError: "",
    });
    return { dispose: vi.fn(), refresh: vi.fn() };
  },
}));

const now = Date.parse("2026-10-22T08:00:00Z");
const preferences = {
  wake_time: "07:00",
  sleep_time: "23:00",
  focus_windows: [],
  never_schedule: [
    { weekdays: [0, 2], start_time: "12:00", end_time: "13:00" },
  ],
  max_study_minutes: 90,
  break_minutes: 15,
  revision: 2,
  timezone: "UTC",
};
const calendarView: CalendarView = {
  generation: 3,
  revision: 4,
  consent_version: "calendar-availability.v1",
  providers: [
    { id: "google", availability: "available" },
    { id: "outlook", availability: "unavailable" },
    { id: "icloud", availability: "unavailable" },
  ],
  grants: [
    {
      id: "33333333-3333-4333-8333-333333333333",
      provider: "google",
      connection_id: "44444444-4444-4444-8444-444444444444",
      account_label: "calendar@example.test",
      status: "connected",
      revision: 1,
    },
  ],
  changes_applied: false,
};
function snapshot(request?: AvailabilityPreviewInput): AvailabilitySnapshot {
  const owned = { user_id: account.id, workspace_id: account.workspace.id };
  const interval = {
    start_at: "2026-10-22T09:00:00Z",
    end_at: "2026-10-22T09:50:00Z",
  };
  return {
    input: {
      ...owned,
      start_at: request?.start_at ?? "2026-10-22T09:00:00Z",
      end_at: request?.end_at ?? "2026-10-22T17:00:00Z",
      timezone: "UTC",
      now: new Date(now).toISOString(),
      preferences: {
        wake_time: "07:00",
        sleep_time: "23:00",
        focus_windows: [],
        never_schedule: [],
        max_study_minutes: 90,
        break_minutes: 15,
      },
      routine_confirmed: request?.routine_confirmed ?? true,
      required_source_ids: ["student-timetable"],
      coverage: [],
      busy: [],
      study_blocks: [],
      uncertainties: [],
      duration_minutes: request?.duration_minutes ?? 30,
      include_short_slots: false,
    },
    result: {
      timezone: "UTC",
      checked_at: new Date(now).toISOString(),
      scope: "provided_owned_sources_only",
      status: "complete",
      confirmed_openings: [
        {
          ...interval,
          minutes: 50,
          reasons: [],
          unchecked_source_ids: [],
          unknown_travel_record_ids: [],
          displaced_study_block_ids: [],
        },
      ],
      provisional_openings: [],
      busy: [],
      warnings: [],
      changes_applied: false,
    },
    checked_at: new Date(now).toISOString(),
    expires_at: new Date(now + 60_000).toISOString(),
    revision: {
      student_revision: "a".repeat(64),
      planning_revision: 2,
      preferences_revision: 2,
      availability_generation: 3,
      availability_revision: 4,
    },
    source_summary: [
      { source_id: "student-timetable", state: "complete", calendar_count: 0 },
    ],
    changes_applied: false,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { resolve, promise };
}
function api(): StudentCalendarApi {
  return {
    account: vi.fn(async () => accountId),
    preferences: vi.fn(async () => structuredClone(preferences)),
    calendars: vi.fn(async () => structuredClone(calendarView)),
    start: vi.fn(async () => ({
      authorization_url: "https://accounts.google.com/o/oauth2/v2/auth",
      requested_scopes: [],
      generation: 3,
      revision: 5,
      grant_id: calendarView.grants[0].id,
    })),
    disconnect: vi.fn(async () => ({
      ...structuredClone(calendarView),
      revision: 5,
      grants: [
        { ...calendarView.grants[0], status: "revoked" as const, revision: 2 },
      ],
    })),
    preview: vi.fn(async (request) => snapshot(request)),
  };
}
let host: HTMLDivElement;
let root: Root;
async function settle() {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}
function button(text: string) {
  const found = [...host.querySelectorAll("button")].find(
    (element) => element.textContent?.trim() === text,
  );
  if (!found) throw new Error(`Missing button ${text}`);
  return found;
}
async function click(text: string) {
  await act(async () => {
    button(text).click();
    await settle();
  });
}
async function select(label: string, value: string) {
  const found = [...host.querySelectorAll("label")].find((element) =>
    element.textContent?.includes(label),
  );
  const input = found?.querySelector("select");
  if (!input) throw new Error(`Missing selector ${label}`);
  await act(async () => {
    input.value = value;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await settle();
  });
}
async function mountAvailability(
  client: StudentCalendarApi,
  suppliedSuggestions?: StudentAvailabilitySuggestionsApi,
) {
  const suggestionsApi =
    suppliedSuggestions ??
    ({
      suggest: vi.fn(async (input, signal) => {
        const { purpose: _purpose, ...request } = input;
        const current = await client.preview(request, signal);
        const options = selectAvailabilityOptions(
          current.result,
          input.duration_minutes,
          Date.now(),
        );
        return {
          changes_applied: false as const,
          snapshot: current,
          selection: {
            status:
              current.result.status === "needs_routine"
                ? ("needs_routine" as const)
                : current.result.status === "partial"
                  ? ("partial_information" as const)
                  : options.length
                    ? ("ok" as const)
                    : ("no_time" as const),
            timezone: current.result.timezone,
            duration_minutes: input.duration_minutes,
            suggestions: options.map((item) => ({
              start_at: item.start_at,
              end_at: item.end_at,
              minutes: item.duration_minutes,
              reasons: [],
              displaced_study_block_ids: [] as [],
            })),
            alternatives_available: 0,
            changes_applied: false as const,
          },
        };
      }),
    } satisfies StudentAvailabilitySuggestionsApi);
  await act(async () => {
    root.render(
      <StudentAvailability
        accountId={accountId}
        timezone="UTC"
        api={client}
        suggestionsApi={suggestionsApi}
      />,
    );
    await settle();
  });
}
async function chooseAssumptions() {
  await select("Is this routine up to date?", "confirmed");
  await select("Travel around commitments", "zero");
}
beforeEach(() => {
  observers.length = 0;
  vi.useFakeTimers();
  vi.setSystemTime(now);
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network");
    }),
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("private calendar connection and availability screens", () => {
  it("shows server ranked options and sends purpose without creating an event", async () => {
    const client = api();
    const suggest = vi.fn<StudentAvailabilitySuggestionsApi["suggest"]>(
      async (input) => ({
        changes_applied: false as const,
        snapshot: snapshot(input),
        selection: {
          status: "ok",
          timezone: "UTC",
          duration_minutes: input.duration_minutes,
          suggestions: [
            {
              start_at: "2026-10-22T09:10:00Z",
              end_at: "2026-10-22T09:40:00Z",
              minutes: 30,
              reasons: ["soonest"],
              displaced_study_block_ids: [],
            },
          ],
          alternatives_available: 0,
          changes_applied: false,
        },
      }),
    );
    await mountAvailability(client, { suggest });
    await chooseAssumptions();
    await select("What are you making time for?", "coffee");
    await click("Find openings");
    expect(suggest).toHaveBeenCalledWith(
      expect.objectContaining({ purpose: "coffee", duration_minutes: 30 }),
      expect.any(AbortSignal),
    );
    expect(client.preview).not.toHaveBeenCalled();
    expect(host.textContent).toContain("Earliest suitable option");
    expect(host.textContent).toContain("9:10");
    expect(host.textContent).toContain(
      "No event, invitation, email or study-plan change was made",
    );
    await select("What are you making time for?", "lunch");
    expect(host.textContent).not.toContain("Earliest suitable option");
    const duration = [...host.querySelectorAll("label")]
      .find((label) => label.textContent?.includes("Meeting duration"))
      ?.querySelector("select");
    expect(duration?.value).toBe("60");
  });

  it("loads only saved routine metadata on mount and requires both explicit assumptions before provider reads", async () => {
    const client = api();
    await mountAvailability(client);
    expect(client.preferences).toHaveBeenCalledTimes(1);
    expect(client.preview).not.toHaveBeenCalled();
    expect(client.calendars).not.toHaveBeenCalled();
    expect(host.textContent).toContain("07:00–23:00");
    expect(host.textContent).toContain("Mon, Wed");
    expect(button("Find openings").disabled).toBe(true);
    await select("Is this routine up to date?", "confirmed");
    expect(button("Find openings").disabled).toBe(true);
    await select("Travel around commitments", "zero");
    expect(button("Find openings").disabled).toBe(false);
    await click("Find openings");
    expect(client.preview).toHaveBeenCalledWith(
      expect.objectContaining({
        duration_minutes: 30,
        preferences_revision: 2,
        expected_timezone: "UTC",
        routine_confirmed: true,
        travel_assumption: "zero",
      }),
      expect.any(AbortSignal),
    );
    expect(host.textContent).toContain("One time fits");
    expect(host.textContent).toContain("A second distinct option did not fit");
    expect(host.textContent).toContain(
      "10 minutes before and after kept clear",
    );
    expect(host.textContent).not.toContain("Option 2");
  });
  it("shows unknown coverage/travel/displaced study honestly and never promotes a provisional window", async () => {
    const client = api();
    const current = snapshot();
    current.result.provisional_openings = [
      {
        ...current.result.confirmed_openings[0],
        reasons: ["coverage_unknown", "travel_unknown", "movable_study"],
        unchecked_source_ids: ["google-calendar-availability"],
        unknown_travel_record_ids: ["meeting"],
        displaced_study_block_ids: ["study"],
      },
    ];
    current.result.confirmed_openings = [];
    current.result.status = "partial";
    current.source_summary.push({
      source_id: "google-calendar-availability",
      state: "unavailable",
      calendar_count: 0,
    });
    vi.mocked(client.preview).mockResolvedValue(current);
    await mountAvailability(client);
    await chooseAssumptions();
    await click("Find openings");
    expect(host.textContent).toContain("No confirmed option fits");
    expect(host.textContent).toContain("Travel time is unknown");
    expect(host.textContent).toContain(
      "Study work overlaps this window and has not been replanned",
    );
    expect(host.textContent).toContain(
      "Google calendar access: not fully checked",
    );
    expect(host.textContent).not.toContain("Option 1");
    expect(host.querySelector('a[href="/connections"]')).not.toBeNull();
  });
  it("expires displayed times and clears a previous result when search assumptions change", async () => {
    const client = api();
    await mountAvailability(client);
    await chooseAssumptions();
    await click("Find openings");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(host.textContent).not.toContain("One time fits");
    expect(host.textContent).toContain("These times need a fresh check");
    vi.setSystemTime(now);
    await click("Find openings");
    expect(host.textContent).toContain("One time fits");
    await select("Meeting duration", "15");
    expect(host.textContent).not.toContain("One time fits");
    expect(client.preview).toHaveBeenCalledTimes(2);
  });
  it("aborts a pending provider read and ignores its late response after account revocation", async () => {
    const client = api();
    const delayed = deferred<AvailabilitySnapshot>();
    vi.mocked(client.preview).mockReturnValue(delayed.promise);
    await mountAvailability(client);
    await chooseAssumptions();
    await click("Find openings");
    const signal = vi.mocked(client.preview).mock.calls[0][1];
    await act(async () => {
      for (const observer of observers) observer.revoking?.();
    });
    expect(signal.aborted).toBe(true);
    await act(async () => {
      delayed.resolve(snapshot());
      await settle();
    });
    expect(host.textContent).not.toContain("One time fits");
    expect(host.textContent).not.toContain("Your saved routine");
    expect(button("Find openings").disabled).toBe(true);
  });
  it("clears on calendar access changes and refuses a changed account proof around a response", async () => {
    const client = api();
    await mountAvailability(client);
    await chooseAssumptions();
    await click("Find openings");
    await act(async () => notifyCalendarAccessChanged());
    expect(host.textContent).not.toContain("One time fits");
    expect(host.textContent).toContain("Calendar access changed");
    vi.mocked(client.account)
      .mockResolvedValueOnce(accountId)
      .mockResolvedValueOnce("different-account");
    await click("Find openings");
    expect(host.textContent).not.toContain("One time fits");
    expect(host.textContent).not.toContain("Your saved routine");
    expect(host.textContent).toContain("Your account could not be verified");
  });
  it("does not treat unsaved default hours as a confirmed routine", async () => {
    const client = api();
    vi.mocked(client.preferences).mockResolvedValue({
      ...preferences,
      revision: 0,
    });
    await mountAvailability(client);
    expect(host.textContent).toContain("These are default hours");
    const option = host.querySelector(
      'option[value="confirmed"]',
    ) as HTMLOptionElement;
    expect(option.disabled).toBe(true);
    await select("Is this routine up to date?", "unknown");
    await select("Travel around commitments", "unknown");
    await click("Find openings");
    expect(client.preview).toHaveBeenCalledWith(
      expect.objectContaining({
        routine_confirmed: false,
        preferences_revision: 0,
        travel_assumption: "unknown",
      }),
      expect.any(AbortSignal),
    );
  });
  it("requires separate calendar purpose consent and retires a late OAuth response after revocation", async () => {
    const client = api();
    const delayed =
      deferred<Awaited<ReturnType<StudentCalendarApi["start"]>>>();
    vi.mocked(client.start).mockReturnValue(delayed.promise);
    await act(async () => {
      root.render(
        <StudentCalendarConnections accountId={accountId} api={client} />,
      );
      await settle();
    });
    expect(client.calendars).not.toHaveBeenCalled();
    expect(client.preview).not.toHaveBeenCalled();
    await click("Check calendar access");
    expect(host.textContent).toContain(calendarConsent);
    expect(host.textContent).toContain("Outlook / Microsoft 365");
    expect(host.textContent).toContain("Apple / iCloud");
    expect(button("Connect Google Calendar").disabled).toBe(true);
    const checkbox = host.querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement;
    await act(async () => checkbox.click());
    await click("Connect Google Calendar");
    expect(client.start).toHaveBeenCalledWith(
      expect.objectContaining({
        consent_version: "calendar-availability.v1",
        confirmed: true,
        generation: 3,
        revision: 4,
      }),
      expect.any(AbortSignal),
    );
    await act(async () => {
      for (const observer of observers) observer.revoking?.();
    });
    expect(vi.mocked(client.start).mock.calls[0][1].aborted).toBe(true);
    await act(async () => {
      delayed.resolve({
        authorization_url: "https://accounts.google.com/o/oauth2/v2/auth",
        requested_scopes: [],
        generation: 3,
        revision: 5,
        grant_id: calendarView.grants[0].id,
      });
      await settle();
    });
    expect(host.textContent).not.toContain("calendar@example.test");
    expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  });
  it("rejects a snapshot that expires during the final account proof", async () => {
    const client = api();
    await mountAvailability(client);
    await chooseAssumptions();
    const proof = deferred<string>();
    vi.mocked(client.account)
      .mockResolvedValueOnce(accountId)
      .mockReturnValueOnce(proof.promise);
    await click("Find openings");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
      proof.resolve(accountId);
      await settle();
    });
    expect(host.textContent).not.toContain("One time fits");
    expect(host.textContent).toContain("This check expired before it finished");
  });

  it("clears and aborts pending reads on page exit without displaying a late result", async () => {
    const client = api();
    const delayed = deferred<AvailabilitySnapshot>();
    vi.mocked(client.preview).mockReturnValue(delayed.promise);
    await mountAvailability(client);
    await chooseAssumptions();
    await click("Find openings");
    await act(async () => window.dispatchEvent(new Event("pagehide")));
    expect(vi.mocked(client.preview).mock.calls[0][1].aborted).toBe(true);
    await act(async () => {
      delayed.resolve(snapshot());
      await settle();
    });
    expect(host.textContent).not.toContain("One time fits");
    expect(host.textContent).not.toContain("Your saved routine");
  });

  it("never carries a previous account's connection labels into a replacement account render", async () => {
    const client = api();
    await act(async () => {
      root.render(
        <StudentCalendarConnections accountId={accountId} api={client} />,
      );
      await settle();
    });
    await click("Check calendar access");
    expect(host.textContent).toContain("calendar@example.test");
    await act(async () => {
      root.render(
        <StudentCalendarConnections
          accountId={`${account.workspace.id}:55555555-5555-4555-8555-555555555555`}
          api={client}
        />,
      );
      await settle();
    });
    expect(host.textContent).not.toContain("calendar@example.test");
    expect(host.textContent).toContain("The account changed");
    expect(button("Check calendar access").disabled).toBe(true);
  });

  it("retires stale connection status and consent after a revision conflict", async () => {
    const client = api();
    vi.mocked(client.disconnect).mockRejectedValue(
      new CalendarRequestError("account-changed", 409),
    );
    await act(async () => {
      root.render(
        <StudentCalendarConnections accountId={accountId} api={client} />,
      );
      await settle();
    });
    await click("Check calendar access");
    await click("Stop availability access");
    expect(host.textContent).not.toContain("calendar@example.test");
    expect(host.querySelector('input[type="checkbox"]')).toBeNull();
    expect(host.textContent).toContain("Refresh before continuing");
    expect(button("Check calendar access").disabled).toBe(false);
  });

  it("stops exact calendar access without making a provider read or calendar write", async () => {
    const client = api();
    await act(async () => {
      root.render(
        <StudentCalendarConnections accountId={accountId} api={client} />,
      );
      await settle();
    });
    await click("Check calendar access");
    await click("Stop availability access");
    expect(client.disconnect).toHaveBeenCalledWith(
      calendarView.grants[0].id,
      expect.objectContaining({ generation: 3, revision: 4 }),
      expect.any(AbortSignal),
    );
    expect(host.textContent).toContain("Availability access is off");
    expect(host.textContent).toContain("Calendar availability access stopped");
    expect(client.preview).not.toHaveBeenCalled();
    expect(client.start).not.toHaveBeenCalled();
  });
});
