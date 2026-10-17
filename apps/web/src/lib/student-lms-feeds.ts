export const lmsFeedPlatforms = [
  "canvas",
  "blackboard",
  "moodle",
  "brightspace",
  "google_classroom",
  "other",
] as const;
export type LmsFeedPlatform = (typeof lmsFeedPlatforms)[number];
export const lmsPlatformLabels: Record<LmsFeedPlatform, string> = {
  canvas: "Canvas",
  blackboard: "Blackboard",
  moodle: "Moodle",
  brightspace: "Brightspace",
  google_classroom: "Google Classroom",
  other: "Another system / not sure",
};
export const lmsFeedGuides: Record<
  LmsFeedPlatform,
  { steps: string[]; note: string }
> = {
  canvas: {
    steps: [
      "Open Calendar in Canvas.",
      "Choose Calendar Feed, usually near the bottom right.",
      "Copy your personal calendar link and paste it below.",
    ],
    note: "Canvas To Do items aren’t included. If a newly enrolled course is missing, copy a fresh feed link.",
  },
  blackboard: {
    steps: [
      "Open My Blackboard, then Calendar.",
      "Choose Get External Calendar link.",
      "Copy the calendar link ending in .ics.",
    ],
    note: "Blackboard changes can take a few hours to appear in the feed.",
  },
  moodle: {
    steps: [
      "Open Calendar, then Export calendar.",
      "Choose the course events and the time range you want.",
      "Select Get URL address and copy the link.",
    ],
    note: "Choose a range that covers upcoming deadlines. The link contains a private access token.",
  },
  brightspace: {
    steps: [
      "Open the course Calendar and its Settings.",
      "Enable Calendar Feeds, then choose Subscribe.",
      "Copy the subscription link for one course or all calendars.",
    ],
    note: "Some schools provide one feed per course. You can connect several links.",
  },
  google_classroom: {
    steps: [
      "Open the Google Calendar connection you already use with NavoX.",
      "Make sure the relevant Classroom calendars are included.",
    ],
    note: "Classroom due dates appear in Google Calendar. This does not enable a separate Classroom API connection.",
  },
  other: {
    steps: [
      "Open your school’s calendar or student portal.",
      "Look for Subscribe, Export, Calendar Feed, or an .ics subscription link.",
      "Copy its private HTTPS calendar link.",
    ],
    note: "If your school only offers a downloaded file, import that timetable in Classes instead.",
  },
};
export interface LmsCalendarFeed {
  id: string;
  name: string;
  domain: string;
  platform: LmsFeedPlatform;
  timezone: string;
  all_day_due_time: string;
  state: "preview" | "active" | "paused";
  revision: number;
  last_synced_at: string | null;
  next_sync_at: string | null;
  last_error: string | null;
  course_matches: Record<string, string | null>;
}
export interface LmsCalendarItem {
  external_id: string;
  occurrence_id: string;
  title: string;
  kind: "assignment" | "event" | "exam";
  starts_at: string | null;
  due_at: string | null;
  ends_at: string | null;
  all_day: boolean;
  course_hint: string | null;
  details: string;
  source_url: string | null;
  location: string | null;
  source_updated_at: string | null;
  cancelled: boolean;
}
export interface LmsSavedItem {
  id: string;
  revision: number;
  course_id: string | null;
  cancelled: boolean;
  conflicted: boolean;
  item: LmsCalendarItem;
}
export interface LmsFeedPreview {
  preview_token: string;
  feed_id: string;
  preview_hash: string;
  expires_at: string;
  domain: string;
  platform: LmsFeedPlatform;
  count: number;
  course_hints: string[];
  all_day_count: number;
  all_day_due_time: string;
  items: LmsCalendarItem[];
  notice: string;
}
export interface LmsItemsPage {
  items: LmsSavedItem[];
  next_cursor: string | null;
}
function invalid(): never {
  throw new Error(
    "The calendar’s saved details could not be checked. Reload the connection list.",
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
function integer(value: unknown, min = 0, max = 1_000_000): number {
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
  max = 5000,
): T[] {
  if (!Array.isArray(value) || value.length > max) return invalid();
  return value.map(parse);
}
function nullable<T>(value: unknown, parse: (value: unknown) => T): T | null {
  return value === null ? null : parse(value);
}
function uuid(value: unknown): string {
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
function clock(value: unknown): string {
  const v = text(value, 5);
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(v)) return invalid();
  return v;
}
function timezone(value: unknown): string {
  const v = text(value, 64);
  try {
    new Intl.DateTimeFormat("en", { timeZone: v }).format();
  } catch {
    return invalid();
  }
  return v;
}
function platform(value: unknown): LmsFeedPlatform {
  if (!lmsFeedPlatforms.includes(value as LmsFeedPlatform)) return invalid();
  return value as LmsFeedPlatform;
}
function domain(value: unknown): string {
  const v = text(value, 253);
  try {
    const url = new URL(`https://${v}`);
    if (
      url.hostname !== v.toLowerCase() ||
      url.username ||
      url.password ||
      url.port ||
      url.pathname !== "/" ||
      url.search ||
      url.hash
    )
      return invalid();
  } catch {
    return invalid();
  }
  return v;
}
export function readLmsCourseMatches(
  value: unknown,
): Record<string, string | null> {
  const source = obj(value);
  if (Object.keys(source).length > 100) return invalid();
  const result: Record<string, string | null> = {};
  for (const [hint, course] of Object.entries(source)) {
    if (hint.length > 120) return invalid();
    Object.defineProperty(result, hint, {
      value: nullable(course, uuid),
      enumerable: true,
      writable: true,
      configurable: true,
    });
  }
  return result;
}
export function readLmsFeed(value: unknown): LmsCalendarFeed {
  const v = obj(value);
  if (!["preview", "active", "paused"].includes(v.state as string))
    return invalid();
  return {
    id: uuid(v.id),
    name: text(v.name, 120),
    domain: domain(v.domain),
    platform: platform(v.platform),
    timezone: timezone(v.timezone),
    all_day_due_time: clock(v.all_day_due_time),
    state: v.state as LmsCalendarFeed["state"],
    revision: integer(v.revision, 1),
    last_synced_at: nullable(v.last_synced_at, instant),
    next_sync_at: nullable(v.next_sync_at, instant),
    last_error: nullable(v.last_error, (item) => text(item, 2000)),
    course_matches: readLmsCourseMatches(v.course_matches),
  };
}
export function readLmsFeeds(value: unknown): LmsCalendarFeed[] {
  return list(obj(value).feeds, readLmsFeed, 10);
}
export function readLmsCalendarItem(value: unknown): LmsCalendarItem {
  const v = obj(value);
  if (!["assignment", "event", "exam"].includes(v.kind as string))
    return invalid();
  return {
    external_id: text(v.external_id, 512),
    occurrence_id: text(v.occurrence_id, 128),
    title: text(v.title, 300),
    kind: v.kind as LmsCalendarItem["kind"],
    starts_at: nullable(v.starts_at, instant),
    due_at: nullable(v.due_at, instant),
    ends_at: nullable(v.ends_at, instant),
    all_day: bool(v.all_day),
    course_hint: nullable(v.course_hint, (item) => text(item, 120)),
    details: text(v.details, 2000),
    source_url: nullable(v.source_url, (item) => text(item, 2048)),
    location: nullable(v.location, (item) => text(item, 256)),
    source_updated_at: nullable(v.source_updated_at, instant),
    cancelled: bool(v.cancelled),
  };
}
export function readLmsPreview(value: unknown): LmsFeedPreview {
  const v = obj(value);
  const hash = text(v.preview_hash, 64);
  if (!/^[a-f0-9]{64}$/.test(hash)) return invalid();
  return {
    preview_token: uuid(v.preview_token),
    feed_id: uuid(v.feed_id),
    preview_hash: hash,
    expires_at: instant(v.expires_at),
    domain: domain(v.domain),
    platform: platform(v.platform),
    count: integer(v.count, 0, 5000),
    course_hints: list(v.course_hints, (item) => text(item, 120), 100),
    all_day_count: integer(v.all_day_count, 0, 5000),
    all_day_due_time: clock(v.all_day_due_time),
    items: list(v.items, readLmsCalendarItem),
    notice: text(v.notice, 2000),
  };
}
export function readLmsItems(value: unknown): LmsItemsPage {
  const v = obj(value);
  return {
    items: list(
      v.items,
      (item) => {
        const row = obj(item);
        return {
          id: uuid(row.id),
          revision: integer(row.revision, 1),
          course_id: nullable(row.course_id, uuid),
          cancelled: bool(row.cancelled),
          conflicted: bool(row.conflicted),
          item: readLmsCalendarItem(row.item),
        };
      },
      100,
    ),
    next_cursor: nullable(v.next_cursor, uuid),
  };
}
export function lmsCourseMatchesComplete(
  hints: string[],
  matches: Record<string, string | null | undefined>,
  activeCourseIds?: readonly string[],
): boolean {
  return hints.every(
    (hint) =>
      Object.hasOwn(matches, hint) &&
      (matches[hint] === null ||
        (typeof matches[hint] === "string" &&
          !!matches[hint] &&
          (!activeCourseIds ||
            activeCourseIds.includes(matches[hint] as string)))),
  );
}
export function lmsSafeSourceUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export class LmsFeedRequestError extends Error {
  constructor(
    message: string,
    readonly uncertain = false,
    readonly unavailable = false,
  ) {
    super(message);
  }
}
const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
/** Private feed URLs never enter error messages, logs, or browser persistence. */
export async function lmsFeedRequest<T>(
  path: string,
  parse: (value: unknown) => T,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20_000);
  try {
    const response = await fetch(`${apiBaseUrl}/student/lms-feeds${path}`, {
      method,
      credentials: "include",
      cache: "no-store",
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
      const safeDetail =
        payload &&
        typeof payload === "object" &&
        "detail" in payload &&
        typeof payload.detail === "string" &&
        safeLmsErrorDetails.includes(payload.detail)
          ? payload.detail
          : null;
      const message =
        response.status === 401
          ? "Sign in again to manage your calendars."
          : response.status === 403
            ? "This calendar is not available to this account."
            : response.status === 404
              ? "Calendar feeds are not available here yet. Reload your saved connections to check."
              : response.status === 409
                ? "These calendar details changed or the preview expired. Reload and review them again."
                : response.status === 429
                  ? "This calendar was checked recently. Wait a moment, then refresh its saved status."
                  : response.status === 422
                    ? (safeDetail ??
                      "Check the private calendar link, time zone, and review choices. You may need a fresh link from your school.")
                    : "The calendar could not be reached. You can keep using your saved timetable.";
      throw new LmsFeedRequestError(
        message,
        method !== "GET" && response.status >= 500,
        response.status === 404,
      );
    }
    try {
      return parse(payload);
    } catch {
      throw new LmsFeedRequestError(
        "The calendar result could not be checked. Reload saved connections before trying again.",
        method !== "GET",
      );
    }
  } catch (cause) {
    if (cause instanceof LmsFeedRequestError) throw cause;
    throw new LmsFeedRequestError(
      "The calendar could not be reached. Reload saved connections before trying again.",
      method !== "GET",
    );
  } finally {
    clearTimeout(timer);
  }
}

export interface LmsFeedMatches {
  revision: number;
  course_hints: string[];
  course_matches: Record<string, string | null>;
  unconfirmed_hints: string[];
}
export function readLmsFeedMatches(value: unknown): LmsFeedMatches {
  const v = obj(value);
  return {
    revision: integer(v.revision, 1),
    course_hints: list(v.course_hints, (item) => text(item, 120), 100),
    course_matches: readLmsCourseMatches(v.course_matches),
    unconfirmed_hints: list(
      v.unconfirmed_hints,
      (item) => text(item, 120),
      100,
    ),
  };
}
export function readLmsRefresh(value: unknown): {
  state: "queued";
  feed_id: string;
} {
  const v = obj(value);
  if (v.state !== "queued") return invalid();
  return { state: "queued", feed_id: uuid(v.feed_id) };
}

/** Only exact server-owned messages from bounded parser failures may be surfaced. */
const safeLmsErrorDetails = [
  "This calendar uses an unsupported or overly complex pattern. No events were saved. Try an exported schedule or another feed.",
  "This calendar exceeds the 5 MB limit. Try a feed for fewer courses.",
];
export function lmsNeedsFreshLink(code: string | null): boolean {
  return code === "feed_link_expired" || code === "feed_history_limit";
}
export function lmsFeedAttention(code: string | null): string {
  if (code === "feed_link_expired")
    return "This private calendar link expired. Copy a fresh link from your school and preview it as a new calendar. After the new connection works, review and remove the old one.";
  if (code === "feed_history_limit")
    return "This calendar reached its retained-item limit. Preview a fresh calendar link, then review and remove the old connection and its unique imported data.";
  return "This calendar needs attention. Review the link or course matches before relying on its dates.";
}
