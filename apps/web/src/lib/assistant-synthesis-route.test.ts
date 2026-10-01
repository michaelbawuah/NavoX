import {
  AssistantError,
  type AssistantRuntime,
} from "@navox/assistant-runtime";
import type { AssistantSessionView, AssistantTurnView } from "@navox/contracts";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as speechRoute } from "../app/api/v1/assistant/sessions/[sessionId]/turns/[turnId]/speech/route";
import { setAssistantRuntimeForTests } from "./assistant-server";

const sessionId = "77777777-7777-4777-8777-777777777777";
const turnId = "88888888-8888-4888-8888-888888888888";
const origin = "http://localhost:3000";
const path = `/api/v1/assistant/sessions/${sessionId}/turns/${turnId}/speech`;
const context = { params: Promise.resolve({ sessionId, turnId }) };
const base = "https://api.navox.example/api/v1";
const MP3 = new Uint8Array([0x49, 0x44, 0x33, 0x04, 0x00]);

function turn(overrides: Partial<AssistantTurnView> = {}): AssistantTurnView {
  return {
    id: turnId,
    sequence: 1,
    modality: "VOICE",
    state: "READY",
    question: "What am I missing today?",
    plan: null,
    decision: null,
    presentation: {
      presentation: "VOICE",
      speak: true,
      speech_text: "1 item needs attention now.",
      blocks: [{ kind: "ANSWER", text: "1 item needs attention now." }],
    },
    action_refs: [],
    created_at: "2026-09-30T12:00:00.000Z",
    ...overrides,
  };
}

function session(turns: AssistantTurnView[] = [turn()]): AssistantSessionView {
  return {
    id: sessionId,
    created_at: "2026-09-30T12:00:00.000Z",
    updated_at: "2026-09-30T12:00:00.000Z",
    expires_at: "2026-10-30T12:00:00.000Z",
    status: "active",
    turns,
  };
}

function fakeRuntime(
  overrides: Partial<AssistantRuntime> = {},
): AssistantRuntime {
  return {
    createSession: vi.fn(async () => session()),
    readSession: vi.fn(async () => session()),
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
    headers.set("content-type", options.contentType ?? "application/json");
  }
  headers.set("sec-fetch-site", "same-origin");
  return new Request(`${origin}${path}`, {
    method: options.method ?? "POST",
    headers,
    body: JSON.stringify({}),
    signal: options.signal,
  });
}

const audio = (body: Uint8Array<ArrayBuffer> = MP3, type = "audio/mpeg") =>
  new Response(body, { status: 200, headers: { "content-type": type } });

afterEach(() => {
  setAssistantRuntimeForTests(null);
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
  delete process.env.NAVOX_API_BASE_URL;
});

describe("assistant saved-turn speech route", () => {
  it("sends only the server-derived answer and returns bounded MP3", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const fetchMock = vi.fn(async () => audio());
    vi.stubGlobal("fetch", fetchMock);

    const response = await speechRoute(request(), context);

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("audio/mpeg");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(MP3);
    expect(runtime.readSession).toHaveBeenCalledWith({
      cookie: "navox_session=abc123",
      session_id: sessionId,
    });
    const [url, options] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(String(url)).toBe(`${base}/ai/assistant/speech/synthesize`);
    expect(options.method).toBe("POST");
    expect(options.redirect).toBe("error");
    expect(options.cache).toBe("no-store");
    expect((options.headers as Record<string, string>).cookie).toBe(
      "navox_session=abc123",
    );
    // The browser sent selectors; only the derived answer text travels upstream.
    expect(JSON.parse(String(options.body))).toEqual({
      text: "1 item needs attention now.",
    });
  });

  it("derives read-aloud text from a saved typed answer", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    const typed = turn({
      modality: "TEXT",
      presentation: {
        presentation: "TEXT",
        speak: false,
        speech_text: null,
        blocks: [{ kind: "ANSWER", text: "Your next class is at 3 PM." }],
      },
    });
    setAssistantRuntimeForTests(
      fakeRuntime({ readSession: vi.fn(async () => session([typed])) }),
    );
    const fetchMock = vi.fn(async () => audio());
    vi.stubGlobal("fetch", fetchMock);

    const response = await speechRoute(request(), context);

    expect(response.status).toBe(200);
    const [, options] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(String(options.body))).toEqual({
      text: "Your next class is at 3 PM.",
    });
  });

  it("proves session ownership before any answer leaves the process", async () => {
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

    const response = await speechRoute(request(), context);

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("denies cross-origin, missing cookie and unknown turn before upstream", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const crossOrigin = await speechRoute(
      request({ origin: "https://evil.example" }),
      context,
    );
    expect(crossOrigin.status).toBe(403);
    expect(runtime.readSession).not.toHaveBeenCalled();

    const noCookie = await speechRoute(request({ cookie: null }), context);
    expect(noCookie.status).toBe(401);
    expect(runtime.readSession).not.toHaveBeenCalled();

    const wrongTurn = await speechRoute(request(), {
      params: Promise.resolve({ sessionId, turnId: "other-turn" }),
    });
    expect(wrongTurn.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses to speak an answer that would need approval", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    const withheld = turn({
      state: "WITHHELD",
      decision: {
        kind: "WITHHELD",
        capability_id: null,
        target: null,
        reason: "approval.required",
        requires_approval: true,
        action_state: "PENDING_APPROVAL",
        action_id: "action-1",
        response_state: "WITHHELD",
      },
      presentation: {
        presentation: "VOICE",
        speak: false,
        speech_text: null,
        blocks: [{ kind: "ANSWER", text: "Send the email now." }],
      },
    });
    setAssistantRuntimeForTests(
      fakeRuntime({ readSession: vi.fn(async () => session([withheld])) }),
    );
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await speechRoute(request(), context);

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(await response.text()).not.toContain("Send the email");
  });

  it("maps upstream failures without echoing the provider body", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    setAssistantRuntimeForTests(fakeRuntime());
    for (const [status, code] of [
      [401, "unauthorized"],
      [403, "forbidden"],
      [429, "unavailable"],
      [503, "unsupported"],
    ] as const) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response("provider body", { status })),
      );
      const response = await speechRoute(request(), context);
      expect(response.status).toBe(new AssistantError(code, "").status);
      expect(await response.text()).not.toContain("provider body");
    }
  });

  it("refuses a wrong content type and an oversize answer", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    setAssistantRuntimeForTests(fakeRuntime());

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => audio(new Uint8Array([1, 2, 3]), "application/json")),
    );
    const wrongType = await speechRoute(request(), context);
    expect(wrongType.status).toBe(503);

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => audio(new Uint8Array(2 * 1024 * 1024 + 1))),
    );
    const oversize = await speechRoute(request(), context);
    expect(oversize.status).toBe(503);
  });

  it("aborts the upstream request when the caller cancels playback", async () => {
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

    const pending = speechRoute(request({ signal: caller.signal }), context);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const upstreamSignal = fetchMock.mock.calls[0]?.[1].signal;
    caller.abort();

    const response = await pending;
    expect(upstreamSignal?.aborted).toBe(true);
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("aborted");
  });
});
