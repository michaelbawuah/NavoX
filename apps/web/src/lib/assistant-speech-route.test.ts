import {
  AssistantError,
  type AssistantRuntime,
} from "@navox/assistant-runtime";
import type { AssistantSessionView } from "@navox/contracts";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as transcribeRoute } from "../app/api/v1/assistant/sessions/[sessionId]/speech/transcribe/route";
import { setAssistantRuntimeForTests } from "./assistant-server";
import { encodeWavPcm16 } from "./assistant-wav";

const sessionId = "66666666-6666-4666-8666-666666666666";
const origin = "http://localhost:3000";
const path = `/api/v1/assistant/sessions/${sessionId}/speech/transcribe`;
const context = { params: Promise.resolve({ sessionId }) };
const base = "https://api.navox.example/api/v1";

const session: AssistantSessionView = {
  id: sessionId,
  created_at: "2026-09-30T12:00:00.000Z",
  updated_at: "2026-09-30T12:00:00.000Z",
  expires_at: "2026-10-30T12:00:00.000Z",
  status: "active",
  turns: [],
};

function clip(milliseconds = 1_000): Uint8Array<ArrayBuffer> {
  const frames = Math.round((milliseconds / 1_000) * 16_000);
  const samples = new Float32Array(frames);
  for (let index = 0; index < frames; index += 1) {
    samples[index] = Math.sin((index / 32) * Math.PI) * 0.3;
  }
  return encodeWavPcm16(samples, 16_000);
}

function fakeRuntime(
  overrides: Partial<AssistantRuntime> = {},
): AssistantRuntime {
  return {
    createSession: vi.fn(async () => session),
    readSession: vi.fn(async () => session),
    submitTurn: vi.fn(),
    deleteSession: vi.fn(async () => undefined),
    purgeExpired: vi.fn(async () => 0),
    createEmailDraft: vi.fn(),
    readEmailDraft: vi.fn(),
    reviseEmailDraft: vi.fn(),
    prepareEmailDraft: vi.fn(),
    approveEmailDraft: vi.fn(),
    readEmailAction: vi.fn(),
    ...overrides,
  } as AssistantRuntime;
}

interface RequestOptions {
  method?: string;
  origin?: string | null;
  cookie?: string | null;
  contentType?: string | null;
  body?: Uint8Array<ArrayBuffer>;
  signal?: AbortSignal;
}

function request(options: RequestOptions = {}): Request {
  const headers = new Headers();
  if (options.origin !== null) headers.set("origin", options.origin ?? origin);
  headers.set("host", "localhost:3000");
  if (options.cookie !== null) {
    headers.set("cookie", options.cookie ?? "navox_session=abc123");
  }
  if (options.contentType !== null) {
    headers.set("content-type", options.contentType ?? "audio/wav");
  }
  headers.set("sec-fetch-site", "same-origin");
  return new Request(`${origin}${path}`, {
    method: options.method ?? "POST",
    headers,
    body: options.body ?? clip(),
    signal: options.signal,
  });
}

const json = (payload: unknown, status = 200) =>
  new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });

afterEach(() => {
  setAssistantRuntimeForTests(null);
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete process.env.NAVOX_API_BASE_URL;
});

describe("assistant speech transcription route", () => {
  it("forwards one validated clip and returns only bounded text", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const fetchMock = vi.fn(async (_url: string | URL, _init: RequestInit) =>
      json({ text: "What am I missing today?" }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const audio = clip();

    const response = await transcribeRoute(request({ body: audio }), context);

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({
      text: "What am I missing today?",
    });
    expect(runtime.readSession).toHaveBeenCalledWith({
      cookie: "navox_session=abc123",
      session_id: sessionId,
    });
    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`${base}/ai/assistant/speech/transcribe`);
    expect(options.method).toBe("POST");
    expect(options.redirect).toBe("error");
    expect(options.cache).toBe("no-store");
    expect((options.headers as Record<string, string>).cookie).toBe(
      "navox_session=abc123",
    );
    expect((options.headers as Record<string, string>)["content-type"]).toBe(
      "audio/wav",
    );
    // The exact container bytes travel; nothing is relabelled or re-encoded.
    expect(options.body).toEqual(audio);
  });

  it("proves session ownership before any audio leaves the process", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    setAssistantRuntimeForTests(
      fakeRuntime({
        readSession: vi.fn(async () => {
          throw new AssistantError("not_found", "That conversation is gone.");
        }),
      }),
    );

    const response = await transcribeRoute(request(), context);

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "not_found" },
    });
  });

  it("denies a cross-origin mutation and a missing cookie before reading", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const crossOrigin = await transcribeRoute(
      request({ origin: "https://evil.example" }),
      context,
    );
    expect(crossOrigin.status).toBe(403);
    expect(runtime.readSession).not.toHaveBeenCalled();

    const noCookie = await transcribeRoute(request({ cookie: null }), context);
    expect(noCookie.status).toBe(401);
    expect(runtime.readSession).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a JSON body, a short clip and an oversize clip", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const wrongType = await transcribeRoute(
      request({ contentType: "application/json" }),
      context,
    );
    expect(wrongType.status).toBe(400);
    await expect(wrongType.json()).resolves.toMatchObject({
      error: { message: "A raw audio/wav body is required." },
    });

    const short = await transcribeRoute(request({ body: clip(120) }), context);
    expect(short.status).toBe(400);
    await expect(short.json()).resolves.toMatchObject({
      error: { code: "invalid_request" },
    });

    const oversized = new Uint8Array(1_000_001);
    oversized.set([0x52, 0x49, 0x46, 0x46], 0);
    const tooBig = await transcribeRoute(request({ body: oversized }), context);
    expect(tooBig.status).toBe(400);
    await expect(tooBig.json()).resolves.toMatchObject({
      error: { message: expect.stringMatching(/too large/i) },
    });

    expect(fetchMock).not.toHaveBeenCalled();
    expect(runtime.readSession).not.toHaveBeenCalled();
  });

  it("maps a provider timeout to a typed, retryable fallback", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    setAssistantRuntimeForTests(fakeRuntime());
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: unknown, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init.signal?.addEventListener("abort", () =>
              reject(new Error("aborted")),
            );
          }),
      ),
    );

    const pending = transcribeRoute(request(), context);
    await vi.advanceTimersByTimeAsync(30_000);
    const response = await pending;

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error: {
        code: "unavailable",
        retryable: true,
        message: expect.stringMatching(/took too long/i),
      },
    });
  });

  it("aborts the upstream upload when the caller cancels the request", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    setAssistantRuntimeForTests(fakeRuntime());
    const caller = new AbortController();
    const fetchMock = vi.fn(
      (_url: unknown, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new Error("aborted")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const pending = transcribeRoute(
      request({ signal: caller.signal }),
      context,
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const upstreamSignal = fetchMock.mock.calls[0]?.[1].signal;
    caller.abort();

    const response = await pending;
    expect(upstreamSignal?.aborted).toBe(true);
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("aborted");
  });

  it("refuses malformed and overlong upstream responses without echoing them", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    setAssistantRuntimeForTests(fakeRuntime());

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>proxy</html>")),
    );
    const malformed = await transcribeRoute(request(), context);
    expect(malformed.status).toBe(503);
    expect(await malformed.text()).not.toContain("proxy");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ text: "x".repeat(501) })),
    );
    const overlong = await transcribeRoute(request(), context);
    expect(overlong.status).toBe(503);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ text: "   " })),
    );
    const blank = await transcribeRoute(request(), context);
    expect(blank.status).toBe(503);
  });

  it("reports an unqualified speech provider as a typed fallback", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "no provider" }, 503)),
    );

    const response = await transcribeRoute(request(), context);

    expect(response.status).toBe(422);
    const body = (await response.json()) as {
      error: { code: string; message: string };
    };
    expect(body.error.code).toBe("unsupported");
    expect(body.error.message).toMatch(/type your question instead/i);
    expect(body.error.message).not.toContain("no provider");
  });

  it("maps quota and permission failures without leaking upstream detail", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    setAssistantRuntimeForTests(fakeRuntime());
    for (const [status, code] of [
      [429, "unavailable"],
      [415, "invalid_request"],
      [403, "forbidden"],
      [401, "unauthorized"],
    ] as const) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => json({ detail: "provider body" }, status)),
      );
      const response = await transcribeRoute(request(), context);
      expect(response.status).toBe(new AssistantError(code, "").status);
      expect(await response.text()).not.toContain("provider body");
    }
  });

  it("keeps audio and transcripts out of logs and the response envelope", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    setAssistantRuntimeForTests(fakeRuntime());
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ text: "What is next?" })),
    );
    const spies = [
      vi.spyOn(console, "log"),
      vi.spyOn(console, "info"),
      vi.spyOn(console, "warn"),
      vi.spyOn(console, "error"),
      vi.spyOn(console, "debug"),
    ];

    const response = await transcribeRoute(request(), context);
    const body = await response.text();

    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    expect(body).toBe('{"text":"What is next?"}');
  });
});
