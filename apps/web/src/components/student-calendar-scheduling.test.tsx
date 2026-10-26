// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountSessionCallbacks } from "../lib/account-session";
import { productFeatures } from "../lib/product-features";
import type {
  AvailabilityPreviewInput,
  CalendarProviderId,
} from "../lib/student-calendar-api";
import { notifyCalendarAccessChanged } from "../lib/student-calendar-privacy";
import type {
  CalendarEventView,
  StudentCalendarSchedulingApi,
} from "../lib/student-calendar-scheduling";
import {
  calendarSchedulingConsent,
  StudentCalendarScheduling,
  StudentCalendarSchedulingConsent,
} from "./student-calendar-scheduling";

const account = {
  id: "11111111-1111-4111-8111-111111111111",
  workspace: {
    id: "22222222-2222-4222-8222-222222222222",
    name: "Personal",
    workspace_type: "personal",
  },
  email: "student@example.test",
  display_name: "Student",
};
const accountId = `${account.workspace.id}:${account.id}`;
const grant = "33333333-3333-4333-8333-333333333333",
  connection = "44444444-4444-4444-8444-444444444444",
  action = "55555555-5555-4555-8555-555555555555";
const observers: AccountSessionCallbacks[] = [];
vi.mock("../lib/account-session", () => ({
  observeAccountSession(callbacks: AccountSessionCallbacks) {
    observers.push(callbacks);
    callbacks.checking();
    callbacks.restored({ account, connections: [], connectionsError: "" });
    return { dispose: vi.fn(), refresh: vi.fn() };
  },
}));
vi.mock("../lib/product-features", () => ({
  productFeatures: {
    studentCalendarAvailability: true,
    studentCalendarWrites: true,
  },
}));
const now = Date.parse("2026-10-22T12:00:00Z");
const availability: AvailabilityPreviewInput = {
  start_at: "2026-10-22T12:00:00Z",
  end_at: "2026-10-22T18:00:00Z",
  duration_minutes: 30,
  preferences_revision: 2,
  expected_timezone: "UTC",
  routine_confirmed: true,
  travel_assumption: "zero",
  include_short_slots: false,
};
const options = [
  {
    start_at: "2026-10-22T13:00:00Z",
    end_at: "2026-10-22T13:30:00Z",
    reserved_start_at: "2026-10-22T12:50:00Z",
    reserved_end_at: "2026-10-22T13:40:00Z",
    duration_minutes: 30,
    buffer_before_minutes: 10 as const,
    buffer_after_minutes: 10 as const,
  },
];
function event(): CalendarEventView {
  return {
    generation: 2,
    calendar_revision: 5,
    id: action,
    revision: 1,
    digest: "a".repeat(64),
    status: "pending",
    calendar_label: "My calendar",
    title: "Study with Sam",
    location: "Library",
    start_at: options[0].start_at,
    end_at: options[0].end_at,
    timezone: "UTC",
    expires_at: "2026-10-22T12:10:00Z",
    changes_applied: false,
    can_undo: false,
    message: "Review",
  };
}
function studyMove(index = 1) {
  return {
    block_id: `70000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    task_id: `80000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    title: `Synthetic study block ${index}`,
    minutes: 30,
    before_start_at: "2026-10-22T12:50:00Z",
    before_end_at: "2026-10-22T13:20:00Z",
    after_start_at: "2026-10-22T14:00:00Z",
    after_end_at: "2026-10-22T14:30:00Z",
  };
}
function client(): StudentCalendarSchedulingApi {
  return {
    account: vi.fn(async () => accountId),
    preferences: vi.fn(),
    preview: vi.fn(),
    start: vi.fn(),
    disconnect: vi.fn(),
    calendars: vi.fn<StudentCalendarSchedulingApi["calendars"]>(async () => ({
      generation: 2,
      revision: 5,
      consent_version: "calendar-availability.v1",
      providers: [
        { id: "google", availability: "available" },
        { id: "outlook", availability: "unavailable" },
        { id: "icloud", availability: "unavailable" },
      ],
      grants: [
        {
          id: grant,
          provider: "google",
          connection_id: connection,
          account_label: "student@example.test",
          status: "connected",
          revision: 1,
        },
      ],
      changes_applied: false,
    })),
    scheduling: vi.fn<StudentCalendarSchedulingApi["scheduling"]>(async () => ({
      generation: 2,
      revision: 5,
      consent_version: "calendar-scheduling.v1",
      grants: [
        {
          id: grant,
          provider: "google",
          connection_id: connection,
          account_label: "student@example.test",
          status: "connected",
          revision: 1,
        },
      ],
      changes_applied: false,
    })),
    startScheduling: vi.fn(),
    startOutlookScheduling: vi.fn(),
    startAppleScheduling: vi.fn(),
    stopScheduling: vi.fn<StudentCalendarSchedulingApi["stopScheduling"]>(
      async () => ({
        generation: 2,
        revision: 6,
        consent_version: "calendar-scheduling.v1",
        grants: [],
        changes_applied: false,
      }),
    ),
    targets: vi.fn<StudentCalendarSchedulingApi["targets"]>(async () => ({
      generation: 2,
      revision: 5,
      grant_id: grant,
      targets: [{ id: "primary", label: "My calendar", timezone: "UTC" }],
      changes_applied: false,
    })),
    previewEvent: vi.fn(async () => event()),
    event: vi.fn(async () => event()),
    events: vi.fn(async () => ({
      generation: 2,
      calendar_revision: 5,
      next_cursor: null,
      events: [],
    })),
    approveEvent: vi.fn<StudentCalendarSchedulingApi["approveEvent"]>(
      async () => ({
        ...event(),
        status: "created",
        revision: 2,
        changes_applied: true,
        can_undo: true,
      }),
    ),
    reconcileEvent: vi.fn<StudentCalendarSchedulingApi["reconcileEvent"]>(
      async () => ({
        ...event(),
        status: "created",
        revision: 2,
        changes_applied: true,
        can_undo: true,
      }),
    ),
    undoEvent: vi.fn<StudentCalendarSchedulingApi["undoEvent"]>(async () => ({
      ...event(),
      status: "undone",
      revision: 3,
      changes_applied: false,
      can_undo: false,
    })),
  };
}
async function providerClient(provider: CalendarProviderId, available = true) {
  const api = client(),
    signal = new AbortController().signal;
  const scheduling = await api.scheduling(signal),
    calendars = await api.calendars(signal);
  vi.mocked(api.scheduling).mockResolvedValue({
    ...scheduling,
    grants: scheduling.grants.map((grant) => ({ ...grant, provider })),
    providers: ["google", "outlook", "icloud"].map((id) => ({
      id: id as CalendarProviderId,
      availability: id === provider && available ? "available" : "unavailable",
      message:
        id === provider && !available
          ? "Exact provider write qualification is unavailable."
          : "Independent calendar scheduling permission.",
    })),
  });
  vi.mocked(api.calendars).mockResolvedValue({
    ...calendars,
    grants: calendars.grants.map((grant) => ({ ...grant, provider })),
  });
  vi.mocked(api.targets).mockResolvedValue({
    generation: 2,
    revision: 5,
    grant_id: grant,
    provider,
    targets: [{ id: "primary", label: "My calendar", timezone: "UTC" }],
    changes_applied: false,
  });
  vi.mocked(api.previewEvent).mockResolvedValue({ ...event(), provider });
  vi.mocked(api.approveEvent).mockResolvedValue({
    ...event(),
    provider,
    status: "created",
    revision: 2,
    changes_applied: true,
    can_undo: true,
  });
  vi.mocked(api.undoEvent).mockResolvedValue({
    ...event(),
    provider,
    status: "undone",
    revision: 3,
    changes_applied: false,
  });
  return api;
}
let host: HTMLDivElement, root: Root;
async function settle() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}
function button(label: string) {
  const found = [...host.querySelectorAll("button")].find(
    (e) => e.textContent?.trim() === label,
  );
  if (!found) throw new Error(`Missing button ${label}`);
  return found;
}
async function click(label: string) {
  await act(async () => {
    button(label).click();
    await settle();
  });
}
function field(label: string) {
  const found = [...host.querySelectorAll("label")].find((e) =>
    e.textContent?.startsWith(label),
  );
  const input = found?.querySelector("input,select");
  if (!input) throw new Error(`Missing field ${label}`);
  return input as HTMLInputElement | HTMLSelectElement;
}
async function set(label: string, value: string) {
  await act(async () => {
    const input = field(label);
    Object.getOwnPropertyDescriptor(
      input instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype,
      "value",
    )?.set?.call(input, value);
    input.dispatchEvent(
      new Event(input instanceof HTMLSelectElement ? "change" : "input", {
        bubbles: true,
      }),
    );
    await settle();
  });
}
async function check(label: string) {
  await act(async () => {
    (field(label) as HTMLInputElement).click();
    await settle();
  });
}
async function mount(api: StudentCalendarSchedulingApi, choices = options) {
  await act(async () => {
    root.render(
      <StudentCalendarScheduling
        accountId={accountId}
        options={choices}
        availability={availability}
        api={api}
      />,
    );
    await settle();
  });
}
async function prepare(
  api: StudentCalendarSchedulingApi,
  choices = options,
  accountField = "Google account",
) {
  await mount(api, choices);
  await click("Check scheduling and recent actions");
  await set(accountField, grant);
  await set("Add to calendar", "primary");
  await set("Checked time", options[0].start_at);
  await set("Event title", "Study with Sam");
  await set("Location (optional)", "Library");
  await click("Review exact event");
}
beforeEach(() => {
  observers.length = 0;
  productFeatures.studentCalendarAvailability = true;
  productFeatures.studentCalendarWrites = true;
  vi.useFakeTimers();
  vi.setSystemTime(now);
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
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

describe("reviewed calendar scheduling screen", () => {
  it("distinguishes both offsets in an exact receipt across a repeated DST hour", async () => {
    const api = client();
    vi.mocked(api.events).mockResolvedValue({
      generation: 2,
      calendar_revision: 5,
      next_cursor: null,
      events: [
        {
          ...event(),
          status: "created",
          changes_applied: true,
          provider: "outlook",
          start_at: "2026-11-01T01:30:00-04:00",
          end_at: "2026-11-01T01:15:00-05:00",
          timezone: "America/New_York",
        },
      ],
    });
    await mount(api);
    await click("Check scheduling and recent actions");
    await click("Study with Sam · Added");
    const receipt = host.querySelector("article");
    expect(receipt?.textContent).toContain("1:30 AM EDT");
    expect(receipt?.textContent).toContain("1:15 AM EST");
    expect(receipt?.textContent).toContain("America/New_York");
    expect(api.approveEvent).not.toHaveBeenCalled();
  });
  it("does not infer Microsoft write support from connected busy-time access", async () => {
    const api = await providerClient("outlook", false);
    await act(async () =>
      root.render(
        <StudentCalendarSchedulingConsent accountId={accountId} api={api} />,
      ),
    );
    await click("Check scheduling access");
    await set("Calendar service", "outlook");
    expect(host.textContent).toContain(
      "Exact provider write qualification is unavailable",
    );
    expect(host.textContent).toContain(
      "Microsoft reviewed scheduling is unavailable here",
    );
    expect(
      [...host.querySelectorAll("button")].some(
        (node) => node.textContent?.trim() === "Connect reviewed scheduling",
      ),
    ).toBe(false);
    expect(api.startOutlookScheduling).not.toHaveBeenCalled();
  });
  it("keeps receipt checks available while provider-specific new writes and Undo are unavailable", async () => {
    const api = await providerClient("outlook", false);
    vi.mocked(api.events).mockResolvedValue({
      generation: 2,
      calendar_revision: 5,
      next_cursor: null,
      events: [
        {
          ...event(),
          provider: "outlook",
          status: "created",
          changes_applied: true,
          can_undo: true,
        },
      ],
    });
    await mount(api);
    await click("Check scheduling and recent actions");
    await click("Study with Sam · Added");
    expect(host.textContent).toContain("Added and verified");
    expect(button("Check event status").disabled).toBe(false);
    expect(
      [...host.querySelectorAll("button")].some(
        (node) => node.textContent?.trim() === "Undo this event",
      ),
    ).toBe(false);
    expect(api.previewEvent).not.toHaveBeenCalled();
    expect(api.undoEvent).not.toHaveBeenCalled();
  });
  it("requires fresh separate Apple consent and reuses only the current native credential", async () => {
    const api = await providerClient("icloud");
    const next = await api.scheduling(new AbortController().signal);
    vi.mocked(api.startAppleScheduling).mockResolvedValue({
      ...next,
      revision: 6,
    });
    await act(async () =>
      root.render(
        <StudentCalendarSchedulingConsent accountId={accountId} api={api} />,
      ),
    );
    await click("Check scheduling access");
    await set("Calendar service", "icloud");
    await set("Apple / iCloud account", connection);
    expect(button("Connect reviewed scheduling").disabled).toBe(true);
    expect(host.querySelector('input[type="password"]')).toBeNull();
    expect(host.textContent).toContain(
      "app-specific credential already connected",
    );
    await check("Let NavoX add and verify events");
    expect(api.startAppleScheduling).not.toHaveBeenCalled();
    await set("Calendar service", "google");
    await set("Calendar service", "icloud");
    await set("Apple / iCloud account", connection);
    expect(button("Connect reviewed scheduling").disabled).toBe(true);
    await check("Let NavoX add and verify events");
    await click("Connect reviewed scheduling");
    expect(api.startAppleScheduling).toHaveBeenCalledWith(
      expect.objectContaining({
        consent_version: "calendar-scheduling.v1",
        connection_id: connection,
        confirmed: true,
        generation: 2,
        revision: 5,
      }),
      expect.any(AbortSignal),
    );
    expect(host.textContent).toContain(
      "Each event still needs its own exact approval",
    );
    expect(api.startScheduling).not.toHaveBeenCalled();
    expect(api.startOutlookScheduling).not.toHaveBeenCalled();
  });
  it("discards a held Apple setup receipt when current owner proof changes", async () => {
    const api = await providerClient("icloud");
    const next = await api.scheduling(new AbortController().signal);
    let resolve!: (value: typeof next) => void;
    vi.mocked(api.startAppleScheduling).mockReturnValue(
      new Promise((yes) => {
        resolve = yes;
      }),
    );
    await act(async () =>
      root.render(
        <StudentCalendarSchedulingConsent accountId={accountId} api={api} />,
      ),
    );
    await click("Check scheduling access");
    await set("Calendar service", "icloud");
    await set("Apple / iCloud account", connection);
    await check("Let NavoX add and verify events");
    await click("Connect reviewed scheduling");
    await act(async () => observers[0].checking());
    await act(async () => {
      resolve(next);
      await settle();
    });
    expect(host.textContent).not.toContain("student@example.test");
    expect(host.textContent).not.toContain(
      "Each event still needs its own exact approval",
    );
    expect(host.querySelector('input[type="checkbox"]')).toBeNull();
  });
  it.each(["outlook", "icloud"] as const)(
    "reviews and conditionally undoes exact %s receipts without a client provider override",
    async (provider) => {
      const api = await providerClient(provider);
      await prepare(api, options, "Calendar account");
      expect(host.textContent).toContain(
        provider === "icloud" ? "Apple / iCloud" : "Microsoft",
      );
      expect(button("Approve and add event").disabled).toBe(true);
      await check("I reviewed this title");
      await click("Approve and add event");
      expect(button("Undo this event").disabled).toBe(true);
      expect(vi.mocked(api.approveEvent).mock.calls[0][1]).not.toHaveProperty(
        "provider",
      );
      await check("Remove only this event");
      await click("Undo this event");
      expect(vi.mocked(api.undoEvent).mock.calls[0][1]).not.toHaveProperty(
        "provider",
      );
      expect(host.textContent).toContain("Removal verified");
    },
  );
  it("does not review a non-Google event mislabeled as legacy Google", async () => {
    const api = await providerClient("icloud");
    vi.mocked(api.previewEvent).mockResolvedValue(event());
    await prepare(api, options, "Calendar account");
    expect(host.textContent).toContain("calendar service changed");
    expect(host.textContent).not.toContain("Approve and add event");
    expect(api.approveEvent).not.toHaveBeenCalled();
  });
  it("does not expose targets mislabeled as another calendar service", async () => {
    const api = await providerClient("icloud");
    const targets = await api.targets(
      grant,
      { generation: 2, revision: 5, request_id: action },
      new AbortController().signal,
    );
    vi.mocked(api.targets).mockResolvedValue({
      ...targets,
      provider: "outlook",
    });
    await mount(api);
    await click("Check scheduling and recent actions");
    await set("Calendar account", grant);
    expect(host.textContent).toContain("calendar service changed");
    expect(host.querySelector('option[value="primary"]')).toBeNull();
    expect(api.previewEvent).not.toHaveBeenCalled();
  });
  it("refreshes the exact older receipt and hides forgotten study details while checking", async () => {
    const api = client();
    vi.mocked(api.previewEvent).mockResolvedValue({
      ...event(),
      study_changes_applied: false,
      study_details_redacted: false,
      study_moves: [studyMove()],
    });
    await prepare(api);
    expect(host.textContent).toContain("Synthetic study block 1");
    let resolve!: (value: CalendarEventView) => void;
    vi.mocked(api.event).mockReturnValue(
      new Promise((yes) => {
        resolve = yes;
      }),
    );
    await click("Check scheduling and recent actions");
    expect(api.event).toHaveBeenCalledWith(action, expect.any(AbortSignal));
    expect(host.textContent).not.toContain("Synthetic study block 1");
    await act(async () => {
      resolve({
        ...event(),
        status: "failed",
        revision: 2,
        study_changes_applied: false,
        study_details_redacted: true,
        study_moves: [],
      });
      await settle();
    });
    expect(host.textContent).toContain("Some study details were forgotten");
    expect(host.textContent).not.toContain("Synthetic study block 1");
    expect(host.textContent).not.toContain("Approve and add event");
    expect(api.reconcileEvent).not.toHaveBeenCalled();
    expect(api.approveEvent).not.toHaveBeenCalled();
  });
  it("keeps private receipt details hidden after a failed refresh and recovers by exact ID", async () => {
    const api = client();
    await prepare(api);
    vi.mocked(api.event).mockRejectedValueOnce(new Error("Interrupted"));
    await click("Check scheduling and recent actions");
    expect(host.querySelector("article")).toBeNull();
    expect(host.textContent).not.toContain("Approve and add event");
    await click("Check scheduling and recent actions");
    expect(api.event).toHaveBeenNthCalledWith(
      2,
      action,
      expect.any(AbortSignal),
    );
    expect(host.textContent).toContain("Ready for your review");
    expect(button("Approve and add event").disabled).toBe(true);
    expect(api.approveEvent).not.toHaveBeenCalled();
  });
  it("does not recover an invalidated pending review after calendar access changes", async () => {
    const api = client();
    await prepare(api);
    await act(async () => notifyCalendarAccessChanged());
    expect(host.textContent).not.toContain("Ready for your review");
    await click("Check scheduling and recent actions");
    expect(api.event).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain("Approve and add event");
    expect(api.approveEvent).not.toHaveBeenCalled();
  });
  it("does not reopen approval for an earlier-reset pending receipt recovered by ID", async () => {
    const api = client();
    await prepare(api);
    vi.mocked(api.event).mockResolvedValue({
      ...event(),
      message:
        "This request predates your reset; only its status can be checked here.",
    });
    await click("Check scheduling and recent actions");
    expect(host.textContent).toContain(
      "predates your reset and cannot be approved",
    );
    expect(host.textContent).not.toContain("Approve and add event");
    expect(host.textContent).not.toContain("Ready for your review");
    await click("Check scheduling and recent actions");
    expect(api.event).toHaveBeenCalledTimes(1);
    expect(api.approveEvent).not.toHaveBeenCalled();
  });
  it("explicitly opts in only for a selected certified study-move option", async () => {
    const api = client();
    const displaced = options.map((option) => ({
      ...option,
      displaced_study_block_ids: [studyMove().block_id],
    }));
    await prepare(api, displaced);
    expect(host.textContent).toContain("review moving 1 study block");
    expect(api.previewEvent).toHaveBeenCalledWith(
      expect.objectContaining({ allow_study_displacement: true }),
      expect.any(AbortSignal),
    );
    expect(api.approveEvent).not.toHaveBeenCalled();
  });
  it("shows every exact before-and-after move before displacement approval", async () => {
    const api = client();
    vi.mocked(api.previewEvent).mockResolvedValue({
      ...event(),
      study_changes_applied: false,
      study_details_redacted: false,
      study_moves: Array.from({ length: 24 }, (_, index) =>
        studyMove(index + 1),
      ),
    });
    await prepare(api);
    const review = host.querySelector(
      '[aria-label="Exact study plan changes"]',
    );
    expect(review?.querySelectorAll("li")).toHaveLength(24);
    expect(review?.textContent).toContain("Synthetic study block 24");
    expect(review?.textContent).toContain("Before:");
    expect(review?.textContent).toContain("After:");
    expect(review?.textContent).toContain(
      "Study moves may remain if the calendar request fails",
    );
    expect(review?.textContent).toContain(
      "Calendar Undo only removes the event",
    );
    expect(button("Approve and add event").disabled).toBe(true);
    expect(api.approveEvent).not.toHaveBeenCalled();
  });
  it("keeps existing receipts reviewable on Connections without a new-event opening", async () => {
    const api = client();
    const saved: CalendarEventView = {
      ...event(),
      status: "failed",
      study_changes_applied: true,
      study_details_redacted: true,
      study_moves: [],
    };
    vi.mocked(api.events).mockResolvedValue({
      generation: 2,
      calendar_revision: 5,
      next_cursor: null,
      events: [saved],
    });
    await act(async () =>
      root.render(
        <StudentCalendarScheduling
          accountId={accountId}
          options={[]}
          availability={null}
          api={api}
        />,
      ),
    );
    expect(host.querySelector("#calendar")).not.toBeNull();
    await click("Check scheduling and recent actions");
    const recent = [...host.querySelectorAll("button")].find((button) =>
      button.textContent?.includes(saved.title),
    );
    if (!recent) throw Error("Missing saved calendar receipt");
    await act(async () => recent.click());
    expect(host.textContent).toContain("Some study details were forgotten");
    expect(host.textContent).toContain(
      "This receipt records applied study moves",
    );
    expect(host.textContent).toContain("Calendar Undo only removes the event");
    expect(api.previewEvent).not.toHaveBeenCalled();
    expect(api.approveEvent).not.toHaveBeenCalled();
    expect(host.querySelector("form")).toBeNull();
  });
  it("performs no calendar or provider reads before the student requests them", async () => {
    const api = client();
    await mount(api);
    expect(api.scheduling).not.toHaveBeenCalled();
    expect(api.events).not.toHaveBeenCalled();
    expect(api.targets).not.toHaveBeenCalled();
    expect(api.previewEvent).not.toHaveBeenCalled();
  });
  it("requires an exact read-back checkbox before adding and a separate checkbox before Undo", async () => {
    const api = client();
    await prepare(api);
    expect(host.textContent).toContain("My calendar");
    expect(host.textContent).toContain("TimezoneUTC");
    expect(host.textContent).toContain("Library");
    expect(button("Approve and add event").disabled).toBe(true);
    expect(api.approveEvent).not.toHaveBeenCalled();
    await check("I reviewed this title");
    await click("Approve and add event");
    expect(api.approveEvent).toHaveBeenCalledWith(
      action,
      expect.objectContaining({
        generation: 2,
        revision: 5,
        event_revision: 1,
        digest: "a".repeat(64),
        confirmed: true,
      }),
      expect.any(AbortSignal),
    );
    expect(host.textContent).toContain("Added and verified");
    expect(button("Undo this event").disabled).toBe(true);
    await check("Remove only this event");
    await click("Undo this event");
    expect(api.undoEvent).toHaveBeenCalledWith(
      action,
      expect.objectContaining({ event_revision: 2, confirmed: true }),
      expect.any(AbortSignal),
    );
    expect(host.textContent).toContain("Removal verified");
  });
  it("clears exact approval when any draft detail changes", async () => {
    const api = client();
    await prepare(api);
    await check("I reviewed this title");
    expect(button("Approve and add event").disabled).toBe(false);
    await set("Event title", "Another title");
    expect(host.textContent).not.toContain("Ready for your review");
    expect(api.approveEvent).not.toHaveBeenCalled();
    await click("Review exact event");
    expect(button("Approve and add event").disabled).toBe(true);
  });
  it("never retries an interrupted write and offers status-only reconciliation", async () => {
    const api = client();
    vi.mocked(api.approveEvent).mockRejectedValue(
      new Error("Network interrupted"),
    );
    vi.mocked(api.reconcileEvent).mockResolvedValue({
      ...event(),
      status: "created",
      revision: 2,
      changes_applied: true,
      can_undo: false,
      message: "Use Google Calendar to change this event.",
    });
    await prepare(api);
    await check("I reviewed this title");
    await click("Approve and add event");
    expect(api.approveEvent).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("Check the saved action’s status");
    expect(host.textContent).not.toContain("No event has been added");
    expect(button("Study with Sam · Awaiting review").disabled).toBe(true);
    expect(
      [...host.querySelectorAll("button")].some(
        (e) => e.textContent === "Approve and add event",
      ),
    ).toBe(false);
    await click("Check event status");
    expect(api.reconcileEvent).toHaveBeenCalledTimes(1);
    expect(api.approveEvent).toHaveBeenCalledTimes(1);
    expect(api.previewEvent).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("Added and verified");
    expect(host.textContent).toContain(
      "Use Google Calendar to change this event",
    );
    expect(
      [...host.querySelectorAll("button")].some(
        (button) => button.textContent === "Undo this event",
      ),
    ).toBe(false);
  });
  it("does not repeat an interrupted Undo or claim that its outcome is known", async () => {
    const api = client();
    vi.mocked(api.undoEvent).mockRejectedValue(
      new Error("Network interrupted"),
    );
    vi.mocked(api.reconcileEvent).mockResolvedValue({
      ...event(),
      status: "undone",
      revision: 3,
      changes_applied: false,
    });
    await prepare(api);
    await check("I reviewed this title");
    await click("Approve and add event");
    await check("Remove only this event");
    await click("Undo this event");
    expect(api.undoEvent).toHaveBeenCalledTimes(1);
    expect(host.textContent).not.toContain("Added and verified");
    expect(host.textContent).not.toContain("Removal verified");
    expect(button("Study with Sam · Added").disabled).toBe(true);
    await click("Check event status");
    expect(api.undoEvent).toHaveBeenCalledTimes(1);
    expect(api.reconcileEvent).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("Removal verified");
  });
  it("clears private event text and ignores a late approval after account revocation", async () => {
    const api = client();
    let resolve!: (value: CalendarEventView) => void;
    vi.mocked(api.approveEvent).mockReturnValue(
      new Promise((yes) => {
        resolve = yes;
      }),
    );
    await prepare(api);
    await check("I reviewed this title");
    await click("Approve and add event");
    const signal = vi.mocked(api.approveEvent).mock.calls[0][2];
    await act(async () => {
      for (const observer of observers) observer.revoking?.();
      await settle();
    });
    expect(signal.aborted).toBe(true);
    await act(async () => {
      resolve({
        ...event(),
        status: "created",
        changes_applied: true,
        can_undo: true,
      });
      await settle();
    });
    expect(host.textContent).not.toContain("Study with Sam");
    expect(host.textContent).not.toContain("Added and verified");
  });
  it("clears temporary review on blur and recovers only from a fresh native ledger read", async () => {
    const api = client();
    await prepare(api);
    await check("I reviewed this title");
    await act(async () => {
      window.dispatchEvent(new Event("blur"));
      await settle();
    });
    expect(host.textContent).not.toContain("Study with Sam");
    vi.mocked(api.events).mockResolvedValue({
      generation: 2,
      calendar_revision: 5,
      next_cursor: null,
      events: [
        { ...event(), status: "uncertain", revision: 2, changes_applied: null },
      ],
    });
    await click("Check scheduling and recent actions");
    expect(host.textContent).toContain("Study with Sam");
    expect(api.approveEvent).not.toHaveBeenCalled();
    expect(
      [...host.querySelectorAll("button")].some(
        (e) => e.textContent === "Review exact event",
      ),
    ).toBe(false);
    await click("Study with Sam · Needs a status check");
    await click("Check event status");
    expect(api.reconcileEvent).toHaveBeenCalledTimes(1);
  });
  it("keeps an interrupted action recoverable when its availability search expires", async () => {
    const api = client();
    let resolve!: (value: CalendarEventView) => void;
    vi.mocked(api.approveEvent).mockReturnValue(
      new Promise((yes) => {
        resolve = yes;
      }),
    );
    await prepare(api);
    await check("I reviewed this title");
    await click("Approve and add event");
    const signal = vi.mocked(api.approveEvent).mock.calls[0][2];
    await act(async () => {
      root.render(
        <StudentCalendarScheduling
          accountId={accountId}
          options={[]}
          availability={null}
          api={api}
        />,
      );
      await settle();
    });
    expect(signal.aborted).toBe(true);
    expect(host.textContent).toContain("Study with Sam");
    await act(async () => {
      resolve({
        ...event(),
        status: "created",
        revision: 2,
        changes_applied: true,
        can_undo: true,
      });
      await settle();
    });
    expect(host.textContent).not.toContain("Added and verified");
    vi.mocked(api.events).mockResolvedValue({
      generation: 2,
      calendar_revision: 5,
      next_cursor: null,
      events: [
        { ...event(), status: "uncertain", revision: 2, changes_applied: null },
      ],
    });
    await click("Check scheduling and recent actions");
    await click("Check event status");
    expect(api.approveEvent).toHaveBeenCalledTimes(1);
    expect(api.reconcileEvent).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("Added and verified");
  });
  it("keeps read-only recovery available while new calendar writes are disabled", async () => {
    productFeatures.studentCalendarWrites = false;
    const api = client();
    vi.mocked(api.events).mockResolvedValue({
      generation: 2,
      calendar_revision: 5,
      next_cursor: null,
      events: [
        {
          ...event(),
          status: "created",
          revision: 2,
          changes_applied: true,
          can_undo: true,
        },
      ],
    });
    await mount(api);
    await click("Check scheduling and recent actions");
    await click("Study with Sam · Added");
    expect(host.querySelector("form")).toBeNull();
    expect(
      [...host.querySelectorAll("button")].some(
        (button) => button.textContent === "Undo this event",
      ),
    ).toBe(false);
    await click("Check event status");
    expect(api.reconcileEvent).toHaveBeenCalledTimes(1);
    expect(api.targets).not.toHaveBeenCalled();
    expect(api.previewEvent).not.toHaveBeenCalled();
    expect(api.approveEvent).not.toHaveBeenCalled();
    expect(api.undoEvent).not.toHaveBeenCalled();
    await act(async () => {
      root.render(
        <StudentCalendarSchedulingConsent accountId={accountId} api={api} />,
      );
      await settle();
    });
    await click("Check scheduling access");
    expect(host.textContent).not.toContain("Connect reviewed scheduling");
    await click("Stop scheduling access");
    expect(api.stopScheduling).toHaveBeenCalledTimes(1);
    expect(api.startScheduling).not.toHaveBeenCalled();
  });
  it("can reach an older held action through bounded native receipt pages", async () => {
    const api = client();
    const old = {
      ...event(),
      id: connection,
      title: "Older calendar request",
      status: "uncertain" as const,
      revision: 2,
      changes_applied: null,
      message:
        "This request predates your reset; only its status can be checked here.",
    };
    vi.mocked(api.events)
      .mockResolvedValueOnce({
        generation: 2,
        calendar_revision: 5,
        events: [event()],
        next_cursor: action,
      })
      .mockResolvedValueOnce({
        generation: 2,
        calendar_revision: 5,
        events: [
          old,
          { ...old, id: account.id, title: "Another older request" },
        ],
        next_cursor: null,
      });
    vi.mocked(api.reconcileEvent).mockResolvedValue({
      ...old,
      status: "created",
      revision: 3,
      changes_applied: true,
      can_undo: false,
    });
    await mount(api);
    await click("Check scheduling and recent actions");
    await click("More saved actions");
    expect(api.events).toHaveBeenLastCalledWith(
      expect.any(AbortSignal),
      action,
    );
    await click("Older calendar request · Needs a status check");
    expect(host.textContent).toContain("predates your reset");
    expect(host.textContent).toContain("Another older request");
    await click("Check event status");
    expect(api.reconcileEvent).toHaveBeenCalledWith(
      connection,
      expect.objectContaining({ generation: 2, event_revision: 2 }),
      expect.any(AbortSignal),
    );
    expect(api.undoEvent).not.toHaveBeenCalled();
    expect(
      [...host.querySelectorAll("button")].some(
        (button) => button.textContent === "Undo this event",
      ),
    ).toBe(false);
  });
  it("can leave an externally changed event without trying to modify it", async () => {
    const api = client();
    vi.mocked(api.events).mockResolvedValue({
      generation: 2,
      calendar_revision: 5,
      next_cursor: null,
      events: [
        { ...event(), status: "changed", revision: 2, changes_applied: null },
      ],
    });
    await mount(api);
    await click("Check scheduling and recent actions");
    await click("Study with Sam · Needs a status check");
    expect(host.textContent).toContain("changed outside NavoX");
    await click("Review another event");
    expect(host.querySelector("form")).not.toBeNull();
    expect(api.approveEvent).not.toHaveBeenCalled();
    expect(api.undoEvent).not.toHaveBeenCalled();
  });
  it("expires the approval checkbox and requires a fresh review", async () => {
    const api = client();
    await prepare(api);
    await check("I reviewed this title");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600_000);
    });
    expect(button("Approve and add event").disabled).toBe(true);
    expect(host.textContent).toContain("This review expired");
    expect(api.approveEvent).not.toHaveBeenCalled();
  });
  it("does not display a late result after owner proof changes", async () => {
    const api = client();
    await mount(api);
    vi.mocked(api.account)
      .mockResolvedValueOnce(accountId)
      .mockResolvedValueOnce("another-owner");
    await click("Check scheduling and recent actions");
    expect(host.textContent).not.toContain("student@example.test");
    expect(host.textContent).toContain("Your account could not be verified");
  });
  it("keeps scheduling consent separate and never starts OAuth without explicit agreement", async () => {
    const api = client();
    await act(async () => {
      root.render(
        <StudentCalendarSchedulingConsent accountId={accountId} api={api} />,
      );
      await settle();
    });
    await click("Check scheduling access");
    await set("Google account", connection);
    expect(host.textContent).toContain(calendarSchedulingConsent);
    expect(button("Connect reviewed scheduling").disabled).toBe(true);
    await check("Let NavoX add and verify events");
    expect(button("Connect reviewed scheduling").disabled).toBe(false);
    expect(api.startScheduling).not.toHaveBeenCalled();
    await click("Stop scheduling access");
    expect(api.stopScheduling).toHaveBeenCalledWith(
      grant,
      expect.objectContaining({ generation: 2, revision: 5 }),
      expect.any(AbortSignal),
    );
    expect(host.textContent).toContain(
      "Existing calendar events were not removed",
    );
    expect(api.disconnect).not.toHaveBeenCalled();
  });
});
