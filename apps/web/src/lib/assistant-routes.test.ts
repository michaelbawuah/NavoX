import {
  AssistantError,
  type AssistantRuntime,
} from "@navox/assistant-runtime";
import type { AssistantSessionView, AssistantTurnView } from "@navox/contracts";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POST as approveEmailRoute } from "../app/api/v1/assistant/sessions/[sessionId]/email-drafts/[draftId]/approve/route";
import { POST as prepareEmailRoute } from "../app/api/v1/assistant/sessions/[sessionId]/email-drafts/[draftId]/prepare/route";
import { POST as messageRoute } from "../app/api/v1/assistant/sessions/[sessionId]/messages/route";
import {
  DELETE as deleteSessionRoute,
  GET as readSessionRoute,
} from "../app/api/v1/assistant/sessions/[sessionId]/route";
import { POST as createSessionRoute } from "../app/api/v1/assistant/sessions/route";
import { setAssistantRuntimeForTests } from "./assistant-server";

const sessionId = "66666666-6666-4666-8666-666666666666";
const session: AssistantSessionView = {
  id: sessionId,
  created_at: "2026-09-30T12:00:00.000Z",
  updated_at: "2026-09-30T12:00:00.000Z",
  expires_at: "2026-10-30T12:00:00.000Z",
  status: "active",
  turns: [],
};
const turn: AssistantTurnView = {
  id: "88888888-8888-4888-8888-888888888888",
  sequence: 1,
  modality: "TEXT",
  state: "READY",
  question: "What am I missing today?",
  plan: null,
  decision: null,
  presentation: {
    presentation: "TEXT",
    speak: false,
    speech_text: null,
    delivery: "AUTOMATIC",
    blocks: [],
  },
  action_refs: [],
  created_at: "2026-09-30T12:00:00.000Z",
};

function fakeRuntime(
  overrides: Partial<AssistantRuntime> = {},
): AssistantRuntime {
  return {
    createSession: vi.fn(async () => session),
    readSession: vi.fn(async () => session),
    submitTurn: vi.fn(async () => ({
      session_id: sessionId,
      turn,
      replay: false,
    })),
    deleteSession: vi.fn(async () => undefined),
    purgeExpired: vi.fn(async () => 0),
    createEmailDraft: vi.fn(),
    readEmailDraft: vi.fn(),
    reviseEmailDraft: vi.fn(),
    prepareEmailDraft: vi.fn(),
    approveEmailDraft: vi.fn(),
    readEmailAction: vi.fn(),
    ...overrides,
  };
}

interface RequestOptions {
  method?: string;
  origin?: string | null;
  host?: string | null;
  cookie?: string | null;
  contentType?: string | null;
  body?: string;
}

function request(path: string, options: RequestOptions = {}): Request {
  const headers = new Headers();
  if (options.origin !== null)
    headers.set("origin", options.origin ?? "http://localhost:3000");
  if (options.host !== null)
    headers.set("host", options.host ?? "localhost:3000");
  if (options.cookie !== null)
    headers.set("cookie", options.cookie ?? "navox_session=abc123");
  if (options.contentType !== null)
    headers.set("content-type", options.contentType ?? "application/json");
  headers.set("sec-fetch-site", "same-origin");
  return new Request(`http://localhost:3000${path}`, {
    method: options.method ?? "POST",
    headers,
    body: options.method === "GET" ? undefined : (options.body ?? "{}"),
  });
}

const context = { params: Promise.resolve({ sessionId }) };

afterEach(() => setAssistantRuntimeForTests(null));

describe("assistant route handlers", () => {
  it("guards email preparation and approval before reaching the runtime", async () => {
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const actionContext = {
      params: Promise.resolve({
        sessionId,
        draftId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      }),
    };
    const path = `/api/v1/assistant/sessions/${sessionId}/email-drafts/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa`;
    const crossOrigin = await prepareEmailRoute(
      request(`${path}/prepare`, { origin: "https://evil.example" }),
      actionContext,
    );
    expect(crossOrigin.status).toBe(403);
    expect(runtime.prepareEmailDraft).not.toHaveBeenCalled();
    const missingCookie = await approveEmailRoute(
      request(`${path}/approve`, { cookie: null }),
      actionContext,
    );
    expect(missingCookie.status).toBe(401);
    expect(runtime.approveEmailDraft).not.toHaveBeenCalled();
  });
  it("creates a session for a same-origin authenticated caller", async () => {
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const response = await createSessionRoute(
      request("/api/v1/assistant/sessions"),
    );
    expect(response.status).toBe(201);
    expect(response.headers.get("cache-control")).toBe("no-store");
    await expect(response.json()).resolves.toEqual({ session });
    expect(runtime.createSession).toHaveBeenCalledWith({
      cookie: "navox_session=abc123",
    });
  });

  it("refuses a caller without a session cookie", async () => {
    setAssistantRuntimeForTests(fakeRuntime());
    const response = await createSessionRoute(
      request("/api/v1/assistant/sessions", { cookie: null }),
    );
    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "unauthorized" },
    });
  });

  it("answers 401 before reporting an unconfigured runtime", async () => {
    setAssistantRuntimeForTests(null);
    const responded = await createSessionRoute(
      request("/api/v1/assistant/sessions", { cookie: null }),
    );
    expect(responded.status).toBe(401);
  });

  it("refuses a cross-origin mutation and a non-JSON body", async () => {
    setAssistantRuntimeForTests(fakeRuntime());
    const crossOrigin = await createSessionRoute(
      request("/api/v1/assistant/sessions", { origin: "https://evil.example" }),
    );
    expect(crossOrigin.status).toBe(403);
    await expect(crossOrigin.json()).resolves.toMatchObject({
      error: { code: "forbidden" },
    });

    const missingOrigin = await createSessionRoute(
      request("/api/v1/assistant/sessions", { origin: null }),
    );
    expect(missingOrigin.status).toBe(403);

    const plainText = await createSessionRoute(
      request("/api/v1/assistant/sessions", { contentType: "text/plain" }),
    );
    expect(plainText.status).toBe(400);
    await expect(plainText.json()).resolves.toMatchObject({
      error: { code: "invalid_request" },
    });
  });

  it("reads and deletes a session through the same scoped path", async () => {
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const read = await readSessionRoute(
      request(`/api/v1/assistant/sessions/${sessionId}`, {
        method: "GET",
        contentType: null,
      }),
      context,
    );
    expect(read.status).toBe(200);
    await expect(read.json()).resolves.toEqual({ session });

    const removed = await deleteSessionRoute(
      request(`/api/v1/assistant/sessions/${sessionId}`, { method: "DELETE" }),
      context,
    );
    expect(removed.status).toBe(200);
    await expect(removed.json()).resolves.toEqual({
      session_id: sessionId,
      deleted: true,
    });
    expect(runtime.deleteSession).toHaveBeenCalledWith({
      cookie: "navox_session=abc123",
      session_id: sessionId,
    });
  });

  it("reports a deleted or expired session as not found", async () => {
    setAssistantRuntimeForTests(
      fakeRuntime({
        readSession: vi.fn(async () => {
          throw new AssistantError(
            "not_found",
            "That assistant session is no longer available.",
          );
        }),
      }),
    );
    const response = await readSessionRoute(
      request(`/api/v1/assistant/sessions/${sessionId}`, {
        method: "GET",
        contentType: null,
      }),
      context,
    );
    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "not_found" },
    });
  });

  it("passes the parsed body to the runtime and surfaces validation failures", async () => {
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const body = {
      request_id: "77777777-7777-4777-8777-777777777777",
      text: "What am I missing today?",
      modality: "TEXT",
    };
    const response = await messageRoute(
      request(`/api/v1/assistant/sessions/${sessionId}/messages`, {
        body: JSON.stringify(body),
      }),
      context,
    );
    expect(response.status).toBe(200);
    expect(runtime.submitTurn).toHaveBeenCalledWith({
      cookie: "navox_session=abc123",
      session_id: sessionId,
      body,
    });

    setAssistantRuntimeForTests(
      fakeRuntime({
        submitTurn: vi.fn(async () => {
          throw new AssistantError(
            "invalid_request",
            "The assistant request does not accept it.",
          );
        }),
      }),
    );
    const invalid = await messageRoute(
      request(`/api/v1/assistant/sessions/${sessionId}/messages`, {
        body: JSON.stringify({ ...body, workspace_id: "x" }),
      }),
      context,
    );
    expect(invalid.status).toBe(400);
  });

  it("reports malformed JSON as an invalid request", async () => {
    setAssistantRuntimeForTests(fakeRuntime());
    const response = await messageRoute(
      request(`/api/v1/assistant/sessions/${sessionId}/messages`, {
        body: "{",
      }),
      context,
    );
    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "invalid_request" },
    });
  });
});
