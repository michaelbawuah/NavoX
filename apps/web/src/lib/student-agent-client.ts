/** Browser projection: never retain raw tool records, review hashes, or conversation history. */
export interface StudentAssistantLink {
  href: "/classes" | "/planner" | "/connections";
  label: string;
  note: string;
}
export interface StudentAssistantReply {
  status: "answer" | "review" | "clarify" | "unavailable" | "limit";
  answer: string;
  source: "deterministic" | "tools" | "question_only_conversation" | "none";
  links: StudentAssistantLink[];
}
export interface StudentAssistantEnvelope {
  turn_id: string;
  session_id: string;
  reply: StudentAssistantReply;
  receipt_id: string | null;
}
export interface StudentAssistantQuestion {
  turn_id: string;
  session_id: string;
  question: string;
  modality: "TEXT";
  allow_tool_context: boolean;
}
export interface StudentAssistantApi {
  account(signal: AbortSignal): Promise<string>;
  open(
    signal: AbortSignal,
  ): Promise<{ session_id: string; account_key: string }>;
  ask(
    question: StudentAssistantQuestion,
    signal: AbortSignal,
  ): Promise<StudentAssistantEnvelope>;
  forget(id: string, session: string): Promise<void>;
}
const uuid = (value: unknown): value is string =>
  typeof value === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function invalid(): never {
  throw new Error("This answer could not be checked. Try your question again.");
}
const LINKS: Record<string, StudentAssistantLink> = {
  "classes.lookup": {
    href: "/classes",
    label: "Open Classes",
    note: "Check or correct your timetable.",
  },
  "course.info": {
    href: "/classes",
    label: "Open Classes",
    note: "Review your saved course details.",
  },
  "tasks.read": {
    href: "/planner",
    label: "Open Planner",
    note: "Review and update your saved tasks.",
  },
  "plan.preview": {
    href: "/planner",
    label: "Review a plan in Planner",
    note: "Preview and review every block there before saving a plan.",
  },
  "feeds.read": {
    href: "/connections",
    label: "Open Connections",
    note: "Review your learning calendar feeds.",
  },
  "feed.refresh_review": {
    href: "/connections",
    label: "Review refresh in Connections",
    note: "No refresh has been queued. Confirm it in Connections.",
  },
};
export function readStudentAssistantReply(
  raw: unknown,
  turnId: string,
  sessionId: string,
): StudentAssistantEnvelope {
  if (
    !record(raw) ||
    raw.turn_id !== turnId ||
    raw.session_id !== sessionId ||
    !record(raw.reply)
  )
    invalid();
  const reply = raw.reply;
  if (
    !["answer", "review", "clarify", "unavailable", "limit"].includes(
      String(reply.status),
    ) ||
    typeof reply.answer !== "string" ||
    !reply.answer.trim() ||
    reply.answer.length > 40_000 ||
    reply.actions_executed !== false ||
    reply.transcript_saved !== false ||
    !["deterministic", "tools", "question_only_conversation", "none"].includes(
      String(reply.source),
    ) ||
    !Array.isArray(reply.observations) ||
    reply.observations.length > 4 ||
    (raw.receipt_id !== null && !uuid(raw.receipt_id))
  )
    invalid();
  const links: StudentAssistantLink[] = [];
  for (const item of reply.observations) {
    if (
      !record(item) ||
      typeof item.name !== "string" ||
      !["clock.read", ...Object.keys(LINKS)].includes(item.name) ||
      !["read", "review"].includes(String(item.effect))
    )
      invalid();
    const link = LINKS[item.name];
    if (link && !links.some((existing) => existing.href === link.href))
      links.push({ ...link });
  }
  return {
    turn_id: turnId,
    session_id: sessionId,
    receipt_id: raw.receipt_id as string | null,
    reply: {
      status: reply.status as StudentAssistantReply["status"],
      source: reply.source as StudentAssistantReply["source"],
      answer: reply.answer,
      links,
    },
  };
}
async function json(
  path: string,
  method: "GET" | "POST" | "DELETE",
  body?: unknown,
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetch(path, {
    method,
    credentials: "include",
    cache: "no-store",
    redirect: "error",
    headers:
      body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: signal
      ? AbortSignal.any([signal, AbortSignal.timeout(15_000)])
      : AbortSignal.timeout(10_000),
  });
  if (response.status === 204) return null;
  const result: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const supplied =
      record(result) &&
      record(result.error) &&
      typeof result.error.message === "string"
        ? result.error.message
        : null;
    throw new Error(
      response.status === 401
        ? "Sign in again to ask NavoX."
        : response.status === 404
          ? "This private session is unavailable. Start a new question."
          : supplied && supplied.length <= 500
            ? supplied
            : "NavoX could not complete this request. Try again.",
    );
  }
  return result;
}
export const studentAssistantApi: StudentAssistantApi = {
  async account(signal) {
    const value = await json(
      "/api/v1/student-voice/account",
      "GET",
      undefined,
      signal,
    );
    if (!record(value) || typeof value.account_key !== "string") invalid();
    return value.account_key;
  },
  async open(signal) {
    const value = await json(
      "/api/v1/student-agent/session",
      "POST",
      {},
      signal,
    );
    if (
      !record(value) ||
      !uuid(value.session_id) ||
      typeof value.account_key !== "string"
    )
      invalid();
    return { session_id: value.session_id, account_key: value.account_key };
  },
  async ask(input, signal) {
    return readStudentAssistantReply(
      await json("/api/v1/student-agent/ask", "POST", input, signal),
      input.turn_id,
      input.session_id,
    );
  },
  async forget(id, session) {
    if (uuid(id) && uuid(session))
      await json(
        `/api/v1/student-agent/answers/${encodeURIComponent(id)}`,
        "DELETE",
        { session_id: session },
      );
  },
};
export interface StudentAssistantState {
  question: string;
  toolConsent: boolean;
  busy: boolean;
  blocked: boolean;
  reply: StudentAssistantReply | null;
  error: string;
  notice: string;
}
export function initialStudentAssistantState(): StudentAssistantState {
  return {
    question: "",
    toolConsent: false,
    busy: false,
    blocked: false,
    reply: null,
    error: "",
    notice: "Each question stands on its own.",
  };
}
/** Account-bound RAM and generation fences, shared by UI and offline lifecycle tests. */
export class StudentAssistantController {
  private value = initialStudentAssistantState();
  private generation = 0;
  private controller: AbortController | null = null;
  private session: string | null = null;
  private receipt: { id: string; session: string } | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private listeners = new Set<(value: StudentAssistantState) => void>();
  private disposed = false;
  constructor(
    private readonly accountId: string,
    private readonly toolContextAvailable = false,
    private readonly api: StudentAssistantApi = studentAssistantApi,
    private readonly newId: () => string = () => crypto.randomUUID(),
  ) {}
  current() {
    return this.value;
  }
  subscribe(listener: (value: StudentAssistantState) => void) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
  private emit(next: Partial<StudentAssistantState>) {
    this.value = { ...this.value, ...next };
    if (!this.disposed)
      for (const listener of this.listeners) listener(this.value);
  }
  private forgetReceipt() {
    if (this.receipt) {
      const receipt = this.receipt;
      this.receipt = null;
      void this.api.forget(receipt.id, receipt.session).catch(() => {});
    }
  }
  setQuestion(question: string) {
    if (!this.disposed && !this.value.busy) {
      this.emit({ question: question.slice(0, 500), error: "" });
      if (question && !this.timer) {
        this.timer = setTimeout(
          () => this.clear("Temporary text cleared after ten minutes."),
          600_000,
        );
        this.timer.unref?.();
      }
    }
  }
  setToolConsent(consent: boolean) {
    if (!this.disposed && !this.value.busy)
      this.emit({ toolConsent: this.toolContextAvailable && consent });
  }
  clear(notice = "Temporary question and answer cleared.") {
    this.generation++;
    this.controller?.abort();
    this.controller = null;
    clearTimeout(this.timer);
    this.timer = undefined;
    this.forgetReceipt();
    this.session = null;
    this.emit({ ...initialStudentAssistantState(), notice });
  }
  async recheck() {
    this.clear("Checking your account. Temporary text was cleared.");
    const generation = this.generation;
    const controller = new AbortController();
    this.controller = controller;
    this.emit({ busy: true });
    try {
      const owner = await this.api.account(controller.signal);
      if (this.disposed || generation !== this.generation) return;
      if (owner !== this.accountId)
        throw new Error(
          "Your account changed. Reload this page before asking again.",
        );
      this.emit({
        busy: false,
        blocked: false,
        notice: "Ready for a new question.",
      });
    } catch (error) {
      if (!this.disposed && generation === this.generation)
        this.emit({
          busy: false,
          blocked: true,
          error:
            error instanceof Error
              ? error.message
              : "Sign in again, then reload this page.",
        });
    }
  }
  async ask() {
    if (this.disposed || this.value.busy || this.value.blocked) return;
    const question = this.value.question.trim();
    if (!question) {
      this.emit({ error: "Type a question first." });
      return;
    }
    const consent = this.toolContextAvailable && this.value.toolConsent;
    this.generation++;
    const generation = this.generation;
    this.controller?.abort();
    clearTimeout(this.timer);
    this.timer = undefined;
    this.forgetReceipt();
    const controller = new AbortController();
    this.controller = controller;
    let pendingReceipt: { id: string; session: string } | null = null;
    this.emit({
      question: "",
      toolConsent: false,
      busy: true,
      reply: null,
      error: "",
      notice: "Checking the current question…",
    });
    try {
      const owner = await this.api.account(controller.signal);
      if (this.disposed || generation !== this.generation) return;
      if (owner !== this.accountId) {
        this.session = null;
        this.emit({ blocked: true });
        throw new Error(
          "Your account changed. Reload this page before asking again.",
        );
      }
      if (!this.session) {
        const opened = await this.api.open(controller.signal);
        if (this.disposed || generation !== this.generation) return;
        if (opened.account_key !== this.accountId) {
          this.emit({ blocked: true });
          throw new Error(
            "Your account changed. Reload this page before asking again.",
          );
        }
        this.session = opened.session_id;
      }
      const session = this.session;
      const turnId = this.newId();
      const envelope = await this.api.ask(
        {
          turn_id: turnId,
          session_id: session,
          question,
          modality: "TEXT",
          allow_tool_context: consent,
        },
        controller.signal,
      );
      if (this.disposed || generation !== this.generation) {
        if (envelope.receipt_id)
          void this.api.forget(envelope.receipt_id, session).catch(() => {});
        return;
      }
      pendingReceipt = envelope.receipt_id
        ? { id: envelope.receipt_id, session }
        : null;
      const finalOwner = await this.api.account(controller.signal);
      if (
        this.disposed ||
        generation !== this.generation ||
        finalOwner !== this.accountId
      ) {
        if (envelope.receipt_id)
          void this.api.forget(envelope.receipt_id, session).catch(() => {});
        if (!this.disposed && generation === this.generation) {
          this.clear();
          this.emit({
            blocked: true,
            error:
              "Your account changed. Reload this page before asking again.",
          });
        }
        return;
      }
      this.receipt = pendingReceipt;
      pendingReceipt = null;
      this.emit({
        busy: false,
        reply: envelope.reply,
        notice:
          envelope.reply.status === "clarify"
            ? "Include the course, date or task in your next complete question."
            : envelope.reply.status === "unavailable" ||
                envelope.reply.status === "limit"
              ? "Open Classes or Planner, or try a smaller question."
              : envelope.reply.source === "question_only_conversation"
                ? "AI answer · this question only"
                : "Checked when you asked. Ask again for current details.",
      });
      this.timer = setTimeout(
        () => this.clear("Temporary text cleared after ten minutes."),
        600_000,
      );
      this.timer.unref?.();
    } catch (error) {
      if (pendingReceipt)
        void this.api
          .forget(pendingReceipt.id, pendingReceipt.session)
          .catch(() => {});
      if (!this.disposed && generation === this.generation) {
        this.session = null;
        this.emit({
          busy: false,
          reply: null,
          error:
            error instanceof Error
              ? error.message
              : "This question could not be completed.",
          notice: "Nothing was sent or changed in connected apps.",
        });
      }
    }
  }
  dispose() {
    this.clear();
    this.disposed = true;
    this.listeners.clear();
  }
}
