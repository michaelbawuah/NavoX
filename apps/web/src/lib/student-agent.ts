/** Server-only Phase 2 transport. Every destination is fixed in this module. */
import { randomUUID } from "node:crypto";
import { AssistantError, isRecord, isUuid } from "@navox/assistant-runtime";
import { parseConversationAnswer } from "@navox/assistant-runtime/conversation";
import {
  createStudentToolAgent,
  type StudentAgentScope,
  type StudentAgentTurn,
  type StudentToolUpstream,
} from "@navox/assistant-runtime/student-tools";
import { assistantApiBaseUrl } from "./assistant-server";

const MAX_UPSTREAM_BYTES = 512 * 1024;
type AgentFetch = (input: string, init: RequestInit) => Promise<Response>;

function statusError(status: number): AssistantError {
  return new AssistantError(
    status === 401
      ? "unauthorized"
      : status === 403
        ? "forbidden"
        : status === 404
          ? "not_found"
          : status === 409
            ? "conflict"
            : status === 422
              ? "invalid_request"
              : "unavailable",
    status === 401
      ? "Sign in to ask NavoX."
      : status === 403
        ? "Your account cannot use this student request."
        : status === 404
          ? "This student feature is not available in this deployment."
          : status === 503
            ? "No appropriately qualified model is enabled for this request. Your saved student tools remain available."
            : "The student request could not be completed. Open the student screen to review it.",
  );
}

async function boundedJson(response: Response): Promise<unknown> {
  if (!response.body)
    throw new AssistantError(
      "unavailable",
      "The student service returned no result.",
    );
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value) continue;
    size += value.length;
    if (size > MAX_UPSTREAM_BYTES) {
      await reader.cancel();
      throw new AssistantError(
        "unavailable",
        "This student response is too large. Open the student screen.",
      );
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  try {
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new AssistantError(
      "unavailable",
      "The student service returned an unreadable result.",
    );
  }
}

export function createStudentAgentTransport(
  cookie: string,
  origin: string,
  config: { baseUrl?: string; fetchImpl?: AgentFetch } = {},
) {
  const base = (config.baseUrl ?? assistantApiBaseUrl()).replace(/\/+$/, "");
  const fetcher = config.fetchImpl ?? fetch;
  const send = async (
    path: string,
    method: "GET" | "POST",
    signal: AbortSignal,
    body?: unknown,
  ): Promise<unknown> => {
    const response = await fetcher(`${base}${path}`, {
      method,
      headers: {
        cookie,
        origin,
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      redirect: "error",
      signal,
    });
    if (!response.ok) throw statusError(response.status);
    return boundedJson(response);
  };
  const upstream: StudentToolUpstream = {
    async authorize(signal) {
      const [account, state] = await Promise.all([
        send("/auth/me", "GET", signal),
        send("/agent/state", "GET", signal),
      ]);
      if (
        !isRecord(account) ||
        !isRecord(account.workspace) ||
        !isUuid(account.id) ||
        !isUuid(account.workspace.id) ||
        !isRecord(state) ||
        typeof state.paused !== "boolean"
      )
        throw new AssistantError(
          "forbidden",
          "The current student account could not be verified.",
        );
      return {
        user_id: account.id,
        workspace_id: account.workspace.id,
        personal: account.workspace.workspace_type === "personal",
        paused: state.paused,
      };
    },
    clock: (signal) => send("/student-agent/clock", "GET", signal),
    lookup(args, signal) {
      const params = new URLSearchParams({ mode: args.mode });
      if (args.date !== undefined) params.set("date", args.date);
      if (args.time !== undefined) params.set("time", args.time);
      if (args.day_offset !== undefined)
        params.set("day_offset", String(args.day_offset));
      if (args.weekday !== undefined)
        params.set("weekday", String(args.weekday));
      return send(`/student/lookup?${params.toString()}`, "GET", signal);
    },
    courses: (signal) => send("/student/courses", "GET", signal),
    tasks: (signal) => send("/student/planning/tasks", "GET", signal),
    preview: (args, signal) =>
      send("/student/planning/preview", "POST", signal, {
        days: args.days,
        ...(args.date ? { day: args.date } : {}),
      }),
    feeds: (signal) => send("/student/lms-feeds", "GET", signal),
    select: (input, signal) =>
      send("/student-agent/select", "POST", signal, input),
    async converse(input, signal) {
      // The already-qualified ordinary contract receives ONLY the current question.
      const raw = await send("/ai/assistant/conversation", "POST", signal, {
        session_id: input.session_id,
        utterance: input.question,
        recent_turns: [],
      });
      return parseConversationAnswer(raw, input.session_id).answer;
    },
  };
  return {
    upstream,
    async openSession(signal: AbortSignal) {
      const scope = await upstream.authorize(signal);
      const response = await send("/ai/sessions", "POST", signal);
      if (!isRecord(response) || !isUuid(response.id))
        throw new AssistantError(
          "unavailable",
          "A private assistant session could not be opened.",
        );
      return {
        session_id: response.id,
        account_key: `${scope.workspace_id}:${scope.user_id}`,
      };
    },
    async requireSession(sessionId: string, signal: AbortSignal) {
      if (!isUuid(sessionId))
        throw new AssistantError(
          "invalid_request",
          "Open a new private conversation.",
        );
      const value = await send(
        `/student-agent/sessions/${encodeURIComponent(sessionId)}`,
        "GET",
        signal,
      );
      if (
        !isRecord(value) ||
        value.session_id !== sessionId ||
        value.mode !== "PERSONAL" ||
        value.status !== "active" ||
        value.actions_executed !== false
      )
        throw new AssistantError(
          "forbidden",
          "This private conversation no longer belongs to the current account.",
        );
    },
  };
}

export function studentAgentForRequest(cookie: string, origin: string) {
  requireStudentAgentEnabled();
  const transport = createStudentAgentTransport(cookie, origin);
  return {
    transport,
    agent: createStudentToolAgent(transport.upstream, {
      modelToolsEnabled:
        process.env.NAVOX_STUDENT_AGENT_MODEL_ENABLED === "true",
      ordinaryConversationEnabled: true,
    }),
  };
}

export function requireStudentAgentEnabled(): void {
  if (process.env.NAVOX_STUDENT_AGENT_ENABLED !== "true")
    throw new AssistantError(
      "unavailable",
      "The student agent is not enabled in this deployment yet.",
    );
}

interface PrivateAgentReceipt {
  id: string;
  scope: Pick<StudentAgentScope, "user_id" | "workspace_id">;
  session_id: string;
  text: string;
  expires_at: number;
  plays: number;
}
const RECEIPT_TTL = 600_000;
export class StudentAgentPrivateAnswers {
  private entries = new Map<
    string,
    {
      receipt: PrivateAgentReceipt;
      bytes: number;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  private totalBytes = 0;
  constructor(private readonly now: () => number = Date.now) {}
  private remove(key: string) {
    const row = this.entries.get(key);
    if (row) {
      this.totalBytes -= row.bytes;
      clearTimeout(row.timer);
    }
    this.entries.delete(key);
  }
  private sweep() {
    for (const [key, { receipt }] of this.entries)
      if (receipt.expires_at <= this.now()) this.remove(key);
  }
  put(scope: StudentAgentScope, session_id: string, text: string) {
    this.sweep();
    if (
      !isUuid(scope.user_id) ||
      !isUuid(scope.workspace_id) ||
      !isUuid(session_id) ||
      !text.trim() ||
      text.length > 600
    )
      throw new AssistantError(
        "invalid_request",
        "This answer is not available for spoken playback.",
      );
    const owned = [...this.entries.values()].filter(
      ({ receipt }) =>
        receipt.scope.user_id === scope.user_id &&
        receipt.scope.workspace_id === scope.workspace_id,
    );
    while (owned.length >= 8) {
      const row = owned.shift();
      if (row) this.remove(row.receipt.id);
    }
    const receipt: PrivateAgentReceipt = {
      id: randomUUID(),
      scope: { user_id: scope.user_id, workspace_id: scope.workspace_id },
      session_id,
      text,
      expires_at: this.now() + RECEIPT_TTL,
      plays: 0,
    };
    const size = Buffer.byteLength(JSON.stringify(receipt), "utf8");
    while (this.entries.size >= 128 || this.totalBytes + size > 262_144) {
      const first = this.entries.keys().next().value;
      if (!first) break;
      this.remove(first);
    }
    const timer = setTimeout(() => this.remove(receipt.id), RECEIPT_TTL);
    timer.unref?.();
    this.entries.set(receipt.id, { receipt, bytes: size, timer });
    this.totalBytes += size;
    return {
      id: receipt.id,
      expires_at: new Date(receipt.expires_at).toISOString(),
    };
  }
  read(
    id: string,
    scope: StudentAgentScope,
    session_id: string,
    play = false,
  ): string {
    this.sweep();
    const row = this.entries.get(id)?.receipt;
    if (
      !row ||
      row.session_id !== session_id ||
      row.scope.user_id !== scope.user_id ||
      row.scope.workspace_id !== scope.workspace_id
    )
      throw new AssistantError(
        "not_found",
        "This private answer expired or is no longer on this server. Ask again; its transcript was not saved.",
      );
    if (play && row.plays >= 3)
      throw new AssistantError(
        "unavailable",
        "This answer reached its playback limit. Read it on screen or ask again.",
      );
    if (play) row.plays += 1;
    return row.text;
  }
  forget(id: string, scope: StudentAgentScope, session_id: string) {
    const row = this.entries.get(id)?.receipt;
    if (
      row?.session_id === session_id &&
      row.scope.user_id === scope.user_id &&
      row.scope.workspace_id === scope.workspace_id
    )
      this.remove(id);
  }
}
const shared = globalThis as typeof globalThis & {
  __navoxPrivateAgentAnswers?: StudentAgentPrivateAnswers;
};
export function privateStudentAgentAnswers() {
  shared.__navoxPrivateAgentAnswers ??= new StudentAgentPrivateAnswers();
  return shared.__navoxPrivateAgentAnswers;
}

export function parseStudentAgentTurn(value: unknown): StudentAgentTurn {
  if (
    !isRecord(value) ||
    Object.keys(value).some(
      (key) =>
        ![
          "turn_id",
          "session_id",
          "question",
          "modality",
          "allow_tool_context",
        ].includes(key),
    ) ||
    !isUuid(value.turn_id) ||
    !isUuid(value.session_id) ||
    typeof value.question !== "string" ||
    !value.question.trim() ||
    value.question.length > 500 ||
    (value.modality !== "TEXT" && value.modality !== "VOICE") ||
    typeof value.allow_tool_context !== "boolean"
  )
    throw new AssistantError(
      "invalid_request",
      "Ask a question of up to 500 characters in a private conversation.",
    );
  return {
    turn_id: value.turn_id,
    session_id: value.session_id,
    question: value.question.trim(),
    modality: value.modality,
    allow_tool_context: value.allow_tool_context,
  };
}
