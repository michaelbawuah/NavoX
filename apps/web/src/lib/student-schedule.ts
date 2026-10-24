import { studentLocalTime } from "./student-time";
export const studentLmsOptions = [
  ["none", "Not sure / add later"],
  ["canvas", "Canvas"],
  ["blackboard", "Blackboard"],
  ["moodle", "Moodle"],
  ["brightspace", "Brightspace"],
  ["google_classroom", "Google Classroom"],
  ["other", "Another system"],
] as const;
export const studentMeetingKinds = [
  "lecture",
  "lab",
  "discussion",
  "recitation",
  "seminar",
  "other",
] as const;
export type StudentMeetingKind = (typeof studentMeetingKinds)[number];
export const studentWeekdays = [
  "Mon",
  "Tue",
  "Wed",
  "Thu",
  "Fri",
  "Sat",
  "Sun",
] as const;
export interface StudentProfile {
  school_name: string | null;
  lms: (typeof studentLmsOptions)[number][0];
  display_name: string | null;
  timezone: string;
  term_start: string | null;
  term_end: string | null;
  revision: number;
}
export interface StudentMeetingInput {
  kind: StudentMeetingKind;
  weekdays: number[];
  start_time: string;
  end_time: string;
  timezone: string;
  building: string | null;
  room: string | null;
  instructor: string | null;
  walk_minutes: number | null;
}
export interface StudentMeeting extends StudentMeetingInput {
  id: string;
  course_id: string;
  dates: string[] | null;
  revision: number;
}
export interface StudentCourseInput {
  office_hours?: string | null;
  code: string;
  name: string;
  term_start: string;
  term_end: string;
  professor: string | null;
}
export interface StudentCourse extends StudentCourseInput {
  id: string;
  archived: boolean;
  revision: number;
  meetings: StudentMeeting[];
}
export interface StudentFixedBlockInput {
  title: string;
  weekdays: number[];
  start_time: string;
  end_time: string;
  timezone: string;
  term_start: string;
  term_end: string;
}
export interface StudentFixedBlock extends StudentFixedBlockInput {
  id: string;
  revision: number;
}
export interface StudentBreak {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  revision: number;
}
export interface StudentException {
  id: string;
  meeting_id: string;
  date: string;
  cancelled: boolean;
  start_time: string | null;
  end_time: string | null;
  building: string | null;
  room: string | null;
}
export interface StudentOccurrence {
  id: string;
  meeting_id: string;
  course_id: string;
  course_code: string;
  course_name: string;
  kind: StudentMeetingKind;
  start_at: string;
  end_at: string;
  timezone: string;
  building: string | null;
  room: string | null;
  instructor: string | null;
  walk_minutes: number | null;
  cancelled: boolean;
  exception: boolean;
}
export interface StudentFixedOccurrence {
  id: string;
  fixed_block_id: string;
  title: string;
  start_at: string;
  end_at: string;
}
export interface StudentConflict {
  left_id: string;
  right_id: string;
  kind: "class_class" | "class_fixed";
  start_at: string;
  end_at: string;
}
export interface StudentTimetable {
  server_now: string;
  timezone: string;
  date: string;
  days: number;
  courses: StudentCourse[];
  fixed_blocks: StudentFixedBlock[];
  breaks: StudentBreak[];
  exceptions: StudentException[];
  occurrences: StudentOccurrence[];
  fixed_occurrences: StudentFixedOccurrence[];
  conflicts: StudentConflict[];
  warnings: string[];
}
export interface StudentLookup {
  server_now: string;
  timezone: string;
  mode: "now" | "next" | "at" | "day";
  date: string;
  at: string;
  occurrences: StudentOccurrence[];
  next_upcoming: StudentOccurrence | null;
  reason: "matches" | "no_classes" | "after_last_class" | "break";
  conflicts: StudentConflict[];
}
export interface StudentImportCandidate {
  id: string;
  course_code: string;
  course_name: string;
  kind: StudentMeetingKind;
  dates: string[];
  start_time: string;
  end_time: string;
  timezone: string;
  building: string | null;
  room: string | null;
  instructor: string | null;
}
export interface StudentImportPreview {
  preview_id: string;
  preview_hash: string;
  expires_at: string;
  timezone: string;
  candidates: StudentImportCandidate[];
  warnings: string[];
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(
      "The saved timetable could not be read. Reload to check it.",
    );
  return value as Record<string, unknown>;
}
function string(value: unknown): string {
  if (typeof value !== "string")
    throw new Error(
      "The saved timetable could not be read. Reload to check it.",
    );
  return value;
}
function nullable(value: unknown): string | null {
  return value === null ? null : string(value);
}
function number(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0)
    throw new Error(
      "The saved timetable could not be read. Reload to check it.",
    );
  return value;
}
function boolean(value: unknown): boolean {
  if (typeof value !== "boolean")
    throw new Error(
      "The saved timetable could not be read. Reload to check it.",
    );
  return value;
}
function array<T>(value: unknown, parse: (item: unknown) => T): T[] {
  if (!Array.isArray(value))
    throw new Error(
      "The saved timetable could not be read. Reload to check it.",
    );
  return value.map(parse);
}
function enumeration<T extends string>(
  value: unknown,
  options: readonly T[],
): T {
  const result = string(value);
  if (!options.includes(result as T))
    throw new Error(
      "The saved timetable uses an unsupported value. Reload to check it.",
    );
  return result as T;
}
function instant(value: unknown): string {
  const result = string(value);
  if (
    !/^\d{4}-\d{2}-\d{2}T/.test(result) ||
    !/(Z|[+-]\d{2}:\d{2})$/.test(result) ||
    !Number.isFinite(Date.parse(result))
  )
    throw new Error("The schedule time could not be verified.");
  return result;
}
function timezone(value: unknown): string {
  const result = string(value);
  try {
    new Intl.DateTimeFormat("en", { timeZone: result }).format();
  } catch {
    throw new Error("The schedule time zone could not be verified.");
  }
  return result;
}
function date(value: unknown): string {
  const result = string(value);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(result) ||
    !Number.isFinite(Date.parse(`${result}T12:00:00Z`)) ||
    new Date(`${result}T12:00:00Z`).toISOString().slice(0, 10) !== result
  )
    throw new Error("The schedule date could not be verified.");
  return result;
}
function time(value: unknown): string {
  const result = string(value);
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(result))
    throw new Error("The schedule time could not be verified.");
  return result;
}
function weekdays(value: unknown): number[] {
  return array(value, (item) => {
    const day = number(item);
    if (day > 6) throw new Error("The schedule days could not be verified.");
    return day;
  });
}
export function readStudentProfile(value: unknown): StudentProfile {
  const v = record(value);
  return {
    school_name: nullable(v.school_name),
    lms: enumeration(
      v.lms,
      studentLmsOptions.map(([key]) => key),
    ),
    display_name: nullable(v.display_name),
    timezone: timezone(v.timezone),
    term_start: v.term_start === null ? null : date(v.term_start),
    term_end: v.term_end === null ? null : date(v.term_end),
    revision: number(v.revision),
  };
}
export function readStudentMeeting(value: unknown): StudentMeeting {
  const v = record(value);
  return {
    id: string(v.id),
    course_id: string(v.course_id),
    kind: enumeration(v.kind, studentMeetingKinds),
    weekdays: weekdays(v.weekdays),
    start_time: time(v.start_time),
    end_time: time(v.end_time),
    timezone: timezone(v.timezone),
    building: nullable(v.building),
    room: nullable(v.room),
    instructor: nullable(v.instructor),
    walk_minutes: v.walk_minutes === null ? null : number(v.walk_minutes),
    dates: v.dates === null ? null : array(v.dates, date),
    revision: number(v.revision),
  };
}
export function readStudentCourse(value: unknown): StudentCourse {
  const v = record(value);
  return {
    id: string(v.id),
    code: string(v.code),
    name: string(v.name),
    term_start: date(v.term_start),
    term_end: date(v.term_end),
    professor: nullable(v.professor),
    ...(v.office_hours !== undefined
      ? { office_hours: nullable(v.office_hours) }
      : {}),
    archived: boolean(v.archived),
    revision: number(v.revision),
    meetings: array(v.meetings, readStudentMeeting),
  };
}
export function readStudentFixedBlock(value: unknown): StudentFixedBlock {
  const v = record(value);
  return {
    id: string(v.id),
    title: string(v.title),
    weekdays: weekdays(v.weekdays),
    start_time: time(v.start_time),
    end_time: time(v.end_time),
    timezone: timezone(v.timezone),
    term_start: date(v.term_start),
    term_end: date(v.term_end),
    revision: number(v.revision),
  };
}
export function readStudentBreak(value: unknown): StudentBreak {
  const v = record(value);
  return {
    id: string(v.id),
    name: string(v.name),
    start_date: date(v.start_date),
    end_date: date(v.end_date),
    revision: number(v.revision),
  };
}
export function readStudentOccurrence(value: unknown): StudentOccurrence {
  const v = record(value);
  return {
    id: string(v.id),
    meeting_id: string(v.meeting_id),
    course_id: string(v.course_id),
    course_code: string(v.course_code),
    course_name: string(v.course_name),
    kind: enumeration(v.kind, studentMeetingKinds),
    start_at: instant(v.start_at),
    end_at: instant(v.end_at),
    timezone: timezone(v.timezone),
    building: nullable(v.building),
    room: nullable(v.room),
    instructor: nullable(v.instructor),
    walk_minutes: v.walk_minutes === null ? null : number(v.walk_minutes),
    cancelled: boolean(v.cancelled),
    exception: boolean(v.exception),
  };
}
function readConflict(value: unknown): StudentConflict {
  const v = record(value);
  return {
    left_id: string(v.left_id),
    right_id: string(v.right_id),
    kind: enumeration(v.kind, ["class_class", "class_fixed"]),
    start_at: instant(v.start_at),
    end_at: instant(v.end_at),
  };
}
export function readStudentTimetable(value: unknown): StudentTimetable {
  const v = record(value);
  return {
    server_now: instant(v.server_now),
    timezone: timezone(v.timezone),
    date: date(v.date),
    days: number(v.days),
    courses: array(v.courses, readStudentCourse),
    fixed_blocks: array(v.fixed_blocks, readStudentFixedBlock),
    breaks: array(v.breaks, readStudentBreak),
    exceptions: array(v.exceptions, (item) => {
      const e = record(item);
      return {
        id: string(e.id),
        meeting_id: string(e.meeting_id),
        date: date(e.date),
        cancelled: boolean(e.cancelled),
        start_time: e.start_time === null ? null : time(e.start_time),
        end_time: e.end_time === null ? null : time(e.end_time),
        building: nullable(e.building),
        room: nullable(e.room),
      };
    }),
    occurrences: array(v.occurrences, readStudentOccurrence),
    fixed_occurrences: array(v.fixed_occurrences, (item) => {
      const e = record(item);
      return {
        id: string(e.id),
        fixed_block_id: string(e.fixed_block_id),
        title: string(e.title),
        start_at: instant(e.start_at),
        end_at: instant(e.end_at),
      };
    }),
    conflicts: array(v.conflicts, readConflict),
    warnings: array(v.warnings, string),
  };
}
export function readStudentLookup(value: unknown): StudentLookup {
  const v = record(value);
  return {
    server_now: instant(v.server_now),
    timezone: timezone(v.timezone),
    mode: enumeration(v.mode, ["now", "next", "at", "day"]),
    date: date(v.date),
    at: instant(v.at),
    occurrences: array(v.occurrences, readStudentOccurrence),
    next_upcoming:
      v.next_upcoming === null ? null : readStudentOccurrence(v.next_upcoming),
    reason: enumeration(v.reason, [
      "matches",
      "no_classes",
      "after_last_class",
      "break",
    ]),
    conflicts: array(v.conflicts, readConflict),
  };
}
export function readStudentImportPreview(value: unknown): StudentImportPreview {
  const v = record(value);
  return {
    preview_id: string(v.preview_id),
    preview_hash: string(v.preview_hash),
    expires_at: instant(v.expires_at),
    timezone: timezone(v.timezone),
    candidates: array(v.candidates, (item) => {
      const c = record(item);
      return {
        id: string(c.id),
        course_code: string(c.course_code),
        course_name: string(c.course_name),
        kind: enumeration(c.kind, studentMeetingKinds),
        dates: array(c.dates, date),
        start_time: time(c.start_time),
        end_time: time(c.end_time),
        timezone: timezone(c.timezone),
        building: nullable(c.building),
        room: nullable(c.room),
        instructor: nullable(c.instructor),
      };
    }),
    warnings: array(v.warnings, string),
  };
}
export function readStudentList<T>(
  value: unknown,
  key: string,
  parse: (item: unknown) => T,
): T[] {
  return array(record(value)[key], parse);
}
export class StudentRequestError extends Error {
  constructor(
    message: string,
    readonly uncertain = false,
  ) {
    super(message);
  }
}
const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export async function studentRequest<T>(
  path: string,
  parse: (value: unknown) => T,
  method = "GET",
  body?: unknown,
  accountKey?: string,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(),
    path.startsWith("/planning") ? 60_000 : 12_000,
  );
  try {
    const response = await fetch(`${apiBaseUrl}/student${path}`, {
      method,
      credentials: "include",
      cache: "no-store",
      signal: controller.signal,
      headers: {
        ...(accountKey ? { "X-NavoX-Account": accountKey } : {}),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined
        ? {}
        : {
            body: JSON.stringify(body),
          }),
    });
    const payload: unknown =
      response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) {
      const detail = studentResponseError(payload);
      throw new StudentRequestError(
        detail
          ? detail
          : response.status === 409
            ? "Your timetable changed elsewhere. Reload the saved version before editing."
            : "Your timetable could not be updated. Please try again.",
        method !== "GET" && response.status >= 500,
      );
    }
    try {
      return parse(payload);
    } catch (cause) {
      throw new StudentRequestError(
        cause instanceof Error
          ? cause.message
          : "The saved timetable could not be read.",
        method !== "GET",
      );
    }
  } catch (cause) {
    if (cause instanceof StudentRequestError) throw cause;
    throw new StudentRequestError(
      cause instanceof Error && cause.name === "AbortError"
        ? "The timetable took too long to respond."
        : "The timetable could not be reached.",
      method !== "GET",
    );
  } finally {
    clearTimeout(timer);
  }
}
export function studentDateInZone(instantValue: string, zone: string): string {
  return studentLocalTime(instantValue, zone).date;
}
export function addStudentDays(value: string, amount: number): string {
  const day = new Date(`${value}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + amount);
  return day.toISOString().slice(0, 10);
}
export function studentClock(value: string, zone: string): string {
  return studentLocalTime(value, zone).clock;
}
export function studentDayLabel(value: string): string {
  return new Intl.DateTimeFormat("en", {
    timeZone: "UTC",
    weekday: "long",
    month: "short",
    day: "numeric",
  }).format(new Date(`${value}T12:00:00Z`));
}
export function studentLocation(value: {
  building: string | null;
  room: string | null;
}): string {
  return (
    [value.building, value.room].filter(Boolean).join(" · ") ||
    "Location not added"
  );
}
export function studentDayNames(days: number[]): string {
  return [...days]
    .sort((a, b) => a - b)
    .map((day) => studentWeekdays[day])
    .join(" · ");
}
export function studentError(cause: unknown): string {
  return cause instanceof Error
    ? cause.message
    : "The timetable could not be loaded.";
}
export function studentWriteUncertain(cause: unknown): boolean {
  return cause instanceof StudentRequestError && cause.uncertain;
}

/** Form drafts contain only editable API fields, never response identifiers or nested rows. */
export function studentCourseDraft(
  value: StudentCourseInput,
): StudentCourseInput {
  return {
    code: value.code,
    name: value.name,
    term_start: value.term_start,
    term_end: value.term_end,
    professor: value.professor,
    ...(value.office_hours !== undefined
      ? { office_hours: value.office_hours }
      : {}),
  };
}
export function studentMeetingDraft(
  value: StudentMeetingInput,
): StudentMeetingInput {
  return {
    kind: value.kind,
    weekdays: [...value.weekdays],
    start_time: value.start_time,
    end_time: value.end_time,
    timezone: value.timezone,
    building: value.building,
    room: value.room,
    instructor: value.instructor,
    walk_minutes: value.walk_minutes,
  };
}
export function studentFixedDraft(
  value: StudentFixedBlockInput,
): StudentFixedBlockInput {
  return {
    title: value.title,
    weekdays: [...value.weekdays],
    start_time: value.start_time,
    end_time: value.end_time,
    timezone: value.timezone,
    term_start: value.term_start,
    term_end: value.term_end,
  };
}

export function studentResponseError(payload: unknown): string | null {
  if (!payload || typeof payload !== "object" || !("detail" in payload))
    return null;
  const detail = payload.detail;
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return null;
  const messages = detail.slice(0, 3).flatMap((item) => {
    if (!item || typeof item !== "object" || typeof item.msg !== "string")
      return [];
    const field = Array.isArray(item.loc)
      ? item.loc
          .filter(
            (part: unknown) => typeof part === "string" && part !== "body",
          )
          .join(" ")
          .replaceAll("_", " ")
      : "";
    return [`${field ? `${field}: ` : ""}${item.msg}`.slice(0, 300)];
  });
  return messages.length ? messages.join(" ") : null;
}
