import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DELETE as forget } from "../app/api/v1/student-agent/answers/[answerId]/route";
import { POST as speech } from "../app/api/v1/student-agent/answers/[answerId]/speech/route";
import { POST as ask } from "../app/api/v1/student-agent/ask/route";
import { POST as openSession } from "../app/api/v1/student-agent/session/route";
import {
  createStudentAgentTransport,
  parseStudentAgentTurn,
  StudentAgentPrivateAnswers,
} from "./student-agent";

const user = "11111111-1111-4111-8111-111111111111";
const workspace = "22222222-2222-4222-8222-222222222222";
const session = "33333333-3333-4333-8333-333333333333";
const turn = "44444444-4444-4444-8444-444444444444";
const other = "55555555-5555-4555-8555-555555555555";
const api = "https://api.navox.example/api/v1";
const origin = "http://localhost:3000";
const scope = {
  user_id: user,
  workspace_id: workspace,
  personal: true,
  paused: false,
};
const input = {
  turn_id: turn,
  session_id: session,
  question: "Explain a flip-flop",
  modality: "TEXT",
  allow_tool_context: false,
};
let currentUser = user;
let ownedSession = true;
let modelStatus = 200;
let fetcher = vi.fn(async (_url: string | URL, _init?: RequestInit) =>
  Response.json({}),
);
function request(
  path: string,
  body: unknown,
  method = "POST",
  source = origin,
) {
  return new Request(`${origin}/api/v1/student-agent/${path}`, {
    method,
    headers: {
      origin: source,
      cookie: "navox_session=test",
      "content-type": "application/json",
      "sec-fetch-site": source === origin ? "same-origin" : "cross-site",
    },
    body: JSON.stringify(body),
  });
}
function context(answerId: string) {
  return { params: Promise.resolve({ answerId }) };
}
beforeEach(() => {
  currentUser = user;
  ownedSession = true;
  modelStatus = 200;
  vi.stubEnv("NAVOX_API_BASE_URL", api);
  vi.stubEnv("NAVOX_STUDENT_AGENT_ENABLED", "true");
  vi.stubEnv("NAVOX_STUDENT_AGENT_MODEL_ENABLED", "false");
  fetcher = vi.fn(async (url: string | URL, _init?: RequestInit) => {
    const path = String(url).slice(api.length);
    if (path === "/auth/me")
      return Response.json({
        id: currentUser,
        workspace: { id: workspace, workspace_type: "personal" },
      });
    if (path === "/agent/state") return Response.json({ paused: false });
    if (path === "/ai/sessions")
      return Response.json({ id: session }, { status: 201 });
    if (path === `/student-agent/sessions/${session}`)
      return Response.json(
        ownedSession
          ? {
              session_id: session,
              mode: "PERSONAL",
              status: "active",
              actions_executed: false,
            }
          : {},
        { status: ownedSession ? 200 : 404 },
      );
    if (path === "/ai/assistant/conversation")
      return Response.json(
        modelStatus === 200
          ? {
              task_id: turn,
              trace_id: other,
              session_id: session,
              turn_sequence: 1,
              answer: "A flip-flop stores one bit.",
              actions_executed: false,
            }
          : {},
        { status: modelStatus },
      );
    if (path === "/student-agent/clock")
      return Response.json({
        server_now: "2026-10-19T14:30:00Z",
        timezone: "UTC",
      });
    if (path === "/ai/assistant/speech/synthesize")
      return new Response(new Uint8Array([1, 2, 3]), {
        headers: { "content-type": "audio/mpeg" },
      });
    throw new Error(`Unexpected fixed transport: ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});
async function privateReply() {
  const response = await ask(request("ask", input));
  expect(response.status).toBe(200);
  return response.json();
}
describe("student private route boundaries", () => {
  it("gates every route before network work", async () => {
    vi.stubEnv("NAVOX_STUDENT_AGENT_ENABLED", "false");
    expect((await openSession(request("session", {}))).status).toBe(503);
    expect((await ask(request("ask", input))).status).toBe(503);
    expect(
      (
        await speech(
          request("answers/x/speech", { session_id: session }),
          context(other),
        )
      ).status,
    ).toBe(503);
    expect(
      (
        await forget(
          request("answers/x", { session_id: session }, "DELETE"),
          context(other),
        )
      ).status,
    ).toBe(503);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("creates only session metadata with fixed no-store no-redirect requests", async () => {
    const response = await openSession(request("session", {}));
    expect(response.status).toBe(201);
    expect(await response.json()).toEqual({
      session_id: session,
      account_key: `${workspace}:${user}`,
    });
    for (const [, init] of fetcher.mock.calls)
      expect(init).toMatchObject({ cache: "no-store", redirect: "error" });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it("sends current question and zero history only to existing qualified conversation", async () => {
    const reply = await privateReply();
    expect(reply.reply).toMatchObject({
      source: "question_only_conversation",
      actions_executed: false,
      transcript_saved: false,
      observations: [],
    });
    expect(reply.receipt_id).toMatch(/^[0-9a-f-]{36}$/);
    const call = fetcher.mock.calls.find(([url]) =>
      String(url).endsWith("/ai/assistant/conversation"),
    );
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      session_id: session,
      utterance: input.question,
      recent_turns: [],
    });
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).includes("/student-agent/select"),
      ),
    ).toBe(false);
  });
  it("reads authoritative clock without any model", async () => {
    const result = await (
      await ask(request("ask", { ...input, question: "What time is it?" }))
    ).json();
    expect(result.reply.answer).toContain("2:30 PM");
    expect(result.receipt_id).toBeNull();
    expect(
      fetcher.mock.calls.some(
        ([url]) =>
          String(url).includes("/conversation") ||
          String(url).includes("/select"),
      ),
    ).toBe(false);
  });
  it("checks native session ownership before model or student data", async () => {
    ownedSession = false;
    expect((await ask(request("ask", input))).status).toBe(404);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("reports unconfigured providers honestly without a receipt", async () => {
    modelStatus = 503;
    const result = await (await ask(request("ask", input))).json();
    expect(result.reply.status).toBe("unavailable");
    expect(result.receipt_id).toBeNull();
  });
  it.each([
    { save: true },
    { provider: "openai" },
    { observations: [] },
    { account_id: other },
  ])("rejects client authority %j", async (extra) => {
    expect(() => parseStudentAgentTurn({ ...input, ...extra })).toThrow();
    expect((await ask(request("ask", { ...input, ...extra }))).status).toBe(
      400,
    );
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("rejects cross-origin before upstream work", async () => {
    expect(
      (await ask(request("ask", input, "POST", "https://evil.test"))).status,
    ).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("speaks only server-owned answer text through existing synthesis", async () => {
    const reply = await privateReply();
    const response = await speech(
      request("answers/x/speech", { session_id: session }),
      context(reply.receipt_id),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("audio/mpeg");
    const call = fetcher.mock.calls.find(([url]) =>
      String(url).endsWith("/speech/synthesize"),
    );
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      text: "A flip-flop stores one bit.",
    });
    expect(
      (
        await speech(
          request("answers/x/speech", {
            session_id: session,
            text: "Caller forged answer",
          }),
          context(reply.receipt_id),
        )
      ).status,
    ).toBe(400);
  });
  it("never speaks for a changed owner or closed session", async () => {
    const reply = await privateReply();
    currentUser = other;
    expect(
      (
        await speech(
          request("answers/x/speech", { session_id: session }),
          context(reply.receipt_id),
        )
      ).status,
    ).toBe(404);
    currentUser = user;
    ownedSession = false;
    expect(
      (
        await speech(
          request("answers/x/speech", { session_id: session }),
          context(reply.receipt_id),
        )
      ).status,
    ).toBe(404);
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).endsWith("/speech/synthesize"),
      ),
    ).toBe(false);
  });
  it("forgets owned receipts without regeneration", async () => {
    const reply = await privateReply();
    expect(
      (
        await forget(
          request("answers/x", { session_id: session }, "DELETE"),
          context(reply.receipt_id),
        )
      ).status,
    ).toBe(204);
    expect(
      (
        await speech(
          request("answers/x/speech", { session_id: session }),
          context(reply.receipt_id),
        )
      ).status,
    ).toBe(404);
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).endsWith("/speech/synthesize"),
      ),
    ).toBe(false);
  });
});
describe("bounded private answer memory", () => {
  it("expires at ten minutes, with three plays and absolute retention", () => {
    let now = 0;
    const store = new StudentAgentPrivateAnswers(() => now);
    const receipt = store.put(scope, session, "Private answer");
    for (let play = 0; play < 3; play++)
      expect(store.read(receipt.id, scope, session, true)).toBe(
        "Private answer",
      );
    expect(() => store.read(receipt.id, scope, session, true)).toThrow(
      "playback limit",
    );
    now = 600_000;
    expect(() => store.read(receipt.id, scope, session)).toThrow("expired");
  });
  it("evicts owner answers and binds owner, workspace and session", () => {
    const store = new StudentAgentPrivateAnswers();
    const first = store.put(scope, session, "First");
    for (let i = 0; i < 8; i++) store.put(scope, session, `Answer ${i}`);
    expect(() => store.read(first.id, scope, session)).toThrow("expired");
    const receipt = store.put(scope, session, "Current");
    expect(() =>
      store.read(receipt.id, { ...scope, user_id: other }, session),
    ).toThrow();
    expect(() =>
      store.read(receipt.id, { ...scope, workspace_id: other }, session),
    ).toThrow();
    expect(() => store.read(receipt.id, scope, other)).toThrow();
  });
  it("bounds upstream JSON without following redirects", async () => {
    const transport = createStudentAgentTransport("cookie", origin, {
      baseUrl: api,
      fetchImpl: vi.fn(async (_url, init) => {
        expect(init.redirect).toBe("error");
        return Response.json({ data: "x".repeat(524_289) });
      }),
    });
    await expect(
      transport.upstream.clock(new AbortController().signal),
    ).rejects.toThrow("too large");
  });
});
