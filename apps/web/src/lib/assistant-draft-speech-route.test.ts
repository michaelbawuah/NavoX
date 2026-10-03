import {
  AssistantError,
  type AssistantRuntime,
} from "@navox/assistant-runtime";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as draftSpeechRoute } from "../app/api/v1/assistant/sessions/[sessionId]/email-drafts/[draftId]/speech/route";
import { setAssistantRuntimeForTests } from "./assistant-server";

const sessionId = "66666666-6666-4666-8666-666666666666";
const turnId = "88888888-8888-4888-8888-888888888888";
const draftId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const origin = "http://localhost:3000";
const path = `/api/v1/assistant/sessions/${sessionId}/email-drafts/${draftId}/speech`;
const context = { params: Promise.resolve({ sessionId, draftId }) };
const base = "https://api.navox.example/api/v1";
const MP3 = new Uint8Array([0x49, 0x44, 0x33, 0x04, 0x00]);

function fakeRuntime(
  overrides: Partial<AssistantRuntime> = {},
): AssistantRuntime {
  return {
    readEmailDraftSpeech: vi.fn(async () => ({
      speakable: true as const,
      text: "Re: Renewal. Thanks, Sarah.",
    })),
    ...overrides,
  } as AssistantRuntime;
}

interface RequestOptions {
  method?: string;
  origin?: string | null;
  cookie?: string | null;
  contentType?: string | null;
  body?: unknown;
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
    body: JSON.stringify(
      options.body ?? { source_turn_id: turnId, version: 1 },
    ),
    signal: options.signal,
  });
}

const audio = (body: Uint8Array<ArrayBuffer> = MP3, type = "audio/mpeg") =>
  new Response(body, { status: 200, headers: { "content-type": type } });

afterEach(() => {
  setAssistantRuntimeForTests(null);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete process.env.NAVOX_API_BASE_URL;
});

describe("assistant draft speech route", () => {
  it("sends only the server-derived draft text and returns bounded MP3", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const fetchMock = vi.fn(async () => audio());
    vi.stubGlobal("fetch", fetchMock);

    const response = await draftSpeechRoute(request(), context);

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("audio/mpeg");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(MP3);
    expect(runtime.readEmailDraftSpeech).toHaveBeenCalledWith({
      cookie: "navox_session=abc123",
      session_id: sessionId,
      source_turn_id: turnId,
      draft_id: draftId,
      version: 1,
    });
    const [url, options] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(String(url)).toBe(`${base}/ai/assistant/speech/synthesize`);
    expect(JSON.parse(String(options.body))).toEqual({
      text: "Re: Renewal. Thanks, Sarah.",
    });
  });

  it("refuses a stale version before any provider call", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    setAssistantRuntimeForTests(
      fakeRuntime({
        readEmailDraftSpeech: vi.fn(async () => {
          throw new AssistantError(
            "conflict",
            "The draft changed. Reload it before reading it aloud.",
          );
        }),
      }),
    );
    const fetchMock = vi.fn(async () => audio());
    vi.stubGlobal("fetch", fetchMock);
    const response = await draftSpeechRoute(request(), context);
    expect(response.status).toBe(409);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses an over-long draft without truncating or synthesizing it", async () => {
    process.env.NAVOX_API_BASE_URL = base;
    setAssistantRuntimeForTests(
      fakeRuntime({
        readEmailDraftSpeech: vi.fn(async () => ({
          speakable: false as const,
          reason: "too_long" as const,
        })),
      }),
    );
    const fetchMock = vi.fn(async () => audio());
    vi.stubGlobal("fetch", fetchMock);
    const response = await draftSpeechRoute(request(), context);
    expect(response.status).toBe(422);
    const body = (await response.json()) as {
      error: { code: string; message: string };
    };
    expect(body.error.code).toBe("unsupported");
    expect(body.error.message).toMatch(/longer than the spoken limit/i);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("guards origin, cookie and body before reaching the runtime", async () => {
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    expect(
      (
        await draftSpeechRoute(
          request({ origin: "http://evil.example" }),
          context,
        )
      ).status,
    ).toBe(403);
    expect(
      (await draftSpeechRoute(request({ cookie: null }), context)).status,
    ).toBe(401);
    expect(
      (
        await draftSpeechRoute(
          request({ body: { source_turn_id: turnId, version: "one" } }),
          context,
        )
      ).status,
    ).toBe(400);
    expect(runtime.readEmailDraftSpeech).not.toHaveBeenCalled();
  });
});
