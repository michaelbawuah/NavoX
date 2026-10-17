import type { AssistantRuntime } from "@navox/assistant-runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DELETE as forgetRoute } from "../app/api/v1/student-voice/answers/[answerId]/route";
import { POST as speechRoute } from "../app/api/v1/student-voice/answers/[answerId]/speech/route";
import { POST as askRoute } from "../app/api/v1/student-voice/ask/route";
import { POST as sessionRoute } from "../app/api/v1/student-voice/session/route";
import { setAssistantRuntimeForTests } from "./assistant-server";

const sessionId = "66666666-6666-4666-8666-666666666666";
const userId = "11111111-1111-4111-8111-111111111111";
const workspaceId = "22222222-2222-4222-8222-222222222222";
const origin = "http://localhost:3000";
const api = "https://api.navox.example/api/v1";
const defaultBody = {
  session_id: sessionId,
  request_id: "44444444-4444-4444-8444-444444444444",
  text: "What class do I have now?",
  modality: "VOICE",
  save: false,
};
const occurrence = {
  id: "33333333-3333-4333-8333-333333333333:2026-10-19",
  meeting_id: "33333333-3333-4333-8333-333333333333",
  course_id: "55555555-5555-4555-8555-555555555555",
  course_code: "CS 2110",
  course_name: "Programming",
  start_at: "2026-10-19T14:10:00Z",
  end_at: "2026-10-19T15:00:00Z",
  building: "Gates Hall",
  room: "G01",
  cancelled: false,
};
let lookup: Record<string, unknown>;
let currentUser: string;
let runtime: AssistantRuntime;
let fetcher = vi.fn(async (_url: string | URL, _init?: RequestInit) =>
  Response.json({}),
);

function request(
  path: string,
  body: unknown,
  method = "POST",
  sourceOrigin = origin,
) {
  return new Request(`${origin}${path}`, {
    method,
    headers: {
      origin: sourceOrigin,
      cookie: "navox_session=test",
      "content-type": "application/json",
      "sec-fetch-site": sourceOrigin === origin ? "same-origin" : "cross-site",
    },
    body: JSON.stringify(body),
  });
}

beforeEach(() => {
  currentUser = userId;
  lookup = {
    mode: "now",
    server_now: "2026-10-19T14:30:00Z",
    timezone: "America/New_York",
    date: "2026-10-19",
    at: "2026-10-19T14:30:00Z",
    occurrences: [occurrence],
    next_upcoming: null,
    reason: "matches",
    conflicts: [],
  };
  runtime = {
    createSession: vi.fn(async () => ({ id: sessionId })),
    readSession: vi.fn(async () => ({ id: sessionId })),
    submitTurn: vi.fn(async () => ({
      session_id: sessionId,
      turn: {
        id: "77777777-7777-4777-8777-777777777777",
        state: "READY",
        decision: { reason: "class.student_timetable" },
        presentation: {
          blocks: [
            {
              kind: "ANSWER",
              text: "Right now: CS 2110, 10:10 AM–11:00 AM, Gates Hall G01.",
            },
          ],
        },
      },
      replay: false,
    })),
  } as unknown as AssistantRuntime;
  setAssistantRuntimeForTests(runtime);
  vi.stubEnv("NAVOX_API_BASE_URL", api);
  fetcher = vi.fn(async (input: string | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url === `${api}/auth/me`)
      return Response.json({ id: currentUser, workspace: { id: workspaceId } });
    if (url.startsWith(`${api}/student/lookup?`)) return Response.json(lookup);
    if (url === `${api}/ai/assistant/speech/synthesize`)
      return new Response(new Uint8Array([1, 2, 3]), {
        headers: { "content-type": "audio/mpeg" },
      });
    throw new Error(`Unexpected test transport: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => {
  setAssistantRuntimeForTests(null);
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("privacy-first student quick asks", () => {
  it("opens only owned session metadata, without microphone or model work", async () => {
    const response = await sessionRoute(
      request("/api/v1/student-voice/session", {}),
    );
    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({
      session_id: sessionId,
      account_key: `${workspaceId}:${userId}`,
    });
    expect(runtime.createSession).toHaveBeenCalledOnce();
    expect(runtime.submitTurn).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each(["TEXT", "VOICE"])(
    "answers %s without saving transcript or calling an AI route",
    async (modality) => {
      const response = await askRoute(
        request("/api/v1/student-voice/ask", { ...defaultBody, modality }),
      );
      expect(response.status).toBe(200);
      const body = await response.json();
      expect(body).toMatchObject({
        saved: false,
        kind: "lookup",
        answer: expect.stringContaining("Gates Hall G01"),
        receipt_id: expect.any(String),
      });
      expect(runtime.submitTurn).not.toHaveBeenCalled();
      expect(runtime.createSession).not.toHaveBeenCalled();
      expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
        `${api}/auth/me`,
        `${api}/student/lookup?mode=now`,
      ]);
      expect(response.headers.get("cache-control")).toBe("no-store");
    },
  );
  it("saves only after an explicit boolean opt-in through the existing runtime", async () => {
    const response = await askRoute(
      request("/api/v1/student-voice/ask", { ...defaultBody, save: true }),
    );
    expect(await response.json()).toMatchObject({
      saved: true,
      receipt_id: expect.any(String),
      turn_id: expect.any(String),
      answer: "Right now: CS 2110, 10:10 AM–11:00 AM, Gates Hall G01.",
    });
    expect(runtime.submitTurn).toHaveBeenCalledWith({
      cookie: "navox_session=test",
      session_id: sessionId,
      body: {
        request_id: defaultBody.request_id,
        text: defaultBody.text,
        modality: "VOICE",
        timezone: null,
        referents: [],
      },
    });
  });
  it.each([
    "Add a class CS 2110 on Monday at 10 am",
    "Add ECE 2300 lecture, Tue/Thu 12:30–1:45, Engineering Hall 101.",
    "Add my chem lab, Wednesdays 2:30 to 4:25 in Science Center 210.",
  ])(
    "only reviews a class note despite the save checkbox: %s",
    async (text) => {
      const response = await askRoute(
        request("/api/v1/student-voice/ask", {
          ...defaultBody,
          text,
          save: true,
        }),
      );
      expect(await response.json()).toMatchObject({
        kind: "class_draft",
        saved: false,
        class_draft: text,
      });
      expect(runtime.submitTurn).not.toHaveBeenCalled();
      expect(fetcher).toHaveBeenCalledTimes(1);
    },
  );
  it("clarifies an ambiguous time instead of using AI or assuming AM/PM", async () => {
    const response = await askRoute(
      request("/api/v1/student-voice/ask", {
        ...defaultBody,
        text: "What class do I have at 2?",
      }),
    );
    expect(await response.json()).toMatchObject({
      kind: "clarify",
      saved: false,
      receipt_id: null,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(runtime.submitTurn).not.toHaveBeenCalled();
  });
  it("rejects foreign origins and client authority before reading an account", async () => {
    expect(
      (
        await askRoute(
          request(
            "/api/v1/student-voice/ask",
            defaultBody,
            "POST",
            "https://elsewhere.example",
          ),
        )
      ).status,
    ).toBe(403);
    expect(
      (
        await askRoute(
          request("/api/v1/student-voice/ask", {
            ...defaultBody,
            provider: "openai",
          }),
        )
      ).status,
    ).toBe(400);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("authoritative transient playback", () => {
  async function answerId(save = false) {
    const response = await askRoute(
      request("/api/v1/student-voice/ask", { ...defaultBody, save }),
    );
    return (await response.json()).receipt_id as string;
  }
  const context = (answerId: string) => ({
    params: Promise.resolve({ answerId }),
  });
  it.each([false, true])(
    "derives exact server text after a fresh timetable/owner check (save=%s)",
    async (save) => {
      const id = await answerId(save);
      const response = await speechRoute(
        request(`/api/v1/student-voice/answers/${id}/speech`, {
          session_id: sessionId,
        }),
        context(id),
      );
      expect(response.status).toBe(200);
      expect(response.headers.get("content-type")).toBe("audio/mpeg");
      const synthesisCall = fetcher.mock.calls.find(
        ([url]) => String(url) === `${api}/ai/assistant/speech/synthesize`,
      );
      expect(synthesisCall).toBeDefined();
      expect(JSON.parse(String(synthesisCall?.[1]?.body))).toEqual({
        text: "Right now: CS 2110, 10:10 AM–11:00 AM, Gates Hall G01.",
      });
      expect(runtime.submitTurn).toHaveBeenCalledTimes(save ? 1 : 0);
    },
  );
  it("refuses a different account without sending any text for synthesis", async () => {
    const id = await answerId();
    currentUser = "99999999-9999-4999-8999-999999999999";
    expect(
      (
        await speechRoute(
          request(`/api/v1/student-voice/answers/${id}/speech`, {
            session_id: sessionId,
          }),
          context(id),
        )
      ).status,
    ).toBe(404);
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).includes("/speech/synthesize"),
      ),
    ).toBe(false);
  });
  it.each([false, true])(
    "invalidates a moved class instead of reading an old room (save=%s)",
    async (save) => {
      const id = await answerId(save);
      lookup = { ...lookup, occurrences: [{ ...occurrence, room: "G02" }] };
      const response = await speechRoute(
        request(`/api/v1/student-voice/answers/${id}/speech`, {
          session_id: sessionId,
        }),
        context(id),
      );
      expect(response.status).toBe(404);
      expect(await response.text()).toContain("timetable changed");
      expect(
        fetcher.mock.calls.some(([url]) =>
          String(url).includes("/speech/synthesize"),
        ),
      ).toBe(false);
    },
  );
  it("keeps the same three-play limit after saving a quick ask", async () => {
    const id = await answerId(true);
    for (let count = 0; count < 3; count += 1)
      expect(
        (
          await speechRoute(
            request(`/api/v1/student-voice/answers/${id}/speech`, {
              session_id: sessionId,
            }),
            context(id),
          )
        ).status,
      ).toBe(200);
    const refused = await speechRoute(
      request(`/api/v1/student-voice/answers/${id}/speech`, {
        session_id: sessionId,
      }),
      context(id),
    );
    expect(refused.status).toBe(503);
    expect(await refused.text()).toContain("playback limit");
    expect(
      fetcher.mock.calls.filter(([url]) =>
        String(url).includes("/speech/synthesize"),
      ),
    ).toHaveLength(3);
  });
  it("forgets an answer on close and cannot regenerate it silently", async () => {
    const id = await answerId();
    expect(
      (
        await forgetRoute(
          request(
            `/api/v1/student-voice/answers/${id}`,
            { session_id: sessionId },
            "DELETE",
          ),
          context(id),
        )
      ).status,
    ).toBe(204);
    const response = await speechRoute(
      request(`/api/v1/student-voice/answers/${id}/speech`, {
        session_id: sessionId,
      }),
      context(id),
    );
    expect(response.status).toBe(404);
    expect(await response.text()).toContain("not saved");
  });
});
