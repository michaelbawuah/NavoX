import type {
  AnswerCitation,
  AskQueryBody,
  AskResponse,
  AskSessionSummary,
  AskSessionView,
  SearchTurnBody,
} from "@navox/contracts";

export class AskRequestError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function messageFor(status: number): string {
  if (status === 401) return "Sign in to ask about your connected sources.";
  if (status === 403)
    return "This account can’t use grounded answers right now.";
  if (status === 404)
    return "Grounded answers aren’t enabled for this workspace.";
  if (status === 409)
    return "That question was already sent. Open the session to reuse its answer.";
  if (status === 422)
    return "That question or follow-up wasn’t valid. Check the wording and referent.";
  if (status === 429)
    return "Too many questions just now. Please try again shortly.";
  return "We couldn’t answer that question. Please try again.";
}

async function askRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const base =
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
  const response = await fetch(`${base}${path}`, {
    ...options,
    credentials: "include",
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    throw new AskRequestError(response.status, messageFor(response.status));
  }
  return response.json() as Promise<T>;
}

export function newAskRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Keeps one paid request identifier per pending submission.
 *
 * A timeout or transient failure must reuse the same identifier so the server's
 * durable reservation replays instead of buying a second answer. Editing the
 * question, changing the referent or clearing history resolves/forgets it.
 */
export class SubmitLedger {
  private pending: { key: string; requestId: string } | null = null;

  requestIdFor(key: string): string {
    if (this.pending?.key === key) return this.pending.requestId;
    const requestId = newAskRequestId();
    this.pending = { key, requestId };
    return requestId;
  }

  resolve(): void {
    this.pending = null;
  }

  forget(): void {
    this.pending = null;
  }
}

export async function runAsk(
  body: AskQueryBody,
  signal?: AbortSignal,
): Promise<AskResponse> {
  return askRequest<AskResponse>("/knowledge/ask", {
    method: "POST",
    body: JSON.stringify(body),
    signal,
  });
}

export async function recordKnowledgeSearchTurn(
  body: SearchTurnBody,
  signal?: AbortSignal,
): Promise<AskResponse> {
  return askRequest<AskResponse>("/knowledge/sessions/search-turns", {
    method: "POST",
    body: JSON.stringify(body),
    signal,
  });
}

export async function loadAskSessions(
  signal?: AbortSignal,
): Promise<AskSessionSummary[]> {
  return askRequest<AskSessionSummary[]>("/knowledge/sessions", { signal });
}

export async function loadAskSession(
  sessionId: string,
  signal?: AbortSignal,
): Promise<AskSessionView> {
  return askRequest<AskSessionView>(
    `/knowledge/sessions/${encodeURIComponent(sessionId)}`,
    { signal },
  );
}

export async function clearAskHistory(): Promise<{ cleared: number }> {
  return askRequest<{ cleared: number }>("/knowledge/sessions", {
    method: "DELETE",
  });
}

export async function deleteAskSession(
  sessionId: string,
): Promise<{ removed: boolean }> {
  return askRequest<{ removed: boolean }>(
    `/knowledge/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
}

const ANSWER_NOTICES: Record<string, string> = {
  READY: "Every passage below is quoted from a source you can open.",
  INSUFFICIENT:
    "The sources you have authorized don’t cover this question well enough to quote.",
  INCOMPLETE:
    "Some selected evidence could not be used, so this answer is incomplete.",
  UNAVAILABLE: "Answers are unavailable right now. Here’s what search found.",
  WITHHELD:
    "A source behind this answer changed or was revoked, so its text is withheld.",
  NOT_REQUESTED: "Search results recorded in this session.",
};

export function askNotice(response: AskResponse): string | null {
  return ANSWER_NOTICES[response.answer_state] ?? null;
}

export function isAnswered(response: AskResponse): boolean {
  return response.answer_state === "READY" && response.citations.length > 0;
}

export function citationLabel(citation: AnswerCitation): string {
  if (citation.fact_label) return citation.fact_label;
  if (citation.excerpt_index !== null) {
    return `Quoted passage ${citation.excerpt_index + 1}`;
  }
  return "Quoted source";
}

export function coverageNotes(response: AskResponse): string[] {
  const issues = (response.coverage.source_issues ?? []).map(
    (source) =>
      `${source.source_label} could not refresh. This answer may not include its latest changes.`,
  );
  return [
    ...issues,
    ...response.coverage.partial_reasons.map((reason) => {
      if (reason === "ASK_NATIVE_DOMAINS_EXCLUDED")
        return "News records stay in search; they are not quoted in answers.";
      if (reason === "ASK_REFERENT_STALE")
        return "The earlier answer this follow-up refers to is no longer current.";
      if (reason === "ASK_WITHHELD_SOURCE_CHANGED")
        return "One selected source changed or was revoked.";
      if (reason === "ASK_INVALID_SELECTION")
        return "Some selected evidence could not be used.";
      if (reason === "ASK_MISSING_VECTORS")
        return "Some sources have no indexed answer evidence yet.";
      return "This answer does not cover everything you may have connected.";
    }),
  ];
}

export function followupReferent(sequence: number): string {
  return `#${sequence}`;
}
