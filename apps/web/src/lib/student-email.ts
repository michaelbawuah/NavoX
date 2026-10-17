import { studyDeadlineChoices } from "./student-planning";
import {
  readStudentTimetable,
  type StudentCourse,
  studentRequest,
} from "./student-schedule";

export type StudentEmailCategory =
  | "needs_reply"
  | "has_deadline"
  | "fyi"
  | "ignore";
export const studentEmailCategories: Record<StudentEmailCategory, string> = {
  needs_reply: "Needs a reply",
  has_deadline: "Has a deadline",
  fyi: "For your information",
  ignore: "Not an action",
};
export interface StudentEmailSource {
  source_id: string;
  source_hash: string;
  display_label: string | null;
  title: string | null;
  sender_label: string | null;
}
export interface StudentEmailSources {
  sources: StudentEmailSource[];
  limit_reached: boolean;
  notice: string;
}
export interface StudentEmailEvidence {
  source: "subject" | "content";
  quote: string;
  start: number;
  end: number;
}
export interface StudentEmailCorrection {
  category: StudentEmailCategory;
  not_important: boolean;
  course_id: string | null;
  title: string | null;
  due_at: string | null;
  estimated_minutes: number | null;
  confirmed_course: boolean;
  confirmed_deadline: boolean;
}
export interface StudentEmailExistingTask {
  id: string;
  revision: number;
  title: string;
  course_id: string | null;
  status: "active" | "completed" | "archived";
  remaining_minutes: number;
  actual_minutes: number;
}
export interface StudentEmailReview {
  review_id: string;
  revision: number;
  source_id: string;
  source_hash: string;
  expires_at: string;
  classification: {
    category: StudentEmailCategory;
    reasons: string[];
    evidence: StudentEmailEvidence[];
    proposed_deadline: string | null;
    ambiguities: string[];
  };
  correction: StudentEmailCorrection;
  content_truncated: boolean;
  reply_handoff: { commitment_id: string; path: string; notice: string } | null;
  actions_executed: false;
  task_id: string | null;
  existing_task: StudentEmailExistingTask | null;
  notice: string;
}
export interface StudentEmailAnalyze {
  request_id: string;
  source_id: string;
  source_hash: string;
}
export interface StudentEmailCorrectionInput extends StudentEmailCorrection {
  request_id: string;
  revision: number;
}
export interface StudentEmailAccept {
  request_id: string;
  revision: number;
  source_hash: string;
  confirmed: true;
  task_id?: string;
  task_revision?: number;
}
export interface StudentEmailTaskReceipt {
  task_id: string;
  reused: boolean;
  available: boolean;
  actions_executed: false;
  notice: string | null;
}
export interface StudentEmailErase {
  erased: true;
  revision: number;
}
export interface StudentEmailForget {
  erased: true;
  reviews_erased: number;
  derived_tasks_erased: number;
}
export interface StudentEmailHandle {
  review_id: string;
  revision: number;
  source_id: string;
}
export interface StudentEmailContext {
  timezone: string;
  server_now: string;
  courses: StudentCourse[];
}
export interface StudentEmailApi {
  account(signal: AbortSignal): Promise<string>;
  context(signal: AbortSignal): Promise<StudentEmailContext>;
  search(query: string, signal: AbortSignal): Promise<StudentEmailSources>;
  analyze(
    input: StudentEmailAnalyze,
    signal: AbortSignal,
  ): Promise<StudentEmailReview>;
  review(id: string, signal: AbortSignal): Promise<StudentEmailReview>;
  correct(
    id: string,
    input: StudentEmailCorrectionInput,
    signal: AbortSignal,
  ): Promise<StudentEmailReview>;
  accept(
    id: string,
    input: StudentEmailAccept,
    signal: AbortSignal,
  ): Promise<StudentEmailTaskReceipt>;
  erase(
    id: string,
    revision: number,
    signal: AbortSignal,
  ): Promise<StudentEmailErase>;
  forget(sourceId: string, signal: AbortSignal): Promise<StudentEmailForget>;
}
export type StudentEmailPending =
  | { kind: "analyze"; input: StudentEmailAnalyze }
  | { kind: "correct"; review_id: string; input: StudentEmailCorrectionInput }
  | { kind: "accept"; review_id: string; input: StudentEmailAccept }
  | { kind: "erase"; handle: StudentEmailHandle }
  | { kind: "forget"; source_id: string };
export interface StudentEmailState {
  query: string;
  sources: StudentEmailSources | null;
  selected: StudentEmailSource | null;
  readConsent: boolean;
  review: StudentEmailReview | null;
  context: StudentEmailContext | null;
  receipt: StudentEmailTaskReceipt | null;
  handles: StudentEmailHandle[];
  pending: StudentEmailPending | null;
  busy: boolean;
  blocked: boolean;
  error: string;
  notice: string;
  epoch: number;
}
function invalid(): never {
  throw new Error(
    "This email review could not be checked. Reopen its saved version before making a change.",
  );
}
function obj(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 2000): string {
  if (typeof value !== "string" || [...value].length > max) return invalid();
  return value;
}
function num(value: unknown, min = 0, max = 1_000_000): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < min ||
    value > max
  )
    return invalid();
  return value;
}
function bool(value: unknown): boolean {
  if (typeof value !== "boolean") return invalid();
  return value;
}
function list<T>(value: unknown, parse: (item: unknown) => T, max = 1000): T[] {
  if (!Array.isArray(value) || value.length > max) return invalid();
  return value.map(parse);
}
function nullable<T>(value: unknown, parse: (item: unknown) => T): T | null {
  return value === null ? null : parse(value);
}
function id(value: unknown): string {
  const v = text(value, 36);
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      v,
    )
  )
    return invalid();
  return v;
}
function hash(value: unknown): string {
  const v = text(value, 64);
  if (!/^[a-f0-9]{64}$/.test(v)) return invalid();
  return v;
}
function instant(value: unknown): string {
  const v = text(value, 64);
  if (
    !/^\d{4}-\d{2}-\d{2}T/.test(v) ||
    !/(Z|[+-]\d{2}:\d{2})$/.test(v) ||
    !Number.isFinite(Date.parse(v))
  )
    return invalid();
  return v;
}
function category(value: unknown): StudentEmailCategory {
  if (
    !["needs_reply", "has_deadline", "fyi", "ignore"].includes(value as string)
  )
    return invalid();
  return value as StudentEmailCategory;
}
export function readStudentEmailSources(value: unknown): StudentEmailSources {
  const v = obj(value);
  return {
    limit_reached: bool(v.limit_reached),
    notice: text(v.notice),
    sources: list(
      v.sources,
      (item) => {
        const row = obj(item);
        return {
          source_id: id(row.source_id),
          source_hash: hash(row.source_hash),
          title: nullable(row.title, (item) => text(item, 160)),
          sender_label: nullable(row.sender_label, (item) => text(item, 120)),
          display_label: nullable(row.display_label, (item) => text(item, 240)),
        };
      },
      20,
    ),
  };
}
export function readStudentEmailCorrection(
  value: unknown,
): StudentEmailCorrection {
  const v = obj(value);
  return {
    category: category(v.category),
    not_important: bool(v.not_important),
    course_id: nullable(v.course_id, id),
    title: nullable(v.title, (item) => text(item, 200)),
    due_at: nullable(v.due_at, instant),
    estimated_minutes: nullable(v.estimated_minutes, (item) =>
      num(item, 1, 10080),
    ),
    confirmed_course: bool(v.confirmed_course),
    confirmed_deadline: bool(v.confirmed_deadline),
  };
}
export function readStudentEmailReview(value: unknown): StudentEmailReview {
  const v = obj(value),
    classification = obj(v.classification);
  if (v.actions_executed !== false) return invalid();
  const proposed = nullable(classification.proposed_deadline, instant),
    truncated = bool(v.content_truncated);
  if (truncated && proposed !== null) return invalid();
  return {
    review_id: id(v.review_id),
    revision: num(v.revision, 1),
    source_id: id(v.source_id),
    source_hash: hash(v.source_hash),
    expires_at: instant(v.expires_at),
    classification: {
      category: category(classification.category),
      reasons: list(classification.reasons, (item) => text(item, 1000), 3),
      evidence: list(
        classification.evidence,
        (item) => {
          const row = obj(item);
          if (row.source !== "subject" && row.source !== "content")
            return invalid();
          const quote = text(row.quote, 240),
            start = num(row.start, 0, 12000),
            end = num(row.end, 0, 12000);
          if (end < start || end - start !== [...quote].length)
            return invalid();
          return { source: row.source, quote, start, end };
        },
        3,
      ),
      proposed_deadline: proposed,
      ambiguities: list(
        classification.ambiguities,
        (item) => text(item, 1000),
        3,
      ),
    },
    correction: readStudentEmailCorrection(v.correction),
    content_truncated: truncated,
    reply_handoff: nullable(v.reply_handoff, (item) => {
      const row = obj(item),
        commitment = id(row.commitment_id);
      id(row.source_id);
      const path = `/inbox?reply=${commitment}#drafts`;
      if (row.path !== path) return invalid();
      return { commitment_id: commitment, path, notice: text(row.notice) };
    }),
    actions_executed: false,
    task_id: nullable(v.task_id, id),
    existing_task:
      v.existing_task === undefined
        ? null
        : nullable(v.existing_task, (item) => {
            const row = obj(item);
            if (
              !["active", "completed", "archived"].includes(
                row.status as string,
              )
            )
              return invalid();
            return {
              id: id(row.id),
              revision: num(row.revision, 1),
              title: text(row.title, 200),
              course_id: nullable(row.course_id, id),
              status: row.status as StudentEmailExistingTask["status"],
              remaining_minutes: num(row.remaining_minutes, 0, 10080),
              actual_minutes: num(row.actual_minutes),
            };
          }),
    notice: text(v.notice),
  };
}
export function readStudentEmailTaskReceipt(
  value: unknown,
): StudentEmailTaskReceipt {
  const v = obj(value);
  if (v.actions_executed !== false) return invalid();
  return {
    task_id: id(v.task_id),
    reused: bool(v.reused),
    available: bool(v.available),
    actions_executed: false,
    notice: v.notice === undefined ? null : text(v.notice),
  };
}
export function readStudentEmailErase(value: unknown): StudentEmailErase {
  const v = obj(value);
  if (v.erased !== true) return invalid();
  return { erased: true, revision: num(v.revision, 1) };
}
export function readStudentEmailForget(value: unknown): StudentEmailForget {
  const v = obj(value);
  if (v.erased !== true) return invalid();
  return {
    erased: true,
    reviews_erased: num(v.reviews_erased, 0, 1000),
    derived_tasks_erased: num(v.derived_tasks_erased, 0, 200),
  };
}
export function studentEmailCorrectionDraft(
  value: StudentEmailReview,
): StudentEmailCorrection {
  return { ...value.correction };
}
export function studentEmailHandle(
  value: StudentEmailReview,
): StudentEmailHandle {
  return {
    review_id: id(value.review_id),
    revision: num(value.revision, 1),
    source_id: id(value.source_id),
  };
}
export function studentEmailDeadline(
  local: string,
  timezone: string,
  choice: string,
): string {
  const options = studyDeadlineChoices(local, timezone);
  if (!options.length)
    throw new Error(
      "That local time is skipped by a clock change. Choose another deadline.",
    );
  if (options.length > 1 && !options.includes(choice))
    throw new Error(
      "That clock time happens twice. Choose the intended occurrence.",
    );
  return options.length === 1 ? options[0] : choice;
}
export function studentEmailReadyForTask(review: StudentEmailReview): boolean {
  const value = review.correction;
  return (
    !review.task_id &&
    !value.not_important &&
    value.category !== "ignore" &&
    !!value.title?.trim() &&
    value.estimated_minutes !== null &&
    value.confirmed_course &&
    value.confirmed_deadline
  );
}
export class StudentEmailRequestError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly uncertain: boolean,
  ) {
    super(message);
    this.name = "StudentEmailRequestError";
  }
}
const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export async function studentEmailRequest<T>(
  path: string,
  parse: (value: unknown) => T,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  try {
    const response = await fetch(`${apiBaseUrl}/student/email${path}`, {
      method,
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(45_000)])
        : AbortSignal.timeout(45_000),
      ...(body === undefined
        ? {}
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const v =
        payload && typeof payload === "object"
          ? (payload as Record<string, unknown>)
          : null;
      const detail =
        typeof v?.detail === "string" && v.detail.length <= 1000
          ? v.detail
          : null;
      throw new StudentEmailRequestError(
        detail ??
          "This email action could not be checked. Reopen the saved review before trying again.",
        response.status,
        method !== "GET" && (response.status >= 500 || response.status === 408),
      );
    }
    try {
      return parse(payload);
    } catch (cause) {
      throw new StudentEmailRequestError(
        cause instanceof Error
          ? cause.message
          : "This email response could not be checked.",
        response.status,
        method !== "GET",
      );
    }
  } catch (cause) {
    if (cause instanceof StudentEmailRequestError) throw cause;
    throw new StudentEmailRequestError(
      "The result could not be confirmed. Check this same request before starting another.",
      null,
      method !== "GET",
    );
  }
}
export const studentEmailApi: StudentEmailApi = {
  async account(signal) {
    const response = await fetch(`${apiBaseUrl}/auth/me`, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
    });
    if (!response.ok)
      throw new Error("Sign in again before reviewing private email.");
    const v = obj(await response.json());
    return `${id(obj(v.workspace).id)}:${id(v.id)}`;
  },
  async context() {
    const table = await studentRequest(
      "/timetable?days=1",
      readStudentTimetable,
    );
    return {
      timezone: table.timezone,
      server_now: table.server_now,
      courses: table.courses.filter((course) => !course.archived),
    };
  },
  search: (query, signal) =>
    studentEmailRequest(
      `/sources?q=${encodeURIComponent(query)}`,
      readStudentEmailSources,
      "GET",
      undefined,
      signal,
    ),
  analyze: (input, signal) =>
    studentEmailRequest(
      "/analyze",
      readStudentEmailReview,
      "POST",
      input,
      signal,
    ),
  review: (reviewId, signal) =>
    studentEmailRequest(
      `/reviews/${reviewId}`,
      readStudentEmailReview,
      "GET",
      undefined,
      signal,
    ),
  correct: (reviewId, input, signal) =>
    studentEmailRequest(
      `/reviews/${reviewId}`,
      readStudentEmailReview,
      "PUT",
      input,
      signal,
    ),
  accept: (reviewId, input, signal) =>
    studentEmailRequest(
      `/reviews/${reviewId}/accept-task`,
      readStudentEmailTaskReceipt,
      "POST",
      input,
      signal,
    ),
  erase: (reviewId, revision, signal) =>
    studentEmailRequest(
      `/reviews/${reviewId}`,
      readStudentEmailErase,
      "DELETE",
      { revision },
      signal,
    ),
  forget: (sourceId, signal) =>
    studentEmailRequest(
      `/sources/${sourceId}`,
      readStudentEmailForget,
      "DELETE",
      undefined,
      signal,
    ),
};

export function initialStudentEmailState(): StudentEmailState {
  return {
    query: "",
    sources: null,
    selected: null,
    readConsent: false,
    review: null,
    context: null,
    receipt: null,
    handles: [],
    pending: null,
    busy: false,
    blocked: false,
    error: "",
    notice:
      "Choose one saved email item. Nothing is read until you explicitly confirm Analyze.",
    epoch: 0,
  };
}

/** Private, in-memory state. Clearing cancels work and keeps only owned erasure handles. */
export class StudentEmailController {
  private state = initialStudentEmailState();
  private listeners = new Set<() => void>();
  private generation = 0;
  private request: AbortController | null = null;
  private expiry: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  constructor(
    readonly accountId: string,
    private api: StudentEmailApi = studentEmailApi,
    private requestId: () => string = () => crypto.randomUUID(),
  ) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private update(change: Partial<StudentEmailState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...change };
    for (const listener of this.listeners) listener();
  }
  clear = (
    notice = "Private email text was cleared. Reopen a saved review, or search again.",
  ) => {
    ++this.generation;
    this.request?.abort();
    this.request = null;
    if (this.expiry) clearTimeout(this.expiry);
    this.expiry = null;
    const handles = this.state.handles;
    this.update({
      ...initialStudentEmailState(),
      handles,
      notice,
      epoch: this.state.epoch + 1,
    });
  };
  dispose = () => {
    this.clear();
    this.disposed = true;
    this.listeners.clear();
  };
  private retain(review: StudentEmailReview) {
    const handle = studentEmailHandle(review);
    return [
      ...this.state.handles.filter((row) => row.review_id !== handle.review_id),
      handle,
    ].slice(-20);
  }
  private showReview(review: StudentEmailReview, context: StudentEmailContext) {
    const duration =
      Date.parse(review.expires_at) - Date.parse(context.server_now);
    if (!Number.isFinite(duration) || duration <= 0)
      throw new StudentEmailRequestError(
        "This review has expired. Analyze again only if you want to read this message again.",
        410,
        false,
      );
    if (this.expiry) clearTimeout(this.expiry);
    this.expiry = setTimeout(
      () => this.clear("This review expired. Its private text was cleared."),
      Math.min(duration, 20 * 60_000),
    );
    this.update({
      review,
      context,
      handles: this.retain(review),
      receipt: null,
      error: "",
      notice: review.notice,
      epoch: this.state.epoch + 1,
    });
  }
  private async run<T>(
    operation: (signal: AbortSignal) => Promise<T>,
    apply: (value: T) => void,
    pending: StudentEmailPending | null = null,
  ) {
    if (this.disposed || this.state.busy || this.state.blocked) return;
    if (this.state.pending && pending !== this.state.pending) return;
    const generation = ++this.generation,
      request = new AbortController();
    this.request = request;
    this.update({ busy: true, error: "" });
    const current = () =>
      !this.disposed &&
      generation === this.generation &&
      !request.signal.aborted;
    let checked = false;
    try {
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new Error(
          "Your account changed. Private email text was cleared.",
        );
      checked = true;
      let result: T;
      try {
        result = await operation(request.signal);
      } catch (cause) {
        checked = false;
        if (!current()) return;
        if ((await this.api.account(request.signal)) !== this.accountId)
          throw new Error(
            "Your account changed. Private email text was cleared.",
          );
        checked = true;
        throw cause;
      }
      checked = false;
      if (!current()) return;
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new Error(
          "Your account changed. Private email text was cleared.",
        );
      checked = true;
      if (current()) {
        this.update({ pending: null });
        apply(result);
      }
    } catch (cause) {
      if (!current()) return;
      if (!checked) {
        this.clear(
          "Your sign-in could not be checked. Private email text was cleared.",
        );
        this.update({
          blocked: true,
          error: "Reload this page after signing in to the intended account.",
        });
        return;
      }
      if (
        pending &&
        cause instanceof StudentEmailRequestError &&
        cause.uncertain
      ) {
        this.update({
          pending,
          error:
            "The result is unknown. Check this exact request before making another change.",
          readConsent: false,
        });
      } else {
        const message =
          cause instanceof Error
            ? cause.message
            : "This email action could not be checked.";
        const stale =
          cause instanceof StudentEmailRequestError &&
          [401, 403, 404, 409, 410].includes(cause.status ?? 0);
        if (stale) {
          this.clear(
            "The saved review changed or is unavailable. Reopen it before continuing.",
          );
        }
        this.update({ pending: null, error: message });
      }
    } finally {
      if (current()) {
        this.request = null;
        this.update({ busy: false });
      }
    }
  }
  setQuery = (query: string) => {
    if (!this.state.busy && !this.state.pending)
      this.update({ query: query.slice(0, 120) });
  };
  select = (source: StudentEmailSource) => {
    if (this.state.busy || this.state.pending) return;
    const found = this.state.sources?.sources.find(
      (row) =>
        row.source_id === source.source_id &&
        row.source_hash === source.source_hash,
    );
    if (!found) return;
    this.clear("Confirm reading this selected message before Analyze.");
    this.update({ selected: found });
  };
  consent = (value: boolean) => {
    if (!this.state.busy && !this.state.pending)
      this.update({ readConsent: value });
  };
  search = async () => {
    const query = this.state.query.trim();
    if (query.length < 2) {
      this.update({
        error: "Enter at least two characters from a saved email label.",
      });
      return;
    }
    await this.run(
      (signal) => this.api.search(query, signal),
      (sources) => {
        this.update({
          sources,
          selected: null,
          review: null,
          context: null,
          receipt: null,
          readConsent: false,
          notice: sources.notice,
          epoch: this.state.epoch + 1,
        });
      },
    );
  };
  private execute = async (pending: StudentEmailPending) => {
    await this.run(
      async (signal) => {
        if (pending.kind === "erase")
          return {
            kind: "erase" as const,
            result: await this.api.erase(
              pending.handle.review_id,
              pending.handle.revision,
              signal,
            ),
            handle: pending.handle,
          };
        if (pending.kind === "forget")
          return {
            kind: "forget" as const,
            result: await this.api.forget(pending.source_id, signal),
            source_id: pending.source_id,
          };
        if (pending.kind === "accept")
          return {
            kind: "receipt" as const,
            receipt: await this.api.accept(
              pending.review_id,
              pending.input,
              signal,
            ),
          };
        const review =
          pending.kind === "analyze"
            ? await this.api.analyze(pending.input, signal)
            : await this.api.correct(pending.review_id, pending.input, signal);
        if (
          pending.kind === "analyze" &&
          review.source_hash !== pending.input.source_hash
        )
          throw new StudentEmailRequestError(
            "This review does not match the selected email. Check the same request.",
            null,
            true,
          );
        if (
          pending.kind === "analyze" &&
          review.source_id !== pending.input.source_id
        )
          throw new StudentEmailRequestError(
            "This review does not match the selected email. Check the same request.",
            null,
            true,
          );
        if (
          pending.kind === "correct" &&
          review.review_id !== pending.review_id
        )
          throw new StudentEmailRequestError(
            "This review does not match the saved review. Check the same request.",
            null,
            true,
          );
        let context: StudentEmailContext;
        try {
          context = await this.api.context(signal);
        } catch {
          throw new StudentEmailRequestError(
            "The review was saved, but its time zone could not be checked. Check this same request to reopen it.",
            null,
            true,
          );
        }
        return { kind: "review" as const, review, context };
      },
      (result) => {
        if (result.kind === "erase") {
          const handles = this.state.handles.filter(
            (row) => row.review_id !== result.handle.review_id,
          );
          this.clear(
            "The saved review was erased. Any accepted task remains in Planner.",
          );
          this.update({ handles });
        } else if (result.kind === "forget") {
          const handles = this.state.handles.filter(
            (row) => row.source_id !== result.source_id,
          );
          this.clear(
            `${result.result.reviews_erased} review copies and ${result.result.derived_tasks_erased} email-derived tasks were erased. The original email and saved Inbox item remain.`,
          );
          this.update({ handles });
        } else if (result.kind === "receipt") {
          this.update({
            receipt: result.receipt,
            notice: result.receipt.available
              ? "Task saved. Review its study plan in Planner; no message was sent."
              : "That task is no longer available. Reopen this review to check its saved state.",
            epoch: this.state.epoch + 1,
          });
        } else this.showReview(result.review, result.context);
      },
      pending,
    );
  };
  analyze = async () => {
    const { selected, readConsent } = this.state;
    if (!selected || !readConsent || this.state.pending) return;
    this.update({ readConsent: false });
    await this.execute({
      kind: "analyze",
      input: {
        request_id: this.requestId(),
        source_id: selected.source_id,
        source_hash: selected.source_hash,
      },
    });
  };
  reopen = async (reviewId: string) => {
    if (!this.state.handles.some((row) => row.review_id === reviewId)) return;
    await this.run(
      async (signal) => {
        const review = await this.api.review(reviewId, signal);
        return { review, context: await this.api.context(signal) };
      },
      (result) => this.showReview(result.review, result.context),
    );
  };
  correct = async (correction: StudentEmailCorrection) => {
    const review = this.state.review;
    if (!review || review.task_id || this.state.receipt || this.state.pending)
      return;
    const input: StudentEmailCorrectionInput = {
      ...readStudentEmailCorrection(correction),
      request_id: this.requestId(),
      revision: review.revision,
    };
    if (
      input.course_id &&
      !this.state.context?.courses.some(
        (course) => course.id === input.course_id,
      )
    ) {
      this.update({
        error: "Choose a current course you own, or Personal / no course.",
      });
      return;
    }
    await this.execute({ kind: "correct", review_id: review.review_id, input });
  };
  notImportant = async () => {
    const review = this.state.review;
    if (!review) return;
    await this.correct({
      ...review.correction,
      category: "ignore",
      not_important: true,
    });
  };
  accept = async (confirmed: boolean, refresh: boolean) => {
    const review = this.state.review;
    if (
      !confirmed ||
      !review ||
      !studentEmailReadyForTask(review) ||
      this.state.pending ||
      this.state.receipt
    )
      return;
    const existing = review.existing_task;
    if (
      existing &&
      (!refresh ||
        existing.status !== "active" ||
        (existing.course_id &&
          !this.state.context?.courses.some(
            (course) => course.id === existing.course_id,
          )))
    ) {
      this.update({
        error:
          "Only the linked active task can be refreshed. Restore its course or review it in Planner first.",
      });
      return;
    }
    const input: StudentEmailAccept = {
      request_id: this.requestId(),
      revision: review.revision,
      source_hash: review.source_hash,
      confirmed: true,
      ...(existing
        ? { task_id: existing.id, task_revision: existing.revision }
        : {}),
    };
    await this.execute({ kind: "accept", review_id: review.review_id, input });
  };
  checkPending = async () => {
    if (this.state.pending) await this.execute(this.state.pending);
  };
  erase = async (handle: StudentEmailHandle) => {
    const owned = this.state.handles.find(
      (row) => row.review_id === handle.review_id,
    );
    if (!owned || this.state.pending) return;
    await this.execute({ kind: "erase", handle: { ...owned } });
  };
  forget = async (sourceId: string, confirmed: boolean) => {
    if (
      !confirmed ||
      this.state.pending ||
      !this.state.handles.some((row) => row.source_id === sourceId)
    )
      return;
    await this.execute({ kind: "forget", source_id: sourceId });
  };
}

/** Leaving this surface clears excerpts, forms and unsent consent. No browser storage is used. */
export function observeStudentEmailPrivacy(
  clear: () => void,
  surface: {
    window: Pick<Window, "addEventListener" | "removeEventListener">;
    document: Pick<
      Document,
      "addEventListener" | "removeEventListener" | "visibilityState"
    >;
    channel: Pick<
      BroadcastChannel,
      "addEventListener" | "removeEventListener" | "close"
    > | null;
  },
) {
  const hidden = () => {
    if (surface.document.visibilityState !== "visible") clear();
  };
  for (const event of ["blur", "pagehide", "storage"])
    surface.window.addEventListener(event, clear);
  surface.document.addEventListener("visibilitychange", hidden);
  surface.channel?.addEventListener("message", clear);
  return () => {
    for (const event of ["blur", "pagehide", "storage"])
      surface.window.removeEventListener(event, clear);
    surface.document.removeEventListener("visibilitychange", hidden);
    surface.channel?.removeEventListener("message", clear);
    surface.channel?.close();
    clear();
  };
}
