import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type AvailabilityPreviewInput,
  type AvailabilitySnapshot,
  CALENDAR_CONSENT_VERSION,
  CALENDAR_READ_SCOPES,
  type CalendarStartInput,
  createStudentCalendarApi,
  MICROSOFT_CALENDAR_READ_SCOPES,
  readAvailabilitySnapshot,
  readCalendarAuthorization,
  readCalendarView,
} from "./student-calendar-api";

const user = "11111111-1111-4111-8111-111111111111";
const workspace = "22222222-2222-4222-8222-222222222222";
const other = "33333333-3333-4333-8333-333333333333";
const grant = "44444444-4444-4444-8444-444444444444";
const requestId = "55555555-5555-4555-8555-555555555555";
const account = `${workspace}:${user}`;
const checked = "2026-10-22T12:00:00Z",
  end = "2026-10-22T23:00:00Z",
  expiry = "2026-10-22T12:01:00Z",
  now = Date.parse(checked);
const signal = () => new AbortController().signal;
const prefs = {
  wake_time: "07:00",
  sleep_time: "23:00",
  focus_windows: [],
  never_schedule: [],
  max_study_minutes: 90,
  break_minutes: 15,
  // The complete JSON shape of the canonical backend Preferences model.
  travel_minutes: 15,
  wake_times: null,
  alarm: {
    enabled: false,
    days: [0, 1, 2, 3, 4, 5, 6],
    smart: false,
    prep_minutes: 30,
    sound: "gentle" as const,
    snooze_minutes: 10 as const,
    skip_date: null,
  },
};
const start: CalendarStartInput = {
  generation: 4,
  revision: 7,
  request_id: requestId,
  consent_version: CALENDAR_CONSENT_VERSION,
  confirmed: true,
};
const preview: AvailabilityPreviewInput = {
  start_at: checked,
  end_at: end,
  duration_minutes: 30,
  preferences_revision: 2,
  expected_timezone: "UTC",
  routine_confirmed: true,
  travel_assumption: "unknown",
  include_short_slots: false,
};
function view() {
  return {
    generation: 4,
    revision: 8,
    consent_version: CALENDAR_CONSENT_VERSION,
    providers: [
      { id: "google", availability: "available" },
      { id: "outlook", availability: "unavailable" },
      { id: "icloud", availability: "unavailable" },
    ],
    grants: [
      {
        id: grant,
        provider: "google",
        connection_id: other,
        account_label: "student@example.test",
        status: "connected",
        revision: 2,
      },
    ],
    changes_applied: false,
  };
}
function authorization() {
  const target = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  for (const [key, value] of Object.entries({
    client_id: "navox-client",
    response_type: "code",
    redirect_uri: "https://api.example.test/api/v1/connections/google/callback",
    scope: CALENDAR_READ_SCOPES.join(" "),
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
    requested_scopes: [...CALENDAR_READ_SCOPES],
    generation: 4,
    revision: 8,
    grant_id: grant,
  };
}
function snapshot(): AvailabilitySnapshot {
  return {
    input: {
      start_at: checked,
      end_at: end,
      user_id: user,
      workspace_id: workspace,
      timezone: "UTC",
      now: checked,
      preferences: structuredClone(prefs),
      routine_confirmed: true,
      required_source_ids: ["student", "google:opaque"],
      coverage: ["student", "google:opaque"].map((source_id) => ({
        start_at: checked,
        end_at: end,
        user_id: user,
        workspace_id: workspace,
        source_id,
        state: "complete",
        observed_at: checked,
        fresh_until: expiry,
        revision: "fresh",
      })),
      busy: [
        {
          id: "meeting:opaque",
          source_id: "google:opaque",
          user_id: user,
          workspace_id: workspace,
          start_at: "2026-10-22T15:00:00Z",
          end_at: "2026-10-22T16:00:00Z",
          kind: "meeting",
          travel_required: true,
          travel_before_minutes: 0,
          travel_after_minutes: 0,
        },
      ],
      study_blocks: [],
      uncertainties: [],
      duration_minutes: 30,
      include_short_slots: false,
    },
    result: {
      timezone: "UTC",
      checked_at: checked,
      scope: "provided_owned_sources_only",
      status: "complete",
      confirmed_openings: [
        {
          start_at: "2026-10-22T12:30:00Z",
          end_at: "2026-10-22T14:50:00Z",
          minutes: 140,
          reasons: [],
          unchecked_source_ids: [],
          unknown_travel_record_ids: [],
          displaced_study_block_ids: [],
        },
      ],
      provisional_openings: [],
      busy: [
        { start_at: "2026-10-22T14:50:00Z", end_at: "2026-10-22T16:10:00Z" },
      ],
      warnings: [],
      changes_applied: false,
    },
    checked_at: checked,
    expires_at: expiry,
    revision: {
      student_revision: "a".repeat(64),
      planning_revision: 1,
      preferences_revision: 2,
      availability_generation: 4,
      availability_revision: 8,
    },
    source_summary: [
      { source_id: "google:opaque", state: "complete", calendar_count: 2 },
    ],
    changes_applied: false,
  };
}
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
function setup(body: unknown = view()) {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(json(body));
  const api = createStudentCalendarApi(account, {
    fetch: fetcher,
    baseUrl: "https://api.example.test/api/v1/",
    now: () => now,
  });
  return { fetcher, api };
}
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
describe("account-bound calendar API", () => {
  it("is inert at construction and uses private cookie requests with exact account proof", async () => {
    const { api, fetcher } = setup();
    expect(fetcher).not.toHaveBeenCalled();
    await expect(api.calendars(signal())).resolves.toEqual(view());
    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/api/v1/student/calendars",
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        headers: { "X-NavoX-Account": account },
      }),
    );
    expect(fetcher.mock.calls[0][1]?.headers).not.toHaveProperty(
      "Authorization",
    );
  });
  it.each([user, `${workspace}:${user}:extra`, `${workspace}:bad`, ""])(
    "rejects invalid owner scope %s before any I/O",
    (owner) => {
      expect(() => createStudentCalendarApi(owner)).toThrow();
    },
  );
  it("checks auth/me still belongs to the captured account", async () => {
    const { api, fetcher } = setup({ id: user, workspace: { id: workspace } });
    await expect(api.account(signal())).resolves.toBe(account);
    fetcher.mockResolvedValue(
      json({ id: other, workspace: { id: workspace } }),
    );
    await expect(api.account(signal())).rejects.toMatchObject({
      code: "account-changed",
    });
  });
  it("reads routine through the bound transport and rejects unrelated details", async () => {
    const data = { ...prefs, revision: 2, timezone: "UTC" },
      { api, fetcher } = setup(data);
    await expect(api.preferences(signal())).resolves.toEqual(data);
    expect(fetcher.mock.calls[0][0]).toBe(
      "https://api.example.test/api/v1/student/availability/routine",
    );
    fetcher.mockResolvedValue(json({ ...data, event_title: "private" }));
    await expect(api.preferences(signal())).rejects.toMatchObject({
      code: "invalid-response",
    });
  });
  it("reads saved per-day wakes, travel and alarm settings in the canonical routine response", async () => {
    const data = {
      ...prefs,
      revision: 2,
      timezone: "America/New_York",
      travel_minutes: 35,
      wake_times: [
        "06:30",
        "06:30",
        "06:30",
        "06:30",
        "06:30",
        "08:00",
        "08:00",
      ],
      alarm: {
        ...prefs.alarm,
        enabled: true,
        days: [0, 1, 2, 3, 4],
        smart: true,
        prep_minutes: 45,
        sound: "chime",
        snooze_minutes: 15,
        skip_date: "2026-10-25",
      },
    };
    const { api } = setup(data);
    await expect(api.preferences(signal())).resolves.toEqual(data);
  });
  it("starts only the exact consented purpose and sends no write or Gmail scopes", async () => {
    const { api, fetcher } = setup(authorization());
    await expect(api.start(start, signal())).resolves.toEqual(authorization());
    expect(fetcher.mock.calls[0][0]).toBe(
      "https://api.example.test/api/v1/student/calendars/google/start",
    );
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(start);
    expect(fetcher.mock.calls[0][1]?.headers).toEqual({
      "X-NavoX-Account": account,
      "Content-Type": "application/json",
    });
  });
  it.each([false, "true", null])(
    "requires explicit purpose consent %j",
    (confirmed) => {
      const { api, fetcher } = setup();
      expect(() =>
        api.start({ ...start, confirmed } as CalendarStartInput, signal()),
      ).toThrow();
      expect(fetcher).not.toHaveBeenCalled();
    },
  );
  it("disconnects exactly the selected grant and rejects arbitrary paths", async () => {
    const { api, fetcher } = setup(),
      command = { generation: 4, revision: 8, request_id: requestId };
    await api.disconnect(grant, command, signal());
    expect(fetcher.mock.calls[0][0]).toBe(
      `https://api.example.test/api/v1/student/calendars/${grant}/disconnect`,
    );
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(command);
    expect(() => api.disconnect("../other", command, signal())).toThrow();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each([
    { generation: 5 },
    { revision: 7 },
    { grant_id: "bad" },
    { credentials: "secret" },
    {
      requested_scopes: [
        ...CALENDAR_READ_SCOPES,
        "https://www.googleapis.com/auth/gmail.send",
      ],
    },
  ])("rejects swapped or overbroad OAuth result %j", async (change) => {
    const { api } = setup({ ...authorization(), ...change });
    await expect(api.start(start, signal())).rejects.toMatchObject({
      code: "invalid-response",
    });
  });
  it.each(["host", "write", "duplicate", "pkce", "implicit", "credentials"])(
    "rejects unsafe OAuth %s before navigation",
    async (change) => {
      const body = authorization(),
        target = new URL(body.authorization_url);
      if (change === "host") target.hostname = "evil.example.test";
      if (change === "write")
        target.searchParams.set(
          "scope",
          [
            ...CALENDAR_READ_SCOPES,
            "https://www.googleapis.com/auth/calendar.events",
          ].join(" "),
        );
      if (change === "duplicate")
        target.searchParams.append("scope", CALENDAR_READ_SCOPES.join(" "));
      if (change === "pkce")
        target.searchParams.set("code_challenge_method", "plain");
      if (change === "implicit")
        target.searchParams.set("response_type", "token");
      if (change === "credentials") target.password = "secret";
      const { api } = setup({ ...body, authorization_url: target.toString() });
      await expect(api.start(start, signal())).rejects.toMatchObject({
        code: "invalid-response",
      });
    },
  );
  it.each([401, 409, 500, 503])(
    "sanitizes HTTP%i without exposing provider bodies",
    async (status) => {
      const { api, fetcher } = setup();
      fetcher.mockResolvedValue(
        json({ detail: "secret-provider-token" }, status),
      );
      await expect(api.calendars(signal())).rejects.toMatchObject({
        code:
          status === 401 || status === 409 ? "account-changed" : "unavailable",
        status,
      });
      await expect(api.calendars(signal())).rejects.not.toThrow(
        "secret-provider-token",
      );
    },
  );
  it("posts the exact saved-routine/timezone snapshot request", async () => {
    const { api, fetcher } = setup(snapshot());
    await expect(api.preview(preview, signal())).resolves.toEqual(snapshot());
    expect(fetcher.mock.calls[0][0]).toBe(
      "https://api.example.test/api/v1/student/availability/preview",
    );
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(preview);
  });
  it.each([
    { duration_minutes: 0 },
    { duration_minutes: 481 },
    { routine_confirmed: "true" },
    { travel_assumption: "guess" },
    { expected_timezone: "bad/zone" },
    { preferences_revision: -1 },
    { include_short_slots: "false" },
    { credentials: "secret" },
  ])("rejects malformed preview commands %j without fetching", (change) => {
    const { api, fetcher } = setup();
    expect(() =>
      api.preview(
        { ...preview, ...change } as AvailabilityPreviewInput,
        signal(),
      ),
    ).toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("does not fetch after a pre-aborted call", async () => {
    const { api, fetcher } = setup(),
      controller = new AbortController();
    controller.abort();
    await expect(api.calendars(controller.signal)).rejects.toMatchObject({
      code: "cancelled",
    });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("rejects on abort and cancels late bodies even if fetch ignores abort", async () => {
    const { api, fetcher } = setup();
    let resolve!: (response: Response) => void;
    fetcher.mockReturnValue(
      new Promise((yes) => {
        resolve = yes;
      }),
    );
    const controller = new AbortController(),
      pending = api.calendars(controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ code: "cancelled" });
    const cancel = vi.fn();
    resolve(new Response(new ReadableStream({ cancel })));
    for (let i = 0; i < 5; i++) await Promise.resolve();
    expect(cancel).toHaveBeenCalledTimes(1);
  });
  it("allows a fresh preview to complete after 25 seconds while metadata keeps its 20 second deadline", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const response = snapshot();
    response.checked_at = "2026-10-22T12:00:25Z";
    response.input.now = response.checked_at;
    response.result.checked_at = response.checked_at;
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(
        () =>
          new Promise((resolve) =>
            setTimeout(() => resolve(json(response)), 25_000),
          ),
      );
    const api = createStudentCalendarApi(account, {
      fetch: fetcher,
      baseUrl: "https://api.example.test/api/v1",
      now: Date.now,
    });
    const pending = api.preview(preview, signal());
    await vi.advanceTimersByTimeAsync(25_000);
    await expect(pending).resolves.toEqual(response);
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("still bounds an abort-ignoring preview at 55 seconds", async () => {
    vi.useFakeTimers();
    const { api, fetcher } = setup();
    fetcher.mockReturnValue(new Promise(() => {}));
    let settled = false;
    const result = api.preview(preview, signal()).catch((error: unknown) => {
      settled = true;
      return error;
    });
    await vi.advanceTimersByTimeAsync(54_999);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await expect(result).resolves.toMatchObject({ code: "cancelled" });
    expect(vi.getTimerCount()).toBe(0);
  });
  it("bounds a fetch that ignores abort with a deadline", async () => {
    vi.useFakeTimers();
    const { api, fetcher } = setup();
    fetcher.mockReturnValue(new Promise(() => {}));
    const pending = expect(api.calendars(signal())).rejects.toMatchObject({
      code: "cancelled",
    });
    await vi.advanceTimersByTimeAsync(20_000);
    await pending;
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each(["declared", "streamed"])(
    "bounds %s response bytes and cancels unread data",
    async (mode) => {
      const { api, fetcher } = setup(),
        cancel = vi.fn(),
        body = new ReadableStream<Uint8Array>({
          start(controller) {
            if (mode === "streamed") controller.enqueue(new Uint8Array(65537));
          },
          cancel,
        });
      fetcher.mockResolvedValue(
        new Response(body, {
          headers: {
            "Content-Type": "application/json",
            ...(mode === "declared" ? { "Content-Length": "65537" } : {}),
          },
        }),
      );
      await expect(api.calendars(signal())).rejects.toMatchObject({
        code: "invalid-response",
      });
      expect(cancel).toHaveBeenCalledTimes(1);
    },
  );
  it("rejects HTML and malformed UTF8 bodies", async () => {
    const { api, fetcher } = setup();
    fetcher.mockResolvedValue(
      new Response("login", { headers: { "Content-Type": "text/html" } }),
    );
    await expect(api.calendars(signal())).rejects.toMatchObject({
      code: "invalid-response",
    });
    fetcher.mockResolvedValue(
      new Response(new Uint8Array([0xc0, 0xaf]), {
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(api.calendars(signal())).rejects.toMatchObject({
      code: "invalid-response",
    });
  });
});
describe("calendar snapshot consistency", () => {
  it("preserves the canonical routine fields inside an availability snapshot", () => {
    const data = snapshot();
    data.input.preferences = {
      ...prefs,
      travel_minutes: 35,
      wake_times: [
        "06:30",
        "06:30",
        "06:30",
        "06:30",
        "06:30",
        "08:00",
        "08:00",
      ],
      alarm: {
        ...prefs.alarm,
        enabled: true,
        days: [0, 1, 2, 3, 4],
        smart: true,
        prep_minutes: 45,
        sound: "chime",
        snooze_minutes: 15,
        skip_date: "2026-10-25",
      },
    };
    expect(
      readAvailabilitySnapshot(data, account, preview, now).input.preferences,
    ).toEqual(data.input.preferences);
  });
  it.each([
    { travel_minutes: -1 },
    { travel_minutes: 241 },
    { travel_minutes: "15" },
    { wake_times: ["07:00"] },
    {
      wake_times: [
        "24:00",
        "07:00",
        "07:00",
        "07:00",
        "07:00",
        "07:00",
        "07:00",
      ],
    },
    {
      wake_times: [
        "23:00",
        "07:00",
        "07:00",
        "07:00",
        "07:00",
        "07:00",
        "07:00",
      ],
    },
    { alarm: { ...prefs.alarm, enabled: "true" } },
    { alarm: { ...prefs.alarm, days: [0, 0] } },
    { alarm: { ...prefs.alarm, days: [7] } },
    { alarm: { ...prefs.alarm, prep_minutes: 181 } },
    { alarm: { ...prefs.alarm, snooze_minutes: 7 } },
    { alarm: { ...prefs.alarm, sound: "unsupported" } },
    { alarm: { ...prefs.alarm, skip_date: "2026-02-30" } },
    { alarm: { ...prefs.alarm, access_token: "unexpected" } },
  ])(
    "rejects malformed new routine fields in both API contracts: %j",
    async (patch) => {
      const value = { ...structuredClone(prefs), ...patch };
      const { api } = setup({ ...value, revision: 2, timezone: "UTC" });
      await expect(api.preferences(signal())).rejects.toMatchObject({
        code: "invalid-response",
      });
      const data = snapshot();
      Object.assign(data.input.preferences, patch);
      expect(() =>
        readAvailabilitySnapshot(data, account, preview, now),
      ).toThrow();
    },
  );
  it("keeps unchecked source coverage provisional", () => {
    const data = snapshot();
    data.input.coverage[1].state = "unavailable";
    data.source_summary[0].state = "unavailable";
    data.result.status = "partial";
    const opening = data.result.confirmed_openings.pop();
    if (!opening) throw new Error("Fixture missing");
    data.result.provisional_openings.push({
      ...opening,
      reasons: ["coverage_unknown"],
      unchecked_source_ids: ["google:opaque"],
    });
    const result = readAvailabilitySnapshot(data, account, preview, now);
    expect(result.result.confirmed_openings).toEqual([]);
    expect(result.result.provisional_openings[0].reasons).toEqual([
      "coverage_unknown",
    ]);
  });
  it.each(["input", "coverage", "busy", "study", "uncertainty"])(
    "rejects swapped owner in %s",
    (where) => {
      const data = snapshot();
      if (where === "input") data.input.user_id = other;
      if (where === "coverage") data.input.coverage[0].workspace_id = other;
      if (where === "busy") data.input.busy[0].user_id = other;
      if (where === "study")
        data.input.study_blocks.push({
          start_at: checked,
          end_at: end,
          user_id: other,
          workspace_id: workspace,
          id: "study",
          movable: true,
          calendar_backed: false,
          needs_review: false,
        });
      if (where === "uncertainty")
        data.input.uncertainties.push({
          start_at: checked,
          end_at: end,
          user_id: user,
          workspace_id: other,
          source_id: "student",
          reason: "unavailable_source",
        });
      expect(() =>
        readAvailabilitySnapshot(data, account, preview, now),
      ).toThrow("account");
    },
  );
  it.each([
    "expired",
    "long",
    "future",
    "timezone",
    "routine",
    "preferences",
    "duration",
    "window",
    "secret",
    "title",
    "unknown-source",
    "duplicate",
    "confirmed",
    "result-clock",
  ])("rejects inconsistent snapshot %s", (change) => {
    const data = snapshot();
    if (change === "expired") data.expires_at = checked;
    if (change === "long") data.expires_at = "2026-10-22T12:01:01Z";
    if (change === "future") {
      data.checked_at = "2026-10-22T12:00:06Z";
      data.input.now = data.checked_at;
    }
    if (change === "timezone") data.input.timezone = "Europe/London";
    if (change === "routine") data.input.routine_confirmed = false;
    if (change === "preferences") data.revision.preferences_revision = 3;
    if (change === "duration") data.input.duration_minutes = 60;
    if (change === "window") data.input.start_at = "2026-10-22T12:01:00Z";
    if (change === "secret") Object.assign(data, { access_token: "secret" });
    if (change === "title")
      Object.assign(data.input.busy[0], { title: "Private event" });
    if (change === "unknown-source") data.input.busy[0].source_id = "missing";
    if (change === "duplicate")
      data.input.coverage.push(data.input.coverage[0]);
    if (change === "confirmed")
      data.result.confirmed_openings[0].reasons = ["coverage_unknown"];
    if (change === "result-clock")
      data.result.checked_at = "2026-10-22T11:59:00Z";
    expect(() =>
      readAvailabilitySnapshot(data, account, preview, now),
    ).toThrow();
  });
  it("keeps revision-zero default routine unconfirmed even after an affirmative checkbox", () => {
    const data = snapshot();
    data.input.routine_confirmed = false;
    data.revision.preferences_revision = 0;
    data.result.status = "needs_routine";
    const opening = data.result.confirmed_openings.pop();
    if (!opening) throw new Error("Fixture missing");
    data.result.provisional_openings.push({
      ...opening,
      reasons: ["routine_unconfirmed"],
    });
    const request = { ...preview, preferences_revision: 0 };
    expect(
      readAvailabilitySnapshot(data, account, request, now).result
        .confirmed_openings,
    ).toEqual([]);
    data.input.routine_confirmed = true;
    expect(() =>
      readAvailabilitySnapshot(data, account, request, now),
    ).toThrow();
  });
  it("rejects confirmed openings when the effective routine is unconfirmed", () => {
    const data = snapshot();
    data.input.routine_confirmed = false;
    expect(() =>
      readAvailabilitySnapshot(
        data,
        account,
        { ...preview, routine_confirmed: false },
        now,
      ),
    ).toThrow();
  });
  it("allows a shorter earliest-source expiry", () => {
    const data = snapshot();
    data.expires_at = "2026-10-22T12:00:30Z";
    expect(
      readAvailabilitySnapshot(data, account, preview, now).expires_at,
    ).toBe(data.expires_at);
  });
  it("accepts explicitly configured Outlook and Apple provider status", () => {
    const data = view();
    data.providers[1].availability = "available";
    data.providers[2].availability = "available";
    data.grants[0].provider = "outlook";
    expect(readCalendarView(data).grants[0].provider).toBe("outlook");
  });
  it("binds Microsoft authorization to calendar-only scopes and its exact host", () => {
    const target = new URL(
      "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    );
    for (const [key, value] of Object.entries({
      client_id: "calendar-client",
      response_type: "code",
      redirect_uri: "https://api.example.test/api/v1/outlook/callback",
      scope: MICROSOFT_CALENDAR_READ_SCOPES.join(" "),
      state: "random-state",
      code_challenge: "a".repeat(43),
      code_challenge_method: "S256",
    }))
      target.searchParams.set(key, value);
    const body = {
      ...authorization(),
      authorization_url: target.toString(),
      requested_scopes: [...MICROSOFT_CALENDAR_READ_SCOPES],
    };
    expect(
      readCalendarAuthorization(
        body,
        start,
        MICROSOFT_CALENDAR_READ_SCOPES,
        "outlook",
      ).authorization_url,
    ).toContain("login.microsoftonline.com");
    expect(() =>
      readCalendarAuthorization(
        {
          ...body,
          authorization_url: body.authorization_url.replace(
            "login.microsoftonline.com",
            "login.microsoftonline.com.attacker.test",
          ),
        },
        start,
        MICROSOFT_CALENDAR_READ_SCOPES,
        "outlook",
      ),
    ).toThrow();
    expect(() =>
      readCalendarAuthorization(
        {
          ...body,
          requested_scopes: [...MICROSOFT_CALENDAR_READ_SCOPES, "Mail.Read"],
        },
        start,
        MICROSOFT_CALENDAR_READ_SCOPES,
        "outlook",
      ),
    ).toThrow();
    target.searchParams.append("scope", "Mail.Read");
    expect(() =>
      readCalendarAuthorization(
        { ...body, authorization_url: target.toString() },
        start,
        MICROSOFT_CALENDAR_READ_SCOPES,
        "outlook",
      ),
    ).toThrow();
  });
  it.each(["missing-connection", "secret", "duplicate"])(
    "rejects misleading calendar status %s",
    (change) => {
      const data = view();
      if (change === "missing-connection")
        Object.assign(data.grants[0], { connection_id: null });
      if (change === "secret")
        Object.assign(data.grants[0], { refresh_token: "secret" });
      if (change === "duplicate") data.providers[1] = data.providers[0];
      expect(() => readCalendarView(data)).toThrow();
    },
  );
});
