import { studentRequest } from "./student-schedule";

export interface StudyTaskFields {
  title: string;
  course_id: string | null;
  due_at: string | null;
  estimated_minutes: number;
  remaining_minutes: number;
  difficulty: number;
  priority: number;
  course_weight: number;
  status: "active" | "completed" | "archived";
}
export interface StudyTask extends StudyTaskFields {
  id: string;
  actual_minutes: number;
  revision: number;
  source_item_id: string | null;
  source_revision: number | null;
  source_current_revision: number | null;
  source_current_title: string | null;
  source_current_due_at: string | null;
  source_needs_review: boolean;
  source_reviewable: boolean;
  source_review_notice: string | null;
}
export type StudyTaskSave = StudyTaskFields & {
  reviewed_source_revision?: number;
  confirmed_source?: true;
};
export function studyTaskSourceAcknowledgement(
  task: StudyTask | undefined,
  confirmed: boolean,
): Pick<StudyTaskSave, "reviewed_source_revision" | "confirmed_source"> {
  return confirmed && task?.source_reviewable && task.source_current_revision
    ? {
        reviewed_source_revision: task.source_current_revision,
        confirmed_source: true,
      }
    : {};
}
export interface StudyWindow {
  weekdays: number[];
  start_time: string;
  end_time: string;
}
export interface AlarmPreferences {
  enabled: boolean;
  days: number[];
  smart: boolean;
  prep_minutes: number;
  sound: "gentle" | "chime";
  snooze_minutes: 5 | 10 | 15;
  skip_date: string | null;
}
export const defaultAlarm: AlarmPreferences = {
  enabled: false,
  days: [0, 1, 2, 3, 4, 5, 6],
  smart: false,
  prep_minutes: 30,
  sound: "gentle",
  snooze_minutes: 10,
  skip_date: null,
};
export interface StudyPreferences {
  travel_minutes?: number;
  wake_times?: string[] | null;
  alarm?: AlarmPreferences;

  wake_time: string;
  sleep_time: string;
  focus_windows: StudyWindow[];
  never_schedule: StudyWindow[];
  max_study_minutes: number;
  break_minutes: number;
  revision: number;
  timezone: string;
}
export interface ProposedStudyBlock {
  task_id: string;
  title: string;
  start_at: string;
  end_at: string;
  minutes: number;
  focus_window: boolean;
}
export interface StudyBlock extends ProposedStudyBlock {
  id: string;
  plan_id: string;
  revision: number;
  status:
    | "planned"
    | "done"
    | "took_longer"
    | "skipped"
    | "superseded"
    | "needs_review";
  actual_minutes: number;
  outcome_at: string | null;
}
export interface StudyPlan {
  revision: number;
  blocks: StudyBlock[];
  has_more_history: boolean;
  needs_review: boolean;
  review_notice: string | null;
}
export interface UnfittedStudyWork {
  task_id: string;
  title: string;
  minutes: number;
  due_at: string | null;
  reason:
    | "deadline_passed"
    | "cannot_meet_deadline"
    | "outside_horizon"
    | "insufficient_time";
}
export interface StudyCapacity {
  day: string;
  free_minutes: number;
  buffer_minutes: number;
  planned_minutes: number;
}
export interface StudyPlanResult {
  start_at: string;
  end_at: string;
  blocks: ProposedStudyBlock[];
  unfitted_work: UnfittedStudyWork[];
  capacities: StudyCapacity[];
  warnings: string[];
  planning_only: true;
}
export interface StudyPlanPreview {
  preview_id: string;
  preview_hash: string;
  expires_at: string;
  base_revision: number;
  result: StudyPlanResult;
  requires_acceptance: true;
}
export interface StudyOutcome {
  task: StudyTask;
  block: StudyBlock;
  plan: StudyPlan;
  result: StudyPlanResult;
  undo_request_id: string;
}
export interface StudyAssignmentDraft {
  id: string;
  revision: number;
  course_id: string | null;
  title: string;
  due_at: string | null;
}
export const unfittedStudyReasons: Record<UnfittedStudyWork["reason"], string> =
  {
    deadline_passed: "The deadline has passed",
    cannot_meet_deadline: "Not enough free time before the deadline",
    outside_horizon: "Some work falls beyond this plan",
    insufficient_time: "Not enough free time in this plan",
  };

function invalid(): never {
  throw new Error(
    "The saved study plan could not be checked. Reload it before making a change.",
  );
}
function obj(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 500): string {
  if (typeof value !== "string" || value.length > max) return invalid();
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
function list<T>(value: unknown, parse: (item: unknown) => T, max = 5000): T[] {
  if (!Array.isArray(value) || value.length > max) return invalid();
  return value.map(parse);
}
function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null {
  return value === null ? null : parse(value);
}
function en<T extends string>(value: unknown, allowed: readonly T[]): T {
  if (!allowed.includes(value as T)) return invalid();
  return value as T;
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
function wall(value: unknown): string {
  const v = text(value, 5);
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(v)) return invalid();
  return v;
}
function zone(value: unknown): string {
  const v = text(value, 64);
  try {
    new Intl.DateTimeFormat("en", { timeZone: v }).format();
  } catch {
    return invalid();
  }
  return v;
}
function day(value: unknown): string {
  const v = text(value, 10);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(v) ||
    !Number.isFinite(Date.parse(`${v}T12:00:00Z`)) ||
    new Date(`${v}T12:00:00Z`).toISOString().slice(0, 10) !== v
  )
    return invalid();
  return v;
}
export function readStudyTask(value: unknown): StudyTask {
  const v = obj(value);
  return {
    id: id(v.id),
    title: text(v.title, 200),
    course_id: nullable(v.course_id, id),
    due_at: nullable(v.due_at, instant),
    estimated_minutes: num(v.estimated_minutes, 1, 10080),
    remaining_minutes: num(v.remaining_minutes, 0, 10080),
    actual_minutes: num(v.actual_minutes),
    difficulty: num(v.difficulty, 1, 5),
    priority: num(v.priority, 1, 5),
    course_weight: num(v.course_weight, 1, 10),
    status: en(v.status, ["active", "completed", "archived"]),
    revision: num(v.revision, 1),
    source_item_id: nullable(v.source_item_id, id),
    source_revision: nullable(v.source_revision, (item) => num(item, 1)),
    source_current_revision: nullable(v.source_current_revision, (item) =>
      num(item, 1),
    ),
    source_current_title: nullable(v.source_current_title, (item) =>
      text(item, 200),
    ),
    source_current_due_at: nullable(v.source_current_due_at, instant),
    source_needs_review: bool(v.source_needs_review),
    source_reviewable: bool(v.source_reviewable),
    source_review_notice: nullable(v.source_review_notice, (item) =>
      text(item, 2000),
    ),
  };
}
export function readStudyTasks(value: unknown): StudyTask[] {
  return list(obj(value).tasks, readStudyTask, 200);
}
function readWindow(value: unknown): StudyWindow {
  const v = obj(value);
  const weekdays = list(v.weekdays, (day) => num(day, 0, 6), 7);
  if (!weekdays.length || new Set(weekdays).size !== weekdays.length)
    return invalid();
  return {
    weekdays,
    start_time: wall(v.start_time),
    end_time: wall(v.end_time),
  };
}
function readAlarmPreferences(value: unknown): AlarmPreferences {
  const v = obj(value);
  const snooze = num(v.snooze_minutes, 5, 15);
  if (![5, 10, 15].includes(snooze)) return invalid();
  const days = list(v.days, (item) => num(item, 0, 6), 7);
  if (new Set(days).size !== days.length) return invalid();
  return {
    enabled: bool(v.enabled),
    days,
    smart: bool(v.smart),
    prep_minutes: num(v.prep_minutes, 0, 180),
    sound: en(v.sound, ["gentle", "chime"]),
    snooze_minutes: snooze as 5 | 10 | 15,
    skip_date: nullable(v.skip_date, day),
  };
}
export function readStudyPreferences(value: unknown): StudyPreferences {
  const v = obj(value);
  if (
    v.wake_times != null &&
    (!Array.isArray(v.wake_times) || v.wake_times.length !== 7)
  )
    return invalid();
  return {
    ...(v.travel_minutes !== undefined
      ? { travel_minutes: num(v.travel_minutes, 0, 240) }
      : {}),
    ...(v.wake_times !== undefined
      ? {
          wake_times:
            v.wake_times === null ? null : list(v.wake_times, wall, 7),
        }
      : {}),
    ...(v.alarm !== undefined ? { alarm: readAlarmPreferences(v.alarm) } : {}),
    wake_time: wall(v.wake_time),
    sleep_time: wall(v.sleep_time),
    focus_windows: list(v.focus_windows, readWindow, 14),
    never_schedule: list(v.never_schedule, readWindow, 28),
    max_study_minutes: num(v.max_study_minutes, 15, 90),
    break_minutes: num(v.break_minutes, 5, 60),
    revision: num(v.revision),
    timezone: zone(v.timezone),
  };
}
function readProposedBlock(value: unknown): ProposedStudyBlock {
  const v = obj(value);
  const start = instant(v.start_at);
  const end = instant(v.end_at);
  if (Date.parse(end) <= Date.parse(start)) return invalid();
  return {
    task_id: id(v.task_id),
    title: text(v.title, 200),
    start_at: start,
    end_at: end,
    minutes: num(v.minutes, 1, 90),
    focus_window: bool(v.focus_window),
  };
}
export function readStudyBlock(value: unknown): StudyBlock {
  const v = obj(value);
  return {
    ...readProposedBlock(v),
    id: id(v.id),
    plan_id: id(v.plan_id),
    revision: num(v.revision, 1),
    status: en(v.status, [
      "planned",
      "done",
      "took_longer",
      "skipped",
      "superseded",
      "needs_review",
    ]),
    actual_minutes: num(v.actual_minutes, 0, 1440),
    outcome_at: nullable(v.outcome_at, instant),
  };
}
export function readStudyPlan(value: unknown): StudyPlan {
  const v = obj(value);
  return {
    revision: num(v.revision),
    blocks: list(v.blocks, readStudyBlock),
    has_more_history: bool(v.has_more_history),
    needs_review: bool(v.needs_review),
    review_notice: nullable(v.review_notice, (item) => text(item, 2000)),
  };
}
export function readStudyPlanResult(value: unknown): StudyPlanResult {
  const v = obj(value);
  if (v.planning_only !== true) return invalid();
  return {
    start_at: instant(v.start_at),
    end_at: instant(v.end_at),
    blocks: list(v.blocks, readProposedBlock, 600),
    unfitted_work: list(
      v.unfitted_work,
      (item) => {
        const row = obj(item);
        return {
          task_id: id(row.task_id),
          title: text(row.title, 200),
          minutes: num(row.minutes, 1, 10080),
          due_at: nullable(row.due_at, instant),
          reason: en(row.reason, [
            "deadline_passed",
            "cannot_meet_deadline",
            "outside_horizon",
            "insufficient_time",
          ]),
        };
      },
      200,
    ),
    capacities: list(
      v.capacities,
      (item) => {
        const row = obj(item);
        return {
          day: day(row.day),
          free_minutes: num(row.free_minutes),
          buffer_minutes: num(row.buffer_minutes),
          planned_minutes: num(row.planned_minutes),
        };
      },
      15,
    ),
    warnings: list(v.warnings, (item) => text(item, 2000), 50),
    planning_only: true,
  };
}
export function readStudyPreview(value: unknown): StudyPlanPreview {
  const v = obj(value);
  if (v.requires_acceptance !== true) return invalid();
  const hash = text(v.preview_hash, 64);
  if (!/^[a-f0-9]{64}$/.test(hash)) return invalid();
  return {
    preview_id: id(v.preview_id),
    preview_hash: hash,
    expires_at: instant(v.expires_at),
    base_revision: num(v.base_revision),
    result: readStudyPlanResult(v.result),
    requires_acceptance: true,
  };
}
export function readStudyOutcome(value: unknown): StudyOutcome {
  const v = obj(value);
  return {
    task: readStudyTask(v.task),
    block: readStudyBlock(v.block),
    plan: readStudyPlan(v.plan),
    result: readStudyPlanResult(v.result),
    undo_request_id: id(v.undo_request_id),
  };
}
export interface StudyOutcomePreview extends StudyOutcome {
  preview_hash: string;
  replaced_blocks: StudyBlock[];
}
export function readStudyOutcomePreview(value: unknown): StudyOutcomePreview {
  const v = obj(value);
  const hash = text(v.preview_hash, 64);
  if (!/^[a-f0-9]{64}$/.test(hash)) return invalid();
  return {
    ...readStudyOutcome(value),
    preview_hash: hash,
    replaced_blocks: list(v.replaced_blocks, readStudyBlock, 600),
  };
}
export function studyTaskDraft(value: StudyTaskFields): StudyTaskFields {
  return {
    title: value.title,
    course_id: value.course_id,
    due_at: value.due_at,
    estimated_minutes: value.estimated_minutes,
    remaining_minutes: value.remaining_minutes,
    difficulty: value.difficulty,
    priority: value.priority,
    course_weight: value.course_weight,
    status: value.status,
  };
}
export function studyPreferencesDraft(value: StudyPreferences) {
  return {
    ...(value.travel_minutes !== undefined
      ? { travel_minutes: value.travel_minutes }
      : {}),
    ...(value.wake_times !== undefined ? { wake_times: value.wake_times } : {}),
    ...(value.alarm !== undefined ? { alarm: value.alarm } : {}),
    wake_time: value.wake_time,
    sleep_time: value.sleep_time,
    focus_windows: value.focus_windows.map((row) => ({
      ...row,
      weekdays: [...row.weekdays],
    })),
    never_schedule: value.never_schedule.map((row) => ({
      ...row,
      weekdays: [...row.weekdays],
    })),
    max_study_minutes: value.max_study_minutes,
    break_minutes: value.break_minutes,
    revision: value.revision,
  };
}
export function studyMinutes(value: number): string {
  const hours = Math.floor(value / 60),
    minutes = value % 60;
  return hours && minutes
    ? `${hours}h ${minutes}m`
    : hours
      ? `${hours}h`
      : `${minutes}m`;
}
export function studyRequest<T>(
  path: string,
  parse: (value: unknown) => T,
  method = "GET",
  body?: unknown,
): Promise<T> {
  return studentRequest(`/planning${path}`, parse, method, body);
}

/** Convert an instant to an editable wall clock in the saved zone, never the device zone. */
export function studyLocalDateTime(value: string, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(value));
  const part = (type: string) =>
    parts.find((row) => row.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}
/** A clock change can yield zero or two instants. The student must resolve that explicitly. */
export function studyDeadlineChoices(
  local: string,
  timezone: string,
): string[] {
  if (!/^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d$/.test(local))
    throw new Error("Choose a valid deadline date and time.");
  const nominal = Date.parse(`${local}:00Z`);
  if (
    !Number.isFinite(nominal) ||
    new Date(nominal).toISOString().slice(0, 16) !== local
  )
    throw new Error("Choose a valid deadline date and time.");
  new Intl.DateTimeFormat("en", { timeZone: timezone }).format();
  const offsets = new Set<number>();
  for (const hours of [-36, -12, 0, 12, 36]) {
    const sample = nominal + hours * 3600_000;
    const formatted = studyLocalDateTime(
      new Date(sample).toISOString(),
      timezone,
    );
    offsets.add(
      Date.parse(`${formatted}:00Z`) - Math.floor(sample / 60000) * 60000,
    );
  }
  return [...offsets]
    .map((offset) => new Date(nominal - offset).toISOString())
    .filter((value) => studyLocalDateTime(value, timezone) === local)
    .sort();
}

export type AlarmOccurrence = {
  day: string;
  enabled: boolean;
  scheduled_at: string | null;
  reason: string;
  first_class_at: string | null;
  first_class_title: string | null;
};
export function readAlarmView(value: unknown): {
  timezone: string;
  alarm_occurrences: AlarmOccurrence[];
  native_ringing_available: false;
} {
  const v = obj(value);
  if (v.native_ringing_available !== false) return invalid();
  return {
    timezone: zone(v.timezone),
    native_ringing_available: false,
    alarm_occurrences: list(
      v.alarm_occurrences,
      (item) => {
        const row = obj(item);
        return {
          day: day(row.day),
          enabled: bool(row.enabled),
          scheduled_at: nullable(row.scheduled_at, instant),
          reason: text(row.reason, 500),
          first_class_at: nullable(row.first_class_at, instant),
          first_class_title: nullable(row.first_class_title, (item) =>
            text(item, 240),
          ),
        };
      },
      14,
    ),
  };
}
export function readRoutinePreview(value: unknown) {
  const v = obj(value),
    hash = text(v.preview_hash, 64);
  if (!/^[a-f0-9]{64}$/.test(hash)) return invalid();
  return {
    ...readAlarmView(value),
    preview_hash: hash,
    warnings: list(v.warnings, (item) => text(item, 2000), 50),
    replaced_blocks: list(v.replaced_blocks, readStudyBlock, 600),
    previous_alarm_occurrences: readAlarmView({
      ...v,
      alarm_occurrences: v.previous_alarm_occurrences,
    }).alarm_occurrences,
  };
}
export type RoutinePreview = ReturnType<typeof readRoutinePreview>;

export async function routineRequest<T>(
  accountKey: string,
  path: string,
  parse: (value: unknown) => T,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const base =
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
  const response = await fetch(`${base}/student/planning${path}`, {
    method,
    credentials: "include",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-NavoX-Account": accountKey,
    },
    signal: AbortSignal.timeout(60000),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const value: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      value && typeof value === "object" && "detail" in value
        ? value.detail
        : null;
    throw new Error(
      response.status < 500 &&
        typeof detail === "string" &&
        detail.length <= 400
        ? detail
        : response.status === 409
          ? "Your schedule or account changed. Review a fresh preview."
          : "Your schedule could not be checked. Please try again.",
    );
  }
  return parse(value);
}

export function readStudyMove(value: unknown) {
  const v = obj(value),
    hash = text(v.preview_hash, 64);
  if (
    !/^[a-f0-9]{64}$/.test(hash) ||
    v.other_blocks_change !== false ||
    v.alarms_change !== false ||
    v.calendar_events_change !== false
  )
    return invalid();
  const before = readStudyBlock(v.before),
    after = readStudyBlock(v.after);
  if (
    before.id !== after.id ||
    before.task_id !== after.task_id ||
    before.minutes !== after.minutes
  )
    return invalid();
  return {
    preview_hash: hash,
    before,
    after,
    warnings: list(v.warnings, (item) => text(item, 2000), 50),
  };
}
export type StudyMove = ReturnType<typeof readStudyMove>;

export function readStudyOpenings(value: unknown) {
  const v = obj(value);
  return {
    timezone: zone(v.timezone),
    checked_at: instant(v.checked_at),
    expires_at: instant(v.expires_at),
    calendar_checked: bool(v.calendar_checked),
    routine_confirmed: bool(v.routine_confirmed),
    warnings: list(v.warnings, (item) => text(item, 2000), 50),
    openings: list(
      v.openings,
      (item) => {
        const r = obj(item),
          start = instant(r.start_at),
          end = instant(r.end_at);
        if (Date.parse(end) <= Date.parse(start)) return invalid();
        return {
          start_at: start,
          end_at: end,
          minutes: num(r.minutes, 15, 1440),
        };
      },
      200,
    ),
  };
}
export type StudyOpenings = ReturnType<typeof readStudyOpenings>;
