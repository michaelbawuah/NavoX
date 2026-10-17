import type {
  AssistantBlock,
  CapabilityDecision,
  IntentPlan,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import {
  isRecord,
  isUuid,
  parseCapabilityDecision,
  parseIntentPlan,
} from "./validate";

export interface StudentLookupRequest {
  mode: "now" | "next" | "at" | "day";
  date?: string;
  day_offset?: number;
  weekday?: number;
  time?: string;
}

export interface StudentClassOccurrence {
  id: string;
  course_code: string;
  course_name: string;
  start_at: string;
  end_at: string;
  building: string | null;
  room: string | null;
}

export interface StudentLookupSnapshot {
  mode: StudentLookupRequest["mode"];
  server_now: string;
  timezone: string;
  date: string;
  at: string;
  occurrences: StudentClassOccurrence[];
  next_upcoming: StudentClassOccurrence | null;
  reason: "matches" | "no_classes" | "after_last_class" | "break";
  conflicts: { left_id: string; right_id: string }[];
}

const WEEKDAYS = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
];
const DAY =
  "(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\\d{4}-\\d{2}-\\d{2})";

function daySelector(
  day: string | undefined,
): Pick<StudentLookupRequest, "date" | "day_offset" | "weekday"> {
  if (!day || day === "today") return {};
  if (day === "tomorrow") return { day_offset: 1 };
  const weekday = WEEKDAYS.indexOf(day);
  return weekday < 0 ? { date: day } : { weekday };
}

/** Only complete, bounded class questions bypass the AI planner. No action is inferred. */
export function studentLookupRequest(
  utterance: string,
): StudentLookupRequest | null {
  const text = utterance
    .trim()
    .toLowerCase()
    .replace(/[?!.]+$/, "")
    .replace(/[’']/g, "")
    .replace(/\s+/g, " ");
  if (
    /^(?:(?:what|which)(?: is|s)? (?:my |the )?(?:current )?class(?: do i have| am i in)?(?: (?:right )?now)?|where(?: is|s) (?:my |the )?(?:current )?class(?: (?:right )?now)?)$/.test(
      text,
    )
  )
    return { mode: "now" };
  if (
    /^(?:(?:what|which|when)(?: is|s)? (?:my |the )?next class|(?:what|which) class (?:do i have|is) next|where(?: is|s) (?:my |the )?next class|(?:my )?next class)$/.test(
      text,
    )
  )
    return { mode: "next" };
  const day = new RegExp(
    `^(?:(?:what|which) classes(?: do i have| have i got)?|(?:show|read)(?: me)? (?:my )?(?:classes|timetable|schedule)|(?:my )?(?:classes|timetable|schedule))(?: (?:on |for )?(${DAY}))?$`,
  ).exec(text);
  if (day) return { mode: "day", ...daySelector(day[1]) };
  const at = new RegExp(
    `^(?:(?:what|which) class(?: do i have| have i got| is on)?|where(?: is|s) (?:my )?class) at (\\d{1,2})(?::(\\d{2}))?\\s*(am|pm)?(?: (?:on )?(${DAY}))?$`,
  ).exec(text);
  if (!at) return null;
  let hour = Number(at[1]);
  const minute = Number(at[2] ?? "0");
  const meridiem = at[3];
  // A bare one-digit clock time is ambiguous, so ask the student to specify AM/PM.
  if (
    minute > 59 ||
    hour > 23 ||
    (meridiem && (hour < 1 || hour > 12)) ||
    (!meridiem && hour >= 1 && hour <= 12 && (!at[2] || at[1].length !== 2))
  )
    return null;
  if (meridiem) hour = (hour % 12) + (meridiem === "pm" ? 12 : 0);
  return {
    mode: "at",
    time: `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`,
    ...daySelector(at[4]),
  };
}

/** A class write remains a reviewable form; the transcript never executes it. */
export function studentClassDraftText(utterance: string): string | null {
  const text = utterance.trim();
  if (
    text.length > 500 ||
    /(?:\b(?:and|then|also)\s+|[,;]\s*)(?:send|email|delete|pay|buy|transfer|cancel|book|install|run|execute|approve|share|publish)\b/i.test(
      text,
    )
  )
    return null;
  const prefix =
    /^(?:please\s+)?(?:add|create)\s+(?:(?:a|an|my|the)\s+)?/i.exec(text);
  if (!prefix) return null;
  const description = text.slice(prefix[0].length);
  if (
    /^(?:class|lecture|lab|discussion|seminar|recitation)\b/i.test(description)
  )
    return text;
  const schedule =
    "(?=$|[,.:;]|\\s+(?:on|at|from|every|this|next|in|mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|\\d)\\b)";
  const courseCode = new RegExp(
    `^[a-z]{2,8}\\s*\\d{3,5}[a-z]?(?:\\s+(?:class|lecture|lab|discussion|seminar|recitation))?\\b${schedule}`,
    "i",
  );
  const namedSection = new RegExp(
    `^(?:[a-z][a-z-]{1,30}\\s+){1,3}(?:lecture|lab|discussion|seminar|recitation)\\b${schedule}`,
    "i",
  );
  return courseCode.test(description) || namedSection.test(description)
    ? text
    : null;
}

function unreadable(): never {
  throw new AssistantError(
    "unavailable",
    "Your timetable could not be verified. Open Timetable to check it.",
  );
}
function boundedText(value: unknown, max: number, empty = false): string {
  if (
    typeof value !== "string" ||
    value.length > max ||
    (!empty && !value.trim()) ||
    Array.from(value).some(
      (character) =>
        character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  )
    unreadable();
  return value;
}
function instant(value: unknown): string {
  const text = boundedText(value, 64);
  if (
    !/(?:Z|[+-]\d{2}:\d{2})$/i.test(text) ||
    !Number.isFinite(Date.parse(text))
  )
    unreadable();
  return text;
}
function occurrence(value: unknown): StudentClassOccurrence {
  if (
    !isRecord(value) ||
    !isUuid(value.meeting_id) ||
    !isUuid(value.course_id) ||
    value.cancelled !== false
  )
    unreadable();
  const start = instant(value.start_at);
  const end = instant(value.end_at);
  if (Date.parse(start) >= Date.parse(end)) unreadable();
  const id = boundedText(value.id, 96);
  if (
    !id.startsWith(`${value.meeting_id}:`) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(id.slice(37))
  )
    unreadable();
  return {
    id,
    course_code: boundedText(value.course_code, 40, true),
    course_name: boundedText(value.course_name, 200),
    start_at: start,
    end_at: end,
    building: value.building === null ? null : boundedText(value.building, 200),
    room: value.room === null ? null : boundedText(value.room, 100),
  };
}

/** The API owns the clock, student timezone, recurrence, exceptions and conflict detection. */
export function parseStudentLookup(
  payload: unknown,
  request: StudentLookupRequest,
): StudentLookupSnapshot {
  if (
    !isRecord(payload) ||
    payload.mode !== request.mode ||
    !Array.isArray(payload.occurrences) ||
    payload.occurrences.length > 250 ||
    !Array.isArray(payload.conflicts) ||
    payload.conflicts.length > 500
  )
    unreadable();
  const timezone = boundedText(payload.timezone, 100);
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: timezone }).format();
  } catch {
    unreadable();
  }
  const date = boundedText(payload.date, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) unreadable();
  const reason = payload.reason;
  if (
    reason !== "matches" &&
    reason !== "no_classes" &&
    reason !== "after_last_class" &&
    reason !== "break"
  )
    unreadable();
  const occurrences = payload.occurrences.map(occurrence);
  if (
    (reason === "matches") !== occurrences.length > 0 ||
    new Set(occurrences.map((row) => row.id)).size !== occurrences.length
  )
    unreadable();
  const at = instant(payload.at);
  const serverNow = instant(payload.server_now);
  if (
    request.mode === "now" &&
    (Date.parse(at) !== Date.parse(serverNow) ||
      occurrences.some(
        (row) =>
          Date.parse(row.start_at) > Date.parse(at) ||
          Date.parse(row.end_at) <= Date.parse(at),
      ))
  )
    unreadable();
  if (
    request.mode === "at" &&
    occurrences.some(
      (row) =>
        Date.parse(row.start_at) > Date.parse(at) ||
        Date.parse(row.end_at) <= Date.parse(at),
    )
  )
    unreadable();
  if (
    request.mode === "next" &&
    occurrences.some((row) => Date.parse(row.start_at) <= Date.parse(serverNow))
  )
    unreadable();
  const conflicts = payload.conflicts.map((item) => {
    if (!isRecord(item)) unreadable();
    return {
      left_id: boundedText(item.left_id, 100),
      right_id: boundedText(item.right_id, 100),
    };
  });
  return {
    mode: request.mode,
    server_now: serverNow,
    timezone,
    date,
    at,
    reason,
    occurrences,
    next_upcoming:
      payload.next_upcoming === null ? null : occurrence(payload.next_upcoming),
    conflicts,
  };
}

export function studentLookupPlan(question: string): IntentPlan {
  return parseIntentPlan({
    version: 1,
    intents: [
      {
        kind: "class.next",
        capability_id: "class.next",
        question,
        confidence: 1,
      },
    ],
  });
}

export function answerStudentLookup(snapshot: StudentLookupSnapshot): {
  state: "READY";
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
  text: string;
} {
  const clock = (value: string) =>
    new Intl.DateTimeFormat("en-US", {
      timeZone: snapshot.timezone,
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(value));
  const describe = (row: StudentClassOccurrence) =>
    `${row.course_code || row.course_name}, ${clock(row.start_at)}–${clock(row.end_at)}${[row.building, row.room].filter(Boolean).length ? `, ${[row.building, row.room].filter(Boolean).join(" ")}` : ", location not set"}`;
  let text: string;
  if (snapshot.occurrences.length) {
    const prefix =
      snapshot.mode === "now"
        ? "Right now"
        : snapshot.mode === "next"
          ? "Your next class"
          : snapshot.mode === "at"
            ? `At ${clock(snapshot.at)}`
            : `Your classes for ${snapshot.date}`;
    text = `${prefix}: ${snapshot.occurrences.slice(0, 8).map(describe).join("; ")}.`;
    if (snapshot.occurrences.length > 8)
      text += ` There are ${snapshot.occurrences.length - 8} more classes. Open Timetable for the full day.`;
    const ids = new Set(snapshot.occurrences.map((row) => row.id));
    if (
      snapshot.conflicts.some(
        (conflict) => ids.has(conflict.left_id) || ids.has(conflict.right_id),
      )
    )
      text +=
        " There is a schedule conflict. Review Timetable before choosing where to go.";
  } else if (snapshot.mode === "now")
    text = "You do not have a class scheduled right now.";
  else if (snapshot.mode === "at")
    text = `You do not have a class scheduled at ${clock(snapshot.at)} on ${snapshot.date}.`;
  else if (snapshot.mode === "next" && snapshot.reason === "after_last_class")
    text = "Your classes are finished for today.";
  else text = `No classes are scheduled for ${snapshot.date}.`;
  if (!snapshot.occurrences.length && snapshot.next_upcoming)
    text += ` Next up: ${describe(snapshot.next_upcoming)} on ${new Intl.DateTimeFormat("en-US", { timeZone: snapshot.timezone, weekday: "long", month: "short", day: "numeric" }).format(new Date(snapshot.next_upcoming.start_at))}.`;
  if (text.length > 3900)
    text = `${snapshot.occurrences.length} classes match. Open Timetable to see all class times and locations${snapshot.conflicts.length ? ", including schedule conflicts" : ""}.`;
  return {
    state: "READY",
    decision: parseCapabilityDecision({
      kind: "DELEGATE",
      capability_id: "class.next",
      target: "student.lookup",
      reason: "class.student_timetable",
      requires_approval: false,
      action_state: "NONE",
      action_id: null,
      response_state: "READY",
    }),
    blocks: [{ kind: "ANSWER", text }],
    text,
  };
}

/** Keep the speech grant's 600-character contract without cutting a room or conflict warning. */
export function studentLookupSpeech(
  snapshot: StudentLookupSnapshot,
  answer: string,
): string {
  if (answer.length <= 600) return answer;
  return `${snapshot.occurrences.length} ${snapshot.occurrences.length === 1 ? "class matches" : "classes match"} your question.${snapshot.conflicts.length ? " There is a schedule conflict." : ""} Open Timetable to see every class time and room.`;
}
