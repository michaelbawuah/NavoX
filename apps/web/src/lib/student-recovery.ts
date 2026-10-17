import { type CourseQuote, readCourseQuote } from "./student-coursework";
import {
  readStudyPlanResult,
  type StudyPlanResult,
  studyDeadlineChoices,
} from "./student-planning";

export type RecoveryTrigger = {
  kind: "reported_absence" | "overdue_task" | "skipped_blocks";
  course_ids: string[];
  from_day: string | null;
  through_day: string | null;
  task_id: string | null;
  task_revision: number | null;
};
export interface RecoveryWorkChoice {
  task_id?: string;
  task_revision?: number;
  material_id?: string;
  material_revision?: number;
  course_id?: string;
  remaining_minutes: number;
  priority: number;
  personal_catchup_target: string;
}
export interface RecoveryPolicy {
  course_id: string;
  kind: "late_work" | "extension";
  source: CourseQuote;
}
export interface RecoveryInput {
  request_id: string;
  trigger: RecoveryTrigger;
  days: number;
  work: RecoveryWorkChoice[];
  policies: RecoveryPolicy[];
}
export type RecoveryEvidence =
  | {
      kind: "student_reported_absence";
      course_id: string;
      scheduled_start_at: string;
      scheduled_end_at: string;
      attendance_verified: false;
    }
  | {
      kind: "saved_overdue_task";
      task_id: string;
      revision: number;
      saved_due_at: string;
      remaining_minutes: number;
    }
  | {
      kind: "recorded_skipped_block";
      block_id: string;
      task_id: string;
      revision: number;
      outcome_at: string;
    };
export interface RecoveryMaterial {
  id: string;
  course_id: string;
  revision: number;
  title: string;
  kind: "note" | "material" | "syllabus";
  claim: "available_saved_material_only";
}
export interface RecoveryWork {
  id: string;
  task_id: string | null;
  material_id: string | null;
  course_id: string | null;
  title: string;
  remaining_minutes: number;
  priority: number;
  official_due_at: string | null;
  deadline_source: "school_assignment" | "student_recorded" | "not_recorded";
  official_deadline_status: "overdue" | "upcoming" | "not_recorded";
  personal_catchup_target: string;
  source_revision: number;
  deadline_notice: string;
}
export interface RecoveryView {
  preview_id: string;
  preview_hash: string;
  expires_at: string;
  server_now: string;
  timezone: string;
  trigger: RecoveryTrigger;
  evidence: RecoveryEvidence[];
  available_materials: RecoveryMaterial[];
  policies: RecoveryPolicy[];
  work: RecoveryWork[];
  result: StudyPlanResult;
  unknowns: string[];
  proposal_only: true;
  requires_student_review: true;
  official_deadlines_changed: false;
  result_deadline_basis: "personal_catchup_target";
}
export interface RecoveryExtensionInput {
  request_id: string;
  preview_hash: string;
  work_id: string;
  recipient: {
    course_id: string;
    name: string;
    email: string;
    confirmed: true;
    source: CourseQuote | null;
  };
  student_reason: string;
  requested_extension_until: string;
  policy: RecoveryPolicy | null;
}
export interface RecoveryExtension {
  preview_id: string;
  preview_hash: string;
  expires_at: string;
  timezone: string;
  recipient: string;
  recipient_name: string;
  recipient_source: CourseQuote | null;
  subject: string;
  body: string;
  student_reported_reason: string;
  official_due_at: string;
  deadline_source: "school_assignment" | "student_recorded";
  requested_extension_until: string;
  policy: RecoveryPolicy | null;
  notices: string[];
  editable_preview_only: true;
  sent: false;
  external_draft_created: false;
}
export interface RecoveryReceipt {
  preview_id: string;
  preview_hash: string;
}
export interface RecoveryHandle extends RecoveryReceipt {
  kind: "recovery" | "extension";
  parent_id: string | null;
}
export function recoveryErasedHandles(
  handles: RecoveryHandle[],
  id: string,
): RecoveryHandle[] {
  return handles.filter((row) => row.preview_id !== id && row.parent_id !== id);
}
export function recoveryReviewMilliseconds(value: RecoveryView): number {
  return Math.max(
    0,
    Math.min(
      Date.parse(value.expires_at),
      ...value.result.blocks.map((row) => Date.parse(row.start_at)),
      ...value.work.flatMap((row) =>
        row.official_deadline_status === "upcoming" && row.official_due_at
          ? [Date.parse(row.official_due_at)]
          : [],
      ),
    ) - Date.parse(value.server_now),
  );
}
export function recoveryUnfittedReason(
  reason: StudyPlanResult["unfitted_work"][number]["reason"],
): string {
  return {
    deadline_passed: "Your personal catch-up target has passed",
    cannot_meet_deadline:
      "Not enough free time before your personal catch-up target",
    outside_horizon: "Some catch-up work falls beyond this proposal",
    insufficient_time: "Not enough free time in this proposal",
  }[reason];
}
function invalid(): never {
  throw new Error(
    "This recovery preview could not be checked. Review your saved sources and request a new proposal.",
  );
}
function obj(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 2000): string {
  if (typeof value !== "string" || value.length > max) return invalid();
  return value;
}
function num(value: unknown, min = 0, max = 10080): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < min ||
    value > max
  )
    return invalid();
  return value;
}
function en<T extends string>(value: unknown, allowed: readonly T[]): T {
  if (!allowed.includes(value as T)) return invalid();
  return value as T;
}
function list<T>(value: unknown, parse: (item: unknown) => T, max = 1000): T[] {
  if (!Array.isArray(value) || value.length > max) return invalid();
  return value.map(parse);
}
function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null {
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
function zone(value: unknown): string {
  const v = text(value, 100);
  try {
    new Intl.DateTimeFormat("en", { timeZone: v }).format();
    return v;
  } catch {
    return invalid();
  }
}
function date(value: unknown): string {
  const v = text(value, 10);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(v) ||
    !Number.isFinite(Date.parse(v)) ||
    new Date(v).toISOString().slice(0, 10) !== v
  )
    return invalid();
  return v;
}
export function readRecoveryTrigger(value: unknown): RecoveryTrigger {
  const v = obj(value);
  return {
    kind: en(v.kind, ["reported_absence", "overdue_task", "skipped_blocks"]),
    course_ids: list(v.course_ids, id, 10),
    from_day: nullable(v.from_day, date),
    through_day: nullable(v.through_day, date),
    task_id: nullable(v.task_id, id),
    task_revision: nullable(v.task_revision, (item) => num(item, 1, 1_000_000)),
  };
}
export function readRecoveryPolicy(value: unknown): RecoveryPolicy {
  const v = obj(value);
  return {
    course_id: id(v.course_id),
    kind: en(v.kind, ["late_work", "extension"]),
    source: readCourseQuote(v.source),
  };
}
function evidence(value: unknown): RecoveryEvidence {
  const v = obj(value);
  if (v.kind === "student_reported_absence") {
    if (v.attendance_verified !== false) return invalid();
    return {
      kind: v.kind,
      course_id: id(v.course_id),
      scheduled_start_at: instant(v.scheduled_start_at),
      scheduled_end_at: instant(v.scheduled_end_at),
      attendance_verified: false,
    };
  }
  if (v.kind === "saved_overdue_task")
    return {
      kind: v.kind,
      task_id: id(v.task_id),
      revision: num(v.revision, 1, 1_000_000),
      saved_due_at: instant(v.saved_due_at),
      remaining_minutes: num(v.remaining_minutes, 1),
    };
  if (v.kind === "recorded_skipped_block")
    return {
      kind: v.kind,
      block_id: id(v.block_id),
      task_id: id(v.task_id),
      revision: num(v.revision, 1, 1_000_000),
      outcome_at: instant(v.outcome_at),
    };
  return invalid();
}
function work(value: unknown): RecoveryWork {
  const v = obj(value);
  if (
    (v.task_id === null) === (v.material_id === null) ||
    (v.material_id !== null && v.course_id === null)
  )
    return invalid();
  const due = nullable(v.official_due_at, instant),
    status = en(v.official_deadline_status, [
      "overdue",
      "upcoming",
      "not_recorded",
    ]),
    source = en(v.deadline_source, [
      "school_assignment",
      "student_recorded",
      "not_recorded",
    ]);
  if (
    (due === null) !== (status === "not_recorded") ||
    (due === null) !== (source === "not_recorded")
  )
    return invalid();
  return {
    id: id(v.id),
    task_id: nullable(v.task_id, id),
    material_id: nullable(v.material_id, id),
    course_id: nullable(v.course_id, id),
    title: text(v.title, 1000),
    remaining_minutes: num(v.remaining_minutes, 1),
    priority: num(v.priority, 1, 5),
    official_due_at: due,
    official_deadline_status: status,
    deadline_source: source,
    personal_catchup_target: instant(v.personal_catchup_target),
    source_revision: num(v.source_revision, 1, 1_000_000),
    deadline_notice: text(v.deadline_notice),
  };
}
export function readRecoveryView(value: unknown): RecoveryView {
  const v = obj(value);
  if (
    v.proposal_only !== true ||
    v.requires_student_review !== true ||
    v.official_deadlines_changed !== false ||
    v.result_deadline_basis !== "personal_catchup_target"
  )
    return invalid();
  const now = instant(v.server_now),
    rows = list(v.work, work, 20);
  if (
    Date.parse(instant(v.expires_at)) <= Date.parse(now) ||
    rows.some(
      (row) => Date.parse(row.personal_catchup_target) <= Date.parse(now),
    )
  )
    return invalid();
  const result = readStudyPlanResult(v.result),
    workIds = new Set(rows.map((row) => row.id));
  if (
    result.blocks.some((row) => !workIds.has(row.task_id)) ||
    result.unfitted_work.some((row) => !workIds.has(row.task_id))
  )
    return invalid();
  if (
    rows.some(
      (row) =>
        row.official_due_at &&
        (row.official_deadline_status === "overdue") !==
          Date.parse(row.official_due_at) <= Date.parse(now),
    )
  )
    return invalid();
  return {
    preview_id: id(v.preview_id),
    preview_hash: hash(v.preview_hash),
    expires_at: instant(v.expires_at),
    server_now: now,
    timezone: zone(v.timezone),
    trigger: readRecoveryTrigger(v.trigger),
    evidence: list(v.evidence, evidence, 1000),
    available_materials: list(
      v.available_materials,
      (item) => {
        const row = obj(item);
        if (row.claim !== "available_saved_material_only") return invalid();
        return {
          id: id(row.id),
          course_id: id(row.course_id),
          revision: num(row.revision, 1, 1_000_000),
          title: text(row.title, 160),
          kind: en(row.kind, ["note", "material", "syllabus"]),
          claim: "available_saved_material_only",
        };
      },
      120,
    ),
    policies: list(v.policies, readRecoveryPolicy, 10),
    work: rows,
    result,
    unknowns: list(v.unknowns, (item) => text(item, 2000), 50),
    proposal_only: true,
    requires_student_review: true,
    official_deadlines_changed: false,
    result_deadline_basis: "personal_catchup_target",
  };
}
export function readRecoveryExtension(value: unknown): RecoveryExtension {
  const v = obj(value);
  if (
    v.editable_preview_only !== true ||
    v.sent !== false ||
    v.external_draft_created !== false
  )
    return invalid();
  return {
    preview_id: id(v.preview_id),
    preview_hash: hash(v.preview_hash),
    expires_at: instant(v.expires_at),
    timezone: zone(v.timezone),
    recipient: text(v.recipient, 320),
    recipient_name: text(v.recipient_name, 120),
    recipient_source: nullable(v.recipient_source, readCourseQuote),
    subject: text(v.subject, 200),
    body: text(v.body, 10000),
    student_reported_reason: text(v.student_reported_reason, 1000),
    official_due_at: instant(v.official_due_at),
    deadline_source: en(v.deadline_source, [
      "school_assignment",
      "student_recorded",
    ]),
    requested_extension_until: instant(v.requested_extension_until),
    policy: nullable(v.policy, readRecoveryPolicy),
    notices: list(v.notices, (item) => text(item, 2000), 20),
    editable_preview_only: true,
    sent: false,
    external_draft_created: false,
  };
}
export function recoveryReceipt(value: RecoveryReceipt): RecoveryReceipt {
  return {
    preview_id: id(value.preview_id),
    preview_hash: hash(value.preview_hash),
  };
}
export function recoveryTarget(
  local: string,
  timezone: string,
  choice: string,
): string {
  const options = studyDeadlineChoices(local, timezone);
  if (!options.length)
    throw new Error(
      "That local time is skipped by a clock change. Choose another catch-up time.",
    );
  if (options.length > 1 && !options.includes(choice))
    throw new Error(
      "That time happens twice during a clock change. Choose the intended occurrence.",
    );
  return options.length === 1 ? options[0] : choice;
}
export function recoveryExtensionText(value: {
  recipient: string;
  subject: string;
  body: string;
}): string {
  return `To: ${value.recipient}\nSubject: ${value.subject}\n\n${value.body}`;
}
export function recoveryRecipientSource(
  email: string,
  source: CourseQuote | null,
): CourseQuote | null {
  if (!source) return null;
  const normalize = (mailbox: string) => {
    const at = mailbox.lastIndexOf("@");
    return at > 0
      ? `${mailbox.slice(0, at)}@${mailbox.slice(at + 1).toLowerCase()}`
      : mailbox;
  };
  const expected = normalize(email);
  const tokens =
    source.quote.match(/[^\s<>()[\],;:"]+@[^\s<>()[\],;:"]+/g) ?? [];
  return tokens.some(
    (token) => normalize(token.replace(/[.!?]+$/, "")) === expected,
  )
    ? source
    : null;
}
export class RecoveryClipboardError extends Error {
  constructor() {
    super(
      "Clipboard access is unavailable. Your edits are still here. Select and copy the draft text manually.",
    );
    this.name = "RecoveryClipboardError";
  }
}
export async function recoveryCopyExtension(
  value: { recipient: string; subject: string; body: string },
  write: (text: string) => Promise<void>,
): Promise<void> {
  try {
    await write(recoveryExtensionText(value));
  } catch {
    throw new RecoveryClipboardError();
  }
}
export class RecoveryRequestError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly uncertain: boolean,
  ) {
    super(message);
    this.name = "RecoveryRequestError";
  }
}
const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export async function recoveryAccount(): Promise<string> {
  try {
    const response = await fetch(`${apiBaseUrl}/auth/me`, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(12_000),
    });
    if (!response.ok) throw new Error();
    const value = obj(await response.json()),
      workspace = obj(value.workspace);
    return `${id(workspace.id)}:${id(value.id)}`;
  } catch {
    throw new Error(
      "Your account could not be checked. Sign in again before reviewing private recovery work.",
    );
  }
}
export async function recoveryRequest<T>(
  path: string,
  parse: (value: unknown) => T,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 20_000);
  try {
    const response = await fetch(`${apiBaseUrl}/student/recovery${path}`, {
      method,
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
      ...(body === undefined
        ? {}
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
    const payload: unknown =
      response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      const detail =
        payload &&
        typeof payload === "object" &&
        "detail" in payload &&
        typeof payload.detail === "string" &&
        payload.detail.length <= 1000
          ? payload.detail
          : null;
      throw new RecoveryRequestError(
        detail ??
          (response.status === 401
            ? "Sign in again to review recovery work."
            : response.status === 404
              ? "Recovery previews are not available on this deployment."
              : "This recovery request could not be completed. Check your sources and try again."),
        response.status,
        method !== "GET" && response.status >= 500,
      );
    }
    try {
      return parse(payload);
    } catch (cause) {
      throw new RecoveryRequestError(
        cause instanceof Error
          ? cause.message
          : "This recovery response could not be checked.",
        response.status,
        method !== "GET",
      );
    }
  } catch (cause) {
    if (cause instanceof RecoveryRequestError) throw cause;
    throw new RecoveryRequestError(
      "The recovery result could not be checked. Check this same request before starting another.",
      null,
      method !== "GET",
    );
  } finally {
    clearTimeout(timer);
  }
}
