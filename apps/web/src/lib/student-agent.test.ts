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

describe("course-material transport", () => {
  function materialTransport() {
    const read = vi.fn(async (_url: string, _init: RequestInit) =>
      Response.json({ literal: "owned source" }),
    );
    const upstream = createStudentAgentTransport(
      "navox_session=private",
      origin,
      {
        baseUrl: api,
        fetchImpl: read,
      },
    ).upstream;
    if (
      !upstream.materialSearch ||
      !upstream.materials ||
      !upstream.materialPage
    )
      throw new Error("Material transport methods must be available");
    return {
      read,
      upstream: {
        materialSearch: upstream.materialSearch,
        materials: upstream.materials,
        materialPage: upstream.materialPage,
      },
    };
  }

  it("uses fixed owned-course paths and encodes the entire literal search", async () => {
    const { read, upstream } = materialTransport();
    const signal = new AbortController().signal;
    await expect(
      upstream.materialSearch(
        { course_id: other, query: "linear & algebra / café? #1" },
        signal,
      ),
    ).resolves.toEqual({ literal: "owned source" });
    await upstream.materials(other, signal);
    await upstream.materialPage(
      { course_id: other, material_id: turn, revision: 2, page: 3 },
      signal,
    );
    expect(read.mock.calls.map(([url]) => url)).toEqual([
      `${api}/student/courses/${other}/workspace/search?q=linear+%26+algebra+%2F+caf%C3%A9%3F+%231`,
      `${api}/student/courses/${other}/workspace/materials`,
      `${api}/student/courses/${other}/workspace/materials/${turn}/pages/3?revision=2`,
    ]);
    for (const [, init] of read.mock.calls)
      expect(init).toMatchObject({
        method: "GET",
        headers: { cookie: "navox_session=private", origin },
        cache: "no-store",
        redirect: "error",
        signal,
      });
  });

  it.each(["../other-course", "https://evil.test", "", `${other}/materials`])(
    "rejects invalid course identifiers before any request: %s",
    (courseId) => {
      const { read, upstream } = materialTransport();
      const signal = new AbortController().signal;
      expect(() => upstream.materials(courseId, signal)).toThrow();
      expect(() =>
        upstream.materialSearch({ course_id: courseId, query: "test" }, signal),
      ).toThrow();
      expect(() =>
        upstream.materialPage(
          { course_id: courseId, material_id: turn, revision: 1, page: 1 },
          signal,
        ),
      ).toThrow();
      expect(read).not.toHaveBeenCalled();
    },
  );

  it.each(["../secret", "https://evil.test/file", "", `${turn}?revision=9`])(
    "rejects invalid material identifiers before any request: %s",
    (materialId) => {
      const { read, upstream } = materialTransport();
      expect(() =>
        upstream.materialPage(
          { course_id: other, material_id: materialId, revision: 1, page: 1 },
          new AbortController().signal,
        ),
      ).toThrow();
      expect(read).not.toHaveBeenCalled();
    },
  );

  it.each([
    { revision: 0 },
    { revision: -1 },
    { revision: 2_147_483_648 },
    { revision: 1.5 },
    { revision: "1" },
    { page: 0 },
    { page: 101 },
    { page: 1.5 },
    { page: "1" },
  ])("rejects invalid page/version arguments before any request: %j", (bad) => {
    const { read, upstream } = materialTransport();
    expect(() =>
      upstream.materialPage(
        {
          course_id: other,
          material_id: turn,
          revision: 1,
          page: 1,
          ...bad,
        } as Parameters<NonNullable<typeof upstream.materialPage>>[0],
        new AbortController().signal,
      ),
    ).toThrow();
    expect(read).not.toHaveBeenCalled();
  });

  it.each(["", "   ", "x".repeat(101), "topic\nsecret", null, 1])(
    "rejects invalid query values before any request: %s",
    (query) => {
      const { read, upstream } = materialTransport();
      expect(() =>
        upstream.materialSearch(
          { course_id: other, query: query as string },
          new AbortController().signal,
        ),
      ).toThrow();
      expect(read).not.toHaveBeenCalled();
    },
  );
});

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
  it("returns material excerpts without a speech receipt, synthesis or model follow-up", async () => {
    vi.stubEnv("NAVOX_STUDENT_AGENT_MODEL_ENABLED", "true");
    const fallback = fetcher.getMockImplementation();
    const materialId = "66666666-6666-4666-8666-666666666666";
    const base = `/student/courses/${other}/workspace`;
    fetcher.mockImplementation(async (url, init) => {
      const path = String(url).slice(api.length);
      if (path === "/student/courses")
        return Response.json({
          courses: [{ id: other, code: "CS 2110", name: "Programming" }],
        });
      if (path === "/student-agent/select") {
        const selected = JSON.parse(String(init?.body));
        return Response.json({
          turn_id: selected.turn_id,
          step: selected.step,
          manifest_hash: selected.manifest_hash,
          selection: {
            kind: "tool",
            name: "course.materials.search",
            args: { course_id: other, query: "hash tables" },
          },
          task_id: turn,
          trace_id: other,
          estimated_cost: "0.001",
          actions_executed: false,
        });
      }
      if (path === `${base}/search?q=hash+tables`)
        return Response.json({
          course_id: other,
          limit_reached: false,
          results: [
            {
              material_id: materialId,
              revision: 1,
              title: "Lecture notes",
              page: 1,
              snippet: "PRIVATE MATERIAL: hash tables.",
            },
          ],
        });
      if (path === `${base}/materials`)
        return Response.json({
          materials: [
            {
              id: materialId,
              revision: 1,
              title: "Lecture notes",
              page_count: 1,
            },
          ],
        });
      if (!fallback) throw new Error("Original fixed transport is required");
      return fallback(url, init);
    });
    const response = await ask(
      request("ask", {
        ...input,
        question: `Find hash tables in course ${other}`,
        allow_tool_context: true,
      }),
    );
    expect(response.status).toBe(200);
    const result = await response.json();
    expect(result.reply.status, JSON.stringify(result.reply)).toBe("answer");
    expect(result.reply.answer).toContain("PRIVATE MATERIAL");
    expect(result.reply.source).toBe("tools");
    expect(result.receipt_id).toBeNull();
    expect(result.expires_at).toBeNull();
    const selectors = fetcher.mock.calls.filter(([url]) =>
      String(url).endsWith("/student-agent/select"),
    );
    expect(selectors).toHaveLength(1);
    expect(String(selectors[0][1]?.body)).not.toContain("PRIVATE MATERIAL");
    expect(
      fetcher.mock.calls.some(([url]) =>
        /conversation|synthesize/.test(String(url)),
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
