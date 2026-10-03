import {
  AssistantError,
  type AssistantRuntime,
} from "@navox/assistant-runtime";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/api/v1/assistant/class-navigation/route";
import { GET as resourceNavigation } from "../app/api/v1/assistant/navigation/route";
import { setAssistantRuntimeForTests } from "./assistant-server";

const connectionId = "00000000-0000-4000-8000-000000000010";
const resourceId = "00000000-0000-4000-8000-000000000011";
const path = `/api/v1/assistant/class-navigation?connection_id=${connectionId}&resource_id=${resourceId}`;

function request(url = path, cookie = "navox_session=abc123"): Request {
  return new Request(`http://localhost:3000${url}`, {
    headers: cookie ? { cookie } : {},
  });
}

afterEach(() => {
  setAssistantRuntimeForTests(null);
  vi.unstubAllGlobals();
});

describe("guarded class navigation", () => {
  it("resolves an exact source with the session cookie and redirects to its verified URL", async () => {
    const fetcher = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        Response.json({
          url: "https://calendar.google.com/calendar/event?eid=2",
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    const response = await GET(request());
    expect(fetcher).toHaveBeenCalledOnce();
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "https://calendar.google.com/calendar/event?eid=2",
    );
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(fetcher).toHaveBeenCalledOnce();
    const [url, options] = fetcher.mock.calls[0] ?? [];
    expect(String(url)).toContain(
      `/knowledge/class-navigation?connection_id=${connectionId}`,
    );
    expect(options).toMatchObject({
      method: "GET",
      headers: { cookie: "navox_session=abc123" },
      redirect: "manual",
    });
  });

  it("rejects missing login, malformed selectors and unsafe redirect targets", async () => {
    const fetcher = vi.fn(async () =>
      Response.json({ url: "javascript:alert(1)" }),
    );
    vi.stubGlobal("fetch", fetcher);
    expect((await GET(request(path, ""))).status).toBe(401);
    expect(
      (
        await GET(
          request("/api/v1/assistant/class-navigation?connection_id=no"),
        )
      ).status,
    ).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
    const unsafe = await GET(request());
    expect(unsafe.status).toBe(503);
    expect(unsafe.headers.get("location")).toBeNull();
  });

  it("refuses a Canvas or foreign-host class redirect", async () => {
    for (const url of [
      "https://canvas.instructure.com/calendar",
      "https://evil.example/calendar/event",
      "https://mail.google.com/mail/u/0",
    ]) {
      const fetcher = vi.fn(async () => Response.json({ url }));
      vi.stubGlobal("fetch", fetcher);
      const response = await GET(request());
      expect(response.status).toBe(503);
      expect(response.headers.get("location")).toBeNull();
    }
  });
});

const narrationSessionId = "66666666-6666-4666-8666-666666666666";
const narrationTurnId = "88888888-8888-4888-8888-888888888888";
const narrationItemId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const narrationPath = `/api/v1/assistant/navigation?session_id=${narrationSessionId}&turn_id=${narrationTurnId}&item_id=${narrationItemId}`;

function fakeRuntime(
  overrides: Partial<AssistantRuntime> = {},
): AssistantRuntime {
  return {
    resolveNavigationTarget: vi.fn(async () => ({
      url: "https://mail.google.com/mail/u/0/#inbox/message-1",
    })),
    ...overrides,
  } as AssistantRuntime;
}

describe("guarded resource navigation", () => {
  it("redirects only after the runtime re-checks ownership and authority", async () => {
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const response = await resourceNavigation(
      request(narrationPath, "navox_session=abc123"),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(
      "https://mail.google.com/mail/u/0/#inbox/message-1",
    );
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(runtime.resolveNavigationTarget).toHaveBeenCalledWith({
      cookie: "navox_session=abc123",
      session_id: narrationSessionId,
      turn_id: narrationTurnId,
      item_id: narrationItemId,
    });
  });

  it("fails closed without a Location when the target no longer resolves", async () => {
    for (const error of [
      new AssistantError("not_found", "That item is gone."),
      new AssistantError("forbidden", "That source was revoked."),
      new AssistantError("unavailable", "That source changed."),
    ]) {
      setAssistantRuntimeForTests(
        fakeRuntime({
          resolveNavigationTarget: vi.fn(async () => {
            throw error;
          }),
        }),
      );
      const response = await resourceNavigation(
        request(narrationPath, "navox_session=abc123"),
      );
      expect(response.status).toBeGreaterThanOrEqual(400);
      expect(response.headers.get("location")).toBeNull();
    }
  });

  it("requires a login and exact selectors before the runtime is reached", async () => {
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    expect((await resourceNavigation(request(narrationPath, ""))).status).toBe(
      401,
    );
    expect(
      (
        await resourceNavigation(
          request(
            "/api/v1/assistant/navigation?session_id=66666666-6666-4666-8666-666666666666",
            "navox_session=abc123",
          ),
        )
      ).status,
    ).toBeGreaterThanOrEqual(400);
    expect(runtime.resolveNavigationTarget).not.toHaveBeenCalled();
  });
});
