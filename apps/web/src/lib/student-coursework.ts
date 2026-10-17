import { readStudyTask, type StudyTask } from "./student-planning";
import {
  readStudentCourse,
  type StudentCourse,
  StudentRequestError,
  studentRequest,
} from "./student-schedule";

export type CourseMaterialKind = "note" | "material" | "syllabus";
export interface CourseQuote {
  material_id: string;
  revision: number;
  page: number;
  quote: string;
}
export interface CourseStaff {
  name: string;
  role: string;
  email: string | null;
  office_hours: string | null;
  source: CourseQuote | null;
}
export interface CourseGradeWeight {
  name: string;
  weight_percent: string;
  source: CourseQuote | null;
}
export interface CourseOverview {
  course: StudentCourse;
  archived: boolean;
  revision: number;
  staff: CourseStaff[];
  grade_breakdown: CourseGradeWeight[];
  ai_policy: CourseQuote | null;
  grade_weight_total: string;
  policy_status: "unknown" | "quoted_policy";
  default_help_mode: "tutor_outline_review";
  ai_permission_granted: false;
  notice: string;
}
export interface CourseMaterial {
  id: string;
  title: string;
  kind: CourseMaterialKind;
  revision: number;
  media_type: string;
  byte_size: number;
  text_size: number;
  page_count: number;
  created_at: string;
  updated_at: string;
}
export interface CourseMaterialDetail extends CourseMaterial {
  pages: string[];
}
export interface CourseReceipt {
  request_id: string;
  entity_id: string;
  revision: number;
  reused: boolean;
  available: boolean;
}
export interface CourseSearchResult {
  material_id: string;
  revision: number;
  title: string;
  page: number;
  snippet: string;
}
export interface CourseSearch {
  course_id: string;
  results: CourseSearchResult[];
  limit_reached: boolean;
}
export interface CourseAssignment {
  id: string;
  revision: number;
  title: string;
  kind: "assignment" | "event" | "exam";
  due_at: string | null;
  cancelled: boolean;
  conflicted: boolean;
  source: "confirmed_calendar_feed";
}
export interface CourseAssignments {
  tasks: StudyTask[];
  feed_items: CourseAssignment[];
  next_offset: number | null;
}
export interface CourseEmailLink {
  id: string;
  source_id: string;
  source_hash: string;
  revision: number;
  source_type: "communication.message";
  source_open_url: string | null;
  title: string | null;
  sender_label: string | null;
  display_label?: string | null;
}
export interface CourseEmailCandidate {
  source_id: string;
  source_hash: string;
  title: string | null;
  sender_label: string | null;
  display_label?: string | null;
}
export interface CourseEmailCandidates {
  candidates: CourseEmailCandidate[];
  limit_reached: boolean;
  notice: string;
}
export interface CourseEmails {
  links: CourseEmailLink[];
  unavailable_count: number;
}
function invalid(): never {
  throw new Error(
    "This course’s saved details could not be checked. Reload before making a change.",
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
function list<T>(
  value: unknown,
  parse: (value: unknown) => T,
  max = 1000,
): T[] {
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
function kind(value: unknown): CourseMaterialKind {
  if (value !== "note" && value !== "material" && value !== "syllabus")
    return invalid();
  return value;
}
function percent(value: unknown): string {
  const v = text(value, 12);
  if (!/^\d{1,3}(?:\.\d{1,2})?$/.test(v) || Number(v) > 100) return invalid();
  return v;
}
export function readCourseQuote(value: unknown): CourseQuote {
  const v = obj(value);
  return {
    material_id: id(v.material_id),
    revision: num(v.revision, 1),
    page: num(v.page, 1, 100),
    quote: text(v.quote, 1000),
  };
}
export function readCourseOverview(value: unknown): CourseOverview {
  const v = obj(value);
  if (
    !["unknown", "quoted_policy"].includes(v.policy_status as string) ||
    v.default_help_mode !== "tutor_outline_review" ||
    v.ai_permission_granted !== false
  )
    return invalid();
  return {
    course: readStudentCourse(v.course),
    archived: bool(v.archived),
    revision: num(v.revision),
    staff: list(
      v.staff,
      (item) => {
        const row = obj(item);
        return {
          name: text(row.name, 120),
          role: text(row.role, 80),
          email: nullable(row.email, (item) => text(item, 320)),
          office_hours: nullable(row.office_hours, (item) => text(item, 500)),
          source: nullable(row.source, readCourseQuote),
        };
      },
      20,
    ),
    grade_breakdown: list(
      v.grade_breakdown,
      (item) => {
        const row = obj(item);
        return {
          name: text(row.name, 120),
          weight_percent: percent(row.weight_percent),
          source: nullable(row.source, readCourseQuote),
        };
      },
      20,
    ),
    ai_policy: nullable(v.ai_policy, readCourseQuote),
    grade_weight_total: percent(v.grade_weight_total),
    policy_status: v.policy_status as CourseOverview["policy_status"],
    default_help_mode: "tutor_outline_review",
    ai_permission_granted: false,
    notice: text(v.notice),
  };
}
export function readCourseMaterial(value: unknown): CourseMaterial {
  const v = obj(value);
  return {
    id: id(v.id),
    title: text(v.title, 160),
    kind: kind(v.kind),
    revision: num(v.revision, 1),
    media_type: text(v.media_type, 100),
    byte_size: num(v.byte_size, 0, 5 * 1024 * 1024),
    text_size: num(v.text_size, 0, 200000),
    page_count: num(v.page_count, 1, 100),
    created_at: instant(v.created_at),
    updated_at: instant(v.updated_at),
  };
}
export function readCourseMaterials(value: unknown): CourseMaterial[] {
  return list(obj(value).materials, readCourseMaterial, 40);
}
export function readCourseMaterialDetail(value: unknown): CourseMaterialDetail {
  const v = obj(value);
  const material = readCourseMaterial(v);
  const pages = list(v.pages, (item) => text(item, 200000), 100);
  if (
    pages.length !== material.page_count ||
    pages.reduce((sum, page) => sum + page.length, 0) > 200000
  )
    return invalid();
  return { ...material, pages };
}
export function readCourseReceipt(value: unknown): CourseReceipt {
  const v = obj(value);
  return {
    request_id: id(v.request_id),
    entity_id: id(v.entity_id),
    revision: num(v.revision),
    reused: bool(v.reused),
    available: bool(v.available),
  };
}
export function readCourseSaveReceipt(value: unknown): CourseReceipt {
  const receipt = readCourseReceipt(value);
  if (!receipt.available)
    throw new Error(
      "This earlier change included data that was deleted. Reload the course before making another change.",
    );
  return receipt;
}
export function coursePolicyQuote(
  material: CourseMaterialDetail,
  page: number,
  quote: string,
): CourseQuote {
  if (
    material.kind !== "syllabus" ||
    !Number.isInteger(page) ||
    page < 1 ||
    page > material.page_count ||
    !quote.trim() ||
    quote.length > 1000 ||
    !material.pages[page - 1]?.includes(quote)
  )
    throw new Error(
      "Copy an exact passage from the selected syllabus page, including its spacing and line breaks.",
    );
  return { material_id: material.id, revision: material.revision, page, quote };
}
export function readCourseSearch(value: unknown): CourseSearch {
  const v = obj(value);
  return {
    course_id: id(v.course_id),
    limit_reached: bool(v.limit_reached),
    results: list(
      v.results,
      (item) => {
        const row = obj(item);
        return {
          material_id: id(row.material_id),
          revision: num(row.revision, 1),
          title: text(row.title, 160),
          page: num(row.page, 1, 100),
          snippet: text(row.snippet, 2000),
        };
      },
      8,
    ),
  };
}
export function readCourseAssignments(value: unknown): CourseAssignments {
  const v = obj(value);
  return {
    tasks: list(v.tasks, readStudyTask, 200),
    feed_items: list(
      v.feed_items,
      (item) => {
        const row = obj(item);
        if (
          !["assignment", "event", "exam"].includes(row.kind as string) ||
          row.source !== "confirmed_calendar_feed"
        )
          return invalid();
        return {
          id: id(row.id),
          revision: num(row.revision, 1),
          title: text(row.title, 300),
          kind: row.kind as CourseAssignment["kind"],
          due_at: nullable(row.due_at, instant),
          cancelled: bool(row.cancelled),
          conflicted: bool(row.conflicted),
          source: "confirmed_calendar_feed",
        };
      },
      50,
    ),
    next_offset: nullable(v.next_offset, (item) => num(item, 0, 5000)),
  };
}
export function readCourseEmails(value: unknown): CourseEmails {
  const v = obj(value);
  return {
    unavailable_count: num(v.unavailable_count),
    links: list(
      v.links,
      (item) => {
        const row = obj(item);
        if (
          row.source_type !== "communication.message" ||
          !/^[a-f0-9]{64}$/.test(text(row.source_hash, 64))
        )
          return invalid();
        return {
          id: id(row.id),
          source_id: id(row.source_id),
          source_hash: text(row.source_hash, 64),
          revision: num(row.revision, 1),
          source_type: "communication.message",
          source_open_url: nullable(row.source_open_url, (item) =>
            text(item, 2048),
          ),
          title: nullable(row.title, (item) => text(item, 160)),
          sender_label: nullable(row.sender_label, (item) => text(item, 120)),
          display_label:
            row.display_label === undefined
              ? null
              : nullable(row.display_label, (item) => text(item, 240)),
        };
      },
      100,
    ),
  };
}
export function readCourseEmailCandidates(
  value: unknown,
): CourseEmailCandidates {
  const v = obj(value);
  return {
    limit_reached: bool(v.limit_reached),
    notice: text(v.notice, 2000),
    candidates: list(
      v.candidates,
      (item) => {
        const row = obj(item);
        const hash = text(row.source_hash, 64);
        if (!/^[a-f0-9]{64}$/.test(hash)) return invalid();
        return {
          source_id: id(row.source_id),
          source_hash: hash,
          title: nullable(row.title, (item) => text(item, 160)),
          sender_label: nullable(row.sender_label, (item) => text(item, 120)),
          display_label:
            row.display_label === undefined
              ? null
              : nullable(row.display_label, (item) => text(item, 240)),
        };
      },
      20,
    ),
  };
}
export function courseOverviewDraft(value: CourseOverview) {
  return {
    revision: value.revision,
    staff: value.staff.map((row) => ({
      ...row,
      source: row.source ? { ...row.source } : null,
    })),
    grade_breakdown: value.grade_breakdown.map((row) => ({
      ...row,
      source: row.source ? { ...row.source } : null,
    })),
    ai_policy: value.ai_policy ? { ...value.ai_policy } : null,
  };
}
export function courseRequest<T>(
  courseId: string,
  path: string,
  parse: (value: unknown) => T,
  method = "GET",
  body?: unknown,
): Promise<T> {
  id(courseId);
  return studentRequest(
    `/courses/${courseId}/workspace${path}`,
    parse,
    method,
    body,
  );
}
const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export function courseMaterialUrl(
  courseId: string,
  materialId: string,
  page?: number,
): string {
  id(courseId);
  id(materialId);
  return `${apiBaseUrl}/student/courses/${courseId}/workspace/materials/${materialId}/file${page ? `#page=${page}` : ""}`;
}
export function courseEmailUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" &&
      url.hostname === "mail.google.com" &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export async function uploadCourseMaterial(
  courseId: string,
  input: {
    title: string;
    kind: CourseMaterialKind;
    revision: number;
    request_id: string;
    content: Blob | Uint8Array<ArrayBuffer>;
    media_type: string;
    material_id?: string;
  },
): Promise<CourseReceipt> {
  id(courseId);
  if (input.material_id) id(input.material_id);
  id(input.request_id);
  if (!input.title.trim() || input.title.length > 160)
    throw new Error("Give this material a title of up to 160 characters.");
  if (
    !["application/pdf", "text/plain", "text/markdown"].includes(
      input.media_type,
    )
  )
    throw new Error("Choose PDF, UTF-8 text, or Markdown.");
  const size =
    input.content instanceof Blob
      ? input.content.size
      : input.content.byteLength;
  if (size === 0 || size > 5 * 1024 * 1024)
    throw new Error("Choose a nonempty file no larger than 5 MiB.");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 40_000);
  try {
    const response = await fetch(
      `${apiBaseUrl}/student/courses/${courseId}/workspace/materials${input.material_id ? `/${input.material_id}` : ""}?kind=${input.kind}&revision=${input.revision}`,
      {
        method: input.material_id ? "PUT" : "POST",
        credentials: "include",
        cache: "no-store",
        headers: {
          "Content-Type": input.media_type,
          "X-Material-Title": encodeURIComponent(input.title.trim()),
          "X-Request-ID": input.request_id,
        },
        body: input.content,
        signal: controller.signal,
      },
    );
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const detail =
        payload &&
        typeof payload === "object" &&
        "detail" in payload &&
        typeof payload.detail === "string"
          ? payload.detail
          : null;
      throw new StudentRequestError(
        detail ??
          "This material could not be saved. Try a smaller PDF or UTF-8 text.",
        response.status >= 500,
      );
    }
    try {
      return readCourseSaveReceipt(payload);
    } catch {
      throw new StudentRequestError(
        "The saved material could not be checked. Reload the course before trying again.",
        true,
      );
    }
  } catch (cause) {
    if (cause instanceof StudentRequestError) throw cause;
    throw new StudentRequestError(
      "The upload result could not be checked. Reload saved course materials before trying again.",
      true,
    );
  } finally {
    clearTimeout(timer);
  }
}
export function coursePercentHundredths(value: string): number | null {
  if (!/^\d{1,3}(?:\.\d{1,2})?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  const amount = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  return amount <= 10000 ? amount : null;
}
