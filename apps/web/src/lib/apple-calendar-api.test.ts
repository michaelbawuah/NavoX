import { afterEach, describe, expect, it, vi } from "vitest";
import {
  APPLE_CALENDAR_CONSENT,
  type AppleCalendarConnect,
  createAppleCalendarApi,
  readAppleCalendarView,
} from "./apple-calendar-api";

const account =
  "22222222-2222-4222-8222-222222222222:11111111-1111-4111-8111-111111111111";
const grant = "33333333-3333-4333-8333-333333333333";
const connection = "44444444-4444-4444-8444-444444444444";
const command = {
  generation: 2,
  revision: 4,
  request_id: "55555555-5555-4555-8555-555555555555",
};
const secret = "aaaa-bbbb-cccc-dddd";
const input: AppleCalendarConnect = {
  ...command,
  consent_version: APPLE_CALENDAR_CONSENT,
  confirmed: true as const,
  apple_id: "student@example.test",
  app_password: secret,
};
function view() {
  return {
    generation: 2,
    revision: 5,
    consent_version: APPLE_CALENDAR_CONSENT,
    configured: true,
    grants: [
      {
        id: grant,
        provider: "icloud",
        connection_id: connection,
        account_label: "student@example.test",
        status: "connected",
        revision: 1,
      },
    ],
    changes_applied: false,
  };
}
afterEach(() => vi.restoreAllMocks());

describe("Apple calendar transport", () => {
  it("sends credentials only in the explicit account-bound connect body", async () => {
    const fetch = vi.fn(async () => Response.json(view()));
    const api = createAppleCalendarApi(account, {
      fetch,
      baseUrl: "https://api.example.test/api/v1",
    });
    const result = await api.connect(input, new AbortController().signal);
    expect(result.grants[0].provider).toBe("icloud");
    const [url, options] = fetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(
      "https://api.example.test/api/v1/student/calendars/apple/connect",
    );
    expect(url).not.toContain(secret);
    expect(options).toMatchObject({
      method: "POST",
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      headers: {
        "X-NavoX-Account": account,
        "Content-Type": "application/json",
      },
    });
    expect(JSON.parse(options.body as string)).toEqual(input);
  });

  it("never sends a primary password, unconfirmed consent or an invalid grant path", () => {
    const fetch = vi.fn();
    const api = createAppleCalendarApi(account, { fetch });
    expect(() =>
      api.connect(
        { ...input, app_password: "primary-private-password" },
        new AbortController().signal,
      ),
    ).toThrow();
    expect(() =>
      api.connect(
        { ...input, confirmed: false as true },
        new AbortController().signal,
      ),
    ).toThrow();
    expect(() =>
      api.disconnect("../private", command, new AbortController().signal),
    ).toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("uses the separate Apple disconnect endpoint without sending credentials", async () => {
    const fetch = vi.fn(async () => Response.json({ ...view(), grants: [] }));
    const api = createAppleCalendarApi(account, { fetch });
    await api.disconnect(grant, command, new AbortController().signal);
    const [url, options] = fetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toContain(`/apple/${grant}/disconnect`);
    expect(options.body).toBe(JSON.stringify(command));
    expect(options.body).not.toContain(secret);
  });

  it.each([401, 409, 422, 503])(
    "sanitizes provider and authentication failures (%s)",
    async (status) => {
      const fetch = vi.fn(async () =>
        Response.json({ detail: `private ${secret}` }, { status }),
      );
      const api = createAppleCalendarApi(account, { fetch });
      const failure = await api
        .connect(input, new AbortController().signal)
        .catch((error) => error);
      expect(failure.message).not.toContain(secret);
      expect(failure.message).not.toContain("private");
    },
  );

  it.each([
    { ...view(), grants: [...view().grants, ...view().grants] },
    { ...view(), grants: [{ ...view().grants[0], provider: "google" }] },
    { ...view(), grants: [{ ...view().grants[0], status: "pending" }] },
    {
      ...view(),
      grants: [{ ...view().grants[0], account_label: "private\u0000value" }],
    },
    { ...view(), app_password: secret },
    { ...view(), changes_applied: true },
    { ...view(), revision: -1 },
    { ...view(), grants: Array(9).fill(view().grants[0]) },
  ])("rejects ambiguous or unbounded saved connection views", (value) => {
    expect(() => readAppleCalendarView(value)).toThrow();
  });
});
