import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "../app/api/v1/assistant/class-navigation/route";

const connectionId = "00000000-0000-4000-8000-000000000010";
const resourceId = "00000000-0000-4000-8000-000000000011";
const path = `/api/v1/assistant/class-navigation?connection_id=${connectionId}&resource_id=${resourceId}`;

function request(url = path, cookie = "navox_session=abc123"): Request {
  return new Request(`http://localhost:3000${url}`, {
    headers: cookie ? { cookie } : {},
  });
}

afterEach(() => vi.unstubAllGlobals());

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
});
