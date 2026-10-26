import { describe, expect, it, vi } from "vitest";
import {
  CALENDAR_SCHEDULING_CONSENT_VERSION,
  CALENDAR_SCHEDULING_SCOPES,
  type CalendarEventView,
  type CalendarSchedulingStart,
  createStudentCalendarSchedulingApi,
  readCalendarEventView,
  readCalendarSchedulingView,
} from "./student-calendar-scheduling";

const user = "11111111-1111-4111-8111-111111111111",
  workspace = "22222222-2222-4222-8222-222222222222";
const grant = "33333333-3333-4333-8333-333333333333",
  connection = "44444444-4444-4444-8444-444444444444";
const action = "55555555-5555-4555-8555-555555555555",
  request = "66666666-6666-4666-8666-666666666666";
const account = `${workspace}:${user}`;
const signal = () => new AbortController().signal;
const command = { generation: 2, revision: 5, request_id: request };
const eventCommand = { ...command, event_revision: 1, digest: "a".repeat(64) };
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
    start_at: "2026-10-22T13:00:00Z",
    end_at: "2026-10-22T13:30:00Z",
    timezone: "UTC",
    expires_at: "2026-10-22T12:10:00Z",
    changes_applied: false,
    can_undo: false,
    message: "Review this event",
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
function view() {
  return {
    generation: 2,
    revision: 5,
    consent_version: CALENDAR_SCHEDULING_CONSENT_VERSION,
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
  };
}
function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
function authorization() {
  const target = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  for (const [key, value] of Object.entries({
    client_id: "client",
    response_type: "code",
    redirect_uri: "https://api.example.test/api/v1/connections/google/callback",
    scope: CALENDAR_SCHEDULING_SCOPES.join(" "),
    state: "random-state",
    code_challenge: "a".repeat(43),
    code_challenge_method: "S256",
    access_type: "offline",
    prompt: "consent",
    include_granted_scopes: "true",
  }))
    target.searchParams.set(key, value);
  return {
    authorization_url: target.toString(),
    requested_scopes: [...CALENDAR_SCHEDULING_SCOPES] as string[],
    generation: 2,
    revision: 6,
    grant_id: grant,
  };
}

describe("separate reviewed calendar scheduling boundary", () => {
  it("reads complete bounded displacement receipts and truthful redaction", () => {
    expect(readCalendarEventView(event())).not.toHaveProperty("study_moves");
    const full = {
      ...event(),
      study_changes_applied: false,
      study_details_redacted: false,
      study_moves: Array.from({ length: 24 }, (_, index) =>
        studyMove(index + 1),
      ),
    };
    expect(readCalendarEventView(full).study_moves).toHaveLength(24);
    for (const changes of [
      { study_moves: [...full.study_moves, studyMove(25)] },
      { study_moves: [studyMove(), studyMove()] },
      { study_moves: [{ ...studyMove(), minutes: 20 }] },
      { study_moves: [{ ...studyMove(), minutes: 91 }] },
      { study_changes_applied: true },
      { study_details_redacted: true },
    ])
      expect(() => readCalendarEventView({ ...full, ...changes })).toThrow();
    expect(() =>
      readCalendarEventView({ ...event(), study_moves: [studyMove()] }),
    ).toThrow();
    expect(
      readCalendarEventView({
        ...event(),
        status: "failed",
        study_changes_applied: true,
        study_details_redacted: true,
        study_moves: [],
      }).study_changes_applied,
    ).toBe(true);
    expect(
      readCalendarEventView({
        ...event(),
        status: "failed",
        study_changes_applied: false,
        study_details_redacted: true,
        study_moves: [],
      }).study_moves,
    ).toEqual([]);
  });
  it("does not read or change anything at construction", () => {
    const fetch = vi.fn();
    createStudentCalendarSchedulingApi(account, { fetch });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("uses credentialed bounded private transport and explicit scheduling purpose", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => json(view()));
    const api = createStudentCalendarSchedulingApi(account, {
      fetch,
      baseUrl: "https://api.example.test/api/v1",
    });
    expect(await api.scheduling(signal())).toEqual(view());
    expect(fetch).toHaveBeenCalledWith(
      "https://api.example.test/api/v1/student/calendars/scheduling",
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        headers: { "X-NavoX-Account": account },
      }),
    );
  });
  it("reads a maximum UTF-8 study-move page within the bounded response cap", async () => {
    const events = Array.from({ length: 20 }, (_, index) => ({
      ...event(),
      id: `90000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
      calendar_label: "界".repeat(1024),
      title: "界".repeat(200),
      location: "界".repeat(500),
      message: "界".repeat(1000),
      study_changes_applied: false,
      study_details_redacted: false,
      study_moves: Array.from({ length: 24 }, (_, index) => ({
        ...studyMove(index + 1),
        title: "界".repeat(200),
      })),
    }));
    const page = {
      generation: 2,
      calendar_revision: 5,
      events,
      next_cursor: null,
    };
    const bytes = new TextEncoder().encode(JSON.stringify(page)).byteLength;
    expect(bytes).toBeGreaterThan(256 * 1024);
    expect(bytes).toBeLessThan(1024 * 1024);
    const api = createStudentCalendarSchedulingApi(account, {
      fetch: vi.fn(async () => json(page)),
    });
    expect((await api.events(signal())).events).toHaveLength(20);
  });
  it("reads only the requested account-checked receipt without provider commands", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => json(event()));
    const api = createStudentCalendarSchedulingApi(account, {
      fetch,
      baseUrl: "https://api.example.test/api/v1",
    });
    expect(await api.event(action, signal())).toEqual(event());
    expect(fetch).toHaveBeenCalledWith(
      `https://api.example.test/api/v1/student/calendars/events/${action}`,
      expect.objectContaining({
        method: "GET",
        headers: { "X-NavoX-Account": account },
        cache: "no-store",
      }),
    );
    fetch.mockResolvedValue(json({ ...event(), id: request }));
    await expect(api.event(action, signal())).rejects.toThrow();
  });
  it("accepts exact Google write-purpose authorization, never just read-busy scopes", async () => {
    const answer = authorization();
    const fetch = vi.fn<typeof globalThis.fetch>(async () => json(answer));
    const api = createStudentCalendarSchedulingApi(account, { fetch });
    const input: CalendarSchedulingStart = {
      ...command,
      connection_id: connection,
      consent_version: CALENDAR_SCHEDULING_CONSENT_VERSION,
      confirmed: true as const,
    };
    expect(
      (await api.startScheduling(input, signal())).requested_scopes,
    ).toContain("https://www.googleapis.com/auth/calendar.events.owned");
    answer.requested_scopes.pop();
    await expect(api.startScheduling(input, signal())).rejects.toThrow();
  });
  it("rejects a redirected or extra-scope authorization", async () => {
    for (const mutate of [
      (a: ReturnType<typeof authorization>) => {
        a.authorization_url = a.authorization_url.replace(
          "accounts.google.com",
          "evil.example",
        );
      },
      (a: ReturnType<typeof authorization>) => {
        a.requested_scopes.push("https://mail.google.com/");
      },
    ]) {
      const answer = authorization();
      mutate(answer);
      const api = createStudentCalendarSchedulingApi(account, {
        fetch: vi.fn(async () => json(answer)),
      });
      await expect(
        api.startScheduling(
          {
            ...command,
            connection_id: connection,
            consent_version: CALENDAR_SCHEDULING_CONSENT_VERSION,
            confirmed: true,
          },
          signal(),
        ),
      ).rejects.toThrow();
    }
  });
  it("refuses unconfirmed or inherited availability-purpose consent before I/O", () => {
    const fetch = vi.fn();
    const api = createStudentCalendarSchedulingApi(account, { fetch });
    for (const extra of [
      { confirmed: false },
      { consent_version: "calendar-availability.v1" },
      { connection_id: null },
    ])
      expect(() =>
        api.startScheduling(
          {
            ...command,
            connection_id: connection,
            consent_version: CALENDAR_SCHEDULING_CONSENT_VERSION,
            confirmed: true,
            ...extra,
          } as never,
          signal(),
        ),
      ).toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects connected grants without exact provider and connection", () => {
    for (const extra of [{ provider: "outlook" }, { connection_id: null }]) {
      const value = view();
      Object.assign(value.grants[0], extra);
      expect(() => readCalendarSchedulingView(value)).toThrow();
    }
  });
  it("rejects duplicate grants and hidden response fields", () => {
    const value = view();
    value.grants.push({ ...value.grants[0] });
    expect(() => readCalendarSchedulingView(value)).toThrow();
    expect(() =>
      readCalendarSchedulingView({ ...view(), private_token: "secret" }),
    ).toThrow();
  });
  it("binds target inventory to the requested grant and generation", async () => {
    const answer = {
      generation: 2,
      revision: 5,
      grant_id: grant,
      targets: [
        { id: "primary@example.test", label: "My calendar", timezone: "UTC" },
      ],
      changes_applied: false,
    };
    const api = createStudentCalendarSchedulingApi(account, {
      fetch: vi.fn(async () => json(answer)),
    });
    expect((await api.targets(grant, command, signal())).targets).toHaveLength(
      1,
    );
    answer.generation = 3;
    await expect(api.targets(grant, command, signal())).rejects.toThrow();
  });
  it("rejects malformed or duplicate writable targets", async () => {
    const answer = {
      generation: 2,
      revision: 5,
      grant_id: grant,
      targets: [
        { id: "primary", label: "My calendar", timezone: "UTC" },
        { id: "primary", label: "Other calendar", timezone: "UTC" },
      ],
      changes_applied: false,
    };
    const api = createStudentCalendarSchedulingApi(account, {
      fetch: vi.fn(async () => json(answer)),
    });
    await expect(api.targets(grant, command, signal())).rejects.toThrow();
  });
  it("binds approve, reconcile and Undo to the exact event, generation and digest", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => json(event()));
    const api = createStudentCalendarSchedulingApi(account, { fetch });
    await api.approveEvent(
      action,
      { ...eventCommand, confirmed: true },
      signal(),
    );
    await api.reconcileEvent(action, eventCommand, signal());
    await api.undoEvent(action, { ...eventCommand, confirmed: true }, signal());
    expect(
      fetch.mock.calls
        .filter((call) => call[1]?.method === "POST")
        .map((call) => String(call[0]).split("/").at(-1)),
    ).toEqual(["approve", "reconcile", "undo"]);
    const bodies = fetch.mock.calls
      .filter((call) => call[1]?.method === "POST")
      .map((call) => JSON.parse(String(call[1]?.body)));
    expect(bodies[0]).toEqual({ ...eventCommand, confirmed: true });
    expect(bodies[1]).toEqual(eventCommand);
    expect(bodies[1]).not.toHaveProperty("confirmed");
  });
  it("never accepts an action response for a different event or generation", async () => {
    for (const replacement of [
      { id: request },
      { generation: 3 },
      { digest: "b".repeat(64) },
      { revision: 0 },
      { calendar_revision: 4 },
    ]) {
      const api = createStudentCalendarSchedulingApi(account, {
        fetch: vi.fn(async () => json({ ...event(), ...replacement })),
      });
      await expect(
        api.approveEvent(
          action,
          { ...eventCommand, confirmed: true },
          signal(),
        ),
      ).rejects.toThrow();
    }
  });
  it("refreshes the native receipt before checking an interrupted outcome without renewing write approval", async () => {
    const native = {
      ...event(),
      generation: 3,
      calendar_revision: 7,
      revision: 2,
      status: "uncertain" as const,
      changes_applied: null,
    };
    const verified = {
      ...native,
      revision: 3,
      status: "created" as const,
      changes_applied: true,
    };
    const fetch = vi.fn<typeof globalThis.fetch>(async (_url, init) =>
      json(init?.method === "GET" ? native : verified),
    );
    const api = createStudentCalendarSchedulingApi(account, { fetch });
    expect(
      (await api.reconcileEvent(action, eventCommand, signal())).status,
    ).toBe("created");
    expect(fetch.mock.calls).toHaveLength(2);
    expect(String(fetch.mock.calls[0][0])).toMatch(
      new RegExp(`/events/${action}$`),
    );
    const sent = JSON.parse(String(fetch.mock.calls[1][1]?.body));
    expect(sent).toEqual({
      ...eventCommand,
      generation: 3,
      revision: 7,
      event_revision: 2,
    });
    expect(sent).not.toHaveProperty("confirmed");
    expect(
      fetch.mock.calls.every(
        (call) =>
          !String(call[0]).endsWith("/approve") &&
          !String(call[0]).endsWith("/undo"),
      ),
    ).toBe(true);
  });
  it("refuses unconfirmed approvals and forged digests without I/O", () => {
    const fetch = vi.fn();
    const api = createStudentCalendarSchedulingApi(account, { fetch });
    expect(() =>
      api.approveEvent(
        action,
        { ...eventCommand, confirmed: false } as never,
        signal(),
      ),
    ).toThrow();
    expect(() =>
      api.undoEvent(
        action,
        { ...eventCommand, digest: "malformed", confirmed: true },
        signal(),
      ),
    ).toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("preserves unknown outcomes instead of claiming no change", () => {
    expect(
      readCalendarEventView({
        ...event(),
        status: "uncertain",
        changes_applied: null,
      }).changes_applied,
    ).toBeNull();
    expect(() =>
      readCalendarEventView({
        ...event(),
        status: "uncertain",
        changes_applied: false,
      }),
    ).toThrow();
  });
  it("requires a bounded, valid event and never offers Undo for an uncertain operation", () => {
    for (const extra of [
      { status: "uncertain", can_undo: true },
      { end_at: "2026-10-22T12:00:00Z" },
      { timezone: "Not/AZone" },
      { title: "x".repeat(201) },
      { unexpected: "value" },
    ])
      expect(() => readCalendarEventView({ ...event(), ...extra })).toThrow();
  });
  it("recovers native actions only within the returned current generation", async () => {
    const value = {
      generation: 2,
      calendar_revision: 5,
      events: [event()],
      next_cursor: null,
    };
    const api = createStudentCalendarSchedulingApi(account, {
      fetch: vi.fn(async () => json(value)),
    });
    expect((await api.events(signal())).events[0].id).toBe(action);
    value.generation = 3;
    await expect(api.events(signal())).rejects.toThrow();
  });
  it("paginates native receipts with an owned cursor and rejects a repeated cursor", async () => {
    const first = {
      generation: 2,
      calendar_revision: 5,
      events: [event()],
      next_cursor: action as string | null,
    };
    const fetch = vi.fn<typeof globalThis.fetch>(async () => json(first));
    const api = createStudentCalendarSchedulingApi(account, { fetch });
    expect((await api.events(signal())).next_cursor).toBe(action);
    await expect(api.events(signal(), action)).rejects.toThrow();
    expect(String(fetch.mock.calls[1][0])).toContain(`?cursor=${action}`);
    first.events = [{ ...event(), id: request }];
    first.next_cursor = null;
    expect((await api.events(signal(), action)).events[0].id).toBe(request);
  });
  it("requires the reviewed event details to match the preview response", async () => {
    const input = {
      ...command,
      grant_id: grant,
      calendar_id: "primary",
      title: "Study with Sam",
      location: "Library",
      start_at: "2026-10-22T13:00:00Z",
      end_at: "2026-10-22T13:30:00Z",
      timezone: "UTC",
      availability: {
        start_at: "2026-10-22T12:00:00Z",
        end_at: "2026-10-22T18:00:00Z",
        duration_minutes: 30,
        preferences_revision: 2,
        expected_timezone: "UTC",
        routine_confirmed: true,
        travel_assumption: "zero" as const,
        include_short_slots: false,
      },
    };
    const fetch = vi.fn<typeof globalThis.fetch>(async () =>
      json({ ...event(), title: "Different event" }),
    );
    const api = createStudentCalendarSchedulingApi(account, { fetch });
    await expect(api.previewEvent(input, signal())).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("sends explicit displacement opt-in only when provided", async () => {
    const input = {
      ...command,
      grant_id: grant,
      calendar_id: "primary",
      title: "Study with Sam",
      location: "Library",
      start_at: event().start_at,
      end_at: event().end_at,
      timezone: "UTC",
      availability: {
        start_at: "2026-10-22T12:00:00Z",
        end_at: "2026-10-22T18:00:00Z",
        duration_minutes: 30,
        preferences_revision: 2,
        expected_timezone: "UTC",
        routine_confirmed: true,
        travel_assumption: "zero" as const,
        include_short_slots: false,
      },
    };
    const fetch = vi.fn<typeof globalThis.fetch>(async () => json(event()));
    const api = createStudentCalendarSchedulingApi(account, { fetch });
    await api.previewEvent(input, signal());
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).not.toHaveProperty(
      "allow_study_displacement",
    );
    await api.previewEvent(
      { ...input, allow_study_displacement: true },
      signal(),
    );
    expect(
      JSON.parse(String(fetch.mock.calls[1][1]?.body)).allow_study_displacement,
    ).toBe(true);
  });
});
