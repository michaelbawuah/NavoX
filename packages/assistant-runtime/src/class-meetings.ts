import type {
  AssistantBlock,
  AssistantResponseState,
  CapabilityDecision,
  ClassMeeting,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import { isRecord, isUuid, parseCapabilityDecision } from "./validate";

type SourceRef = ClassMeeting["sourceRefs"][number];

interface CourseSource {
  courseId: string;
  name: string;
  code: string | null;
  section: string | null;
  source: SourceRef;
}

interface EventSource {
  provider: "canvas" | "google";
  courseId: string | null;
  title: string;
  startAt: string;
  endAt: string | null;
  location: string | null;
  meetingUrl: string | null;
  status: "SCHEDULED" | "CANCELLED";
  source: SourceRef;
}

export interface ClassSourceSnapshot {
  complete: boolean;
  courses: CourseSource[];
  events: EventSource[];
}

function unreadable(): never {
  throw new AssistantError(
    "unavailable",
    "Class schedule sources returned unverifiable data.",
  );
}

function text(value: unknown, max: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > max)
    unreadable();
  return value.trim();
}

function optionalText(value: unknown, max: number): string | null {
  if (value === null || value === undefined) return null;
  return text(value, max);
}

function moment(value: unknown, now: Date): string {
  const raw = text(value, 64);
  const parsed = Date.parse(raw);
  if (
    !Number.isFinite(parsed) ||
    !/(?:Z|[+-]\d{2}:\d{2})$/i.test(raw) ||
    parsed > now.getTime() + 366 * 86_400_000
  )
    unreadable();
  return raw;
}

function sourceRef(
  value: unknown,
  provider: "canvas" | "google",
  now: Date,
): SourceRef {
  if (!isRecord(value)) unreadable();
  const resourceId = value.resource_id;
  const connectionId = value.connection_id;
  if (!isUuid(resourceId) || !isUuid(connectionId)) unreadable();
  const updated = value.updated_at;
  return {
    provider,
    resourceId,
    connectionId,
    externalResourceId: text(value.external_resource_id, 512),
    updatedAt: updated === null ? null : moment(updated, now),
    navigationAvailable: value.navigation_available === true,
  };
}

function safeMeetingUrl(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const raw = text(value, 2048);
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.username || url.password) unreadable();
    return url.href;
  } catch {
    unreadable();
  }
}

/** Parse a permission-fenced SPEC-007 schedule projection before synthesis. */
export function parseClassSourceSnapshot(
  payload: unknown,
  now: Date,
): ClassSourceSnapshot {
  if (!isRecord(payload) || payload.complete !== true) unreadable();
  if (
    !Array.isArray(payload.courses) ||
    !Array.isArray(payload.events) ||
    payload.courses.length > 50 ||
    payload.events.length > 50
  )
    unreadable();
  const courses = payload.courses.map((entry): CourseSource => {
    if (!isRecord(entry)) unreadable();
    return {
      courseId: text(entry.course_id, 128),
      name: text(entry.course_name, 300),
      code: optionalText(entry.course_code, 128),
      section: optionalText(entry.section, 128),
      source: sourceRef(entry.source, "canvas", now),
    };
  });
  const events = payload.events.map((entry): EventSource => {
    if (!isRecord(entry)) unreadable();
    const provider = entry.provider;
    if (provider !== "canvas" && provider !== "google") unreadable();
    if (entry.status !== "SCHEDULED" && entry.status !== "CANCELLED")
      unreadable();
    if (entry.explicit_class_meeting !== true) unreadable();
    if (entry.all_day !== false) unreadable();
    const startAt = moment(entry.start_at, now);
    const endAt = entry.end_at === null ? null : moment(entry.end_at, now);
    if (endAt !== null && Date.parse(endAt) <= Date.parse(startAt))
      unreadable();
    const freshUntil = moment(entry.fresh_until, now);
    if (Date.parse(freshUntil) <= now.getTime()) unreadable();
    const courseId = optionalText(entry.course_id, 128);
    if (provider === "canvas" && courseId === null) unreadable();
    return {
      provider,
      courseId,
      title: text(entry.title, 300),
      startAt,
      endAt,
      location: optionalText(entry.location, 256),
      meetingUrl: safeMeetingUrl(entry.meeting_url),
      status: entry.status,
      source: sourceRef(entry.source, provider, now),
    };
  });
  const seen = new Set<string>();
  for (const source of [
    ...courses.map((row) => row.source),
    ...events.map((row) => row.source),
  ]) {
    if (seen.has(source.resourceId)) unreadable();
    seen.add(source.resourceId);
  }
  return { complete: true, courses, events };
}

function normalized(value: string): string {
  return value
    .toLocaleLowerCase("en-US")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function courseForEvent(
  event: EventSource,
  courses: CourseSource[],
): CourseSource | null {
  if (event.courseId !== null) {
    return (
      courses.find(
        (course) =>
          course.courseId === event.courseId &&
          (event.provider !== "canvas" ||
            course.source.connectionId === event.source.connectionId),
      ) ?? null
    );
  }
  const title = ` ${normalized(event.title)} `;
  const compactTitle = title.replace(/\s/g, "");
  const matches = courses.filter((course) =>
    [course.code, course.name]
      .filter(
        (value): value is string =>
          value !== null && normalized(value).length >= 3,
      )
      .some(
        (value) =>
          title.includes(` ${normalized(value)} `) ||
          (value === course.code &&
            compactTitle.includes(normalized(value).replace(/\s/g, ""))),
      ),
  );
  return matches.length === 1 ? (matches[0] ?? null) : null;
}

function chooseNewer(
  canvas: EventSource,
  google: EventSource,
): EventSource | null {
  const canvasAt =
    canvas.source.updatedAt && Date.parse(canvas.source.updatedAt);
  const googleAt =
    google.source.updatedAt && Date.parse(google.source.updatedAt);
  if (canvasAt === null || googleAt === null || canvasAt === googleAt)
    return null;
  return (googleAt as number) > (canvasAt as number) ? google : canvas;
}

function sessionType(title: string): string | null {
  return (
    title
      .match(/\b(lecture|lab|seminar|tutorial|section)\b/i)?.[1]
      ?.toLowerCase() ?? null
  );
}

function isClassTitle(title: string): boolean {
  if (
    /\b(assignment|quiz|exam|midterm|final|deadline|due|office hours)\b/i.test(
      title,
    )
  )
    return false;
  return (
    /\b(class|lecture|seminar|tutorial|lab|section)\b/i.test(title) ||
    /\b[A-Z]{2,8}[ -]?\d{3,5}\b/i.test(title)
  );
}

function single(event: EventSource, course: CourseSource | null): ClassMeeting {
  return {
    courseId: course?.courseId ?? event.courseId,
    courseName: course?.name ?? event.title,
    courseCode: course?.code ?? null,
    section: course?.section ?? null,
    startAt: event.startAt,
    endAt: event.endAt,
    location: event.location,
    meetingUrl: event.meetingUrl,
    status: event.status,
    sourceRefs: course ? [course.source, event.source] : [event.source],
    sourceSchedules: [schedule(event)],
    confidence: "SINGLE_SOURCE",
    scheduleAuthority: event.provider,
    conflictState: "NONE",
  };
}

function schedule(event: EventSource): ClassMeeting["sourceSchedules"][number] {
  return {
    provider: event.provider,
    startAt: event.startAt,
    endAt: event.endAt,
    location: event.location,
    status: event.status,
    updatedAt: event.source.updatedAt,
  };
}

function paired(
  canvas: EventSource,
  google: EventSource,
  course: CourseSource,
): ClassMeeting {
  const timeConflict =
    Math.abs(Date.parse(canvas.startAt) - Date.parse(google.startAt)) >
    5 * 60_000;
  const statusConflict = canvas.status !== google.status;
  const locationConflict =
    canvas.location !== null &&
    google.location !== null &&
    normalized(canvas.location) !== normalized(google.location);
  const conflictState: ClassMeeting["conflictState"] = statusConflict
    ? "STATUS_CONFLICT"
    : timeConflict
      ? "TIME_CONFLICT"
      : locationConflict
        ? "LOCATION_CONFLICT"
        : "NONE";
  const preferred =
    conflictState === "NONE" ? google : chooseNewer(canvas, google);
  return {
    courseId: course.courseId,
    courseName: course.name,
    courseCode: course.code,
    section: course.section,
    startAt: preferred?.startAt ?? null,
    endAt:
      conflictState === "NONE"
        ? (google.endAt ?? canvas.endAt)
        : (preferred?.endAt ?? null),
    location:
      conflictState === "NONE"
        ? (google.location ?? canvas.location)
        : (preferred?.location ?? null),
    meetingUrl:
      conflictState === "NONE"
        ? (google.meetingUrl ?? canvas.meetingUrl)
        : (preferred?.meetingUrl ?? null),
    status: preferred?.status ?? "UNCERTAIN",
    sourceRefs: [course.source, canvas.source, google.source],
    sourceSchedules: [schedule(canvas), schedule(google)],
    confidence: conflictState === "NONE" ? "CORROBORATED" : "CONFLICTED",
    scheduleAuthority: preferred?.provider ?? null,
    conflictState: preferred === null ? "UNRESOLVED" : conflictState,
  };
}

/** Canvas owns identity; explicit Calendar updates can supersede schedule, with the conflict retained. */
export function normalizeClassMeetings(
  snapshot: ClassSourceSnapshot,
  now: Date,
): ClassMeeting[] {
  const courses = snapshot.courses;
  const canvas = snapshot.events.filter((event) => event.provider === "canvas");
  const google = snapshot.events.filter((event) => event.provider === "google");
  const used = new Set<string>();
  const meetings: ClassMeeting[] = [];
  for (const canvasEvent of canvas) {
    const course = courseForEvent(canvasEvent, courses);
    if (!course) {
      meetings.push(single(canvasEvent, null));
      continue;
    }
    const matches = google.filter((event) => {
      if (used.has(event.source.resourceId)) return false;
      const linked = courseForEvent(event, courses);
      return (
        linked?.courseId === course.courseId &&
        (sessionType(event.title) === null ||
          sessionType(canvasEvent.title) === null ||
          sessionType(event.title) === sessionType(canvasEvent.title)) &&
        Math.abs(Date.parse(event.startAt) - Date.parse(canvasEvent.startAt)) <=
          4 * 60 * 60_000
      );
    });
    if (matches.length === 1) {
      const match = matches[0];
      if (!match) continue;
      used.add(match.source.resourceId);
      meetings.push(paired(canvasEvent, match, course));
    } else {
      meetings.push(single(canvasEvent, course));
    }
  }
  for (const event of google) {
    if (used.has(event.source.resourceId)) continue;
    const course = courseForEvent(event, courses);
    if (course || isClassTitle(event.title))
      meetings.push(single(event, course));
  }
  return meetings
    .filter((meeting) =>
      meeting.sourceSchedules.some(
        (schedule) => Date.parse(schedule.startAt) > now.getTime(),
      ),
    )
    .sort(
      (a, b) =>
        Date.parse(a.startAt ?? "9999-12-31") -
        Date.parse(b.startAt ?? "9999-12-31"),
    );
}

export function nextClassMeeting(
  meetings: readonly ClassMeeting[],
  now: Date,
): ClassMeeting | null {
  return (
    meetings.find(
      (meeting) =>
        meeting.status === "SCHEDULED" &&
        meeting.startAt !== null &&
        Date.parse(meeting.startAt) > now.getTime(),
    ) ?? null
  );
}

function clock(value: string, timezone: string | null): string {
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: timezone ?? "UTC",
    timeZoneName: "short",
  }).format(new Date(value));
}

function classDecision(
  state: AssistantResponseState,
  reason: string,
): CapabilityDecision {
  return parseCapabilityDecision({
    kind: state === "READY" ? "DELEGATE" : "CLARIFY",
    capability_id: "class.next",
    target: "knowledge.class-sources",
    reason,
    requires_approval: false,
    action_state: "NONE",
    action_id: null,
    response_state: state,
  });
}

/** No next-class claim is made while an unresolved source conflict remains. */
export function answerNextClass(
  snapshot: ClassSourceSnapshot,
  now: Date,
  timezone: string | null,
): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  const meetings = normalizeClassMeetings(snapshot, now);
  const unresolved = meetings.find(
    (meeting) =>
      meeting.conflictState === "UNRESOLVED" ||
      (meeting.conflictState === "STATUS_CONFLICT" &&
        meeting.status === "CANCELLED"),
  );
  if (unresolved) {
    const label = unresolved.courseCode ?? unresolved.courseName ?? "a class";
    const schedules = unresolved.sourceSchedules.map(
      (row) =>
        `${row.provider === "google" ? "Google Calendar" : "Canvas"}: ${clock(row.startAt, timezone)}${row.location ? ` at ${row.location}` : ""} (${row.status.toLowerCase()})`,
    );
    return {
      state: "CLARIFY",
      decision: classDecision("CLARIFY", "class.schedule_conflict"),
      blocks: [
        {
          kind: "ANSWER",
          text: `I can't determine your next class because ${label} has conflicting schedules. ${schedules.join("; ")}. Please confirm the class time.`,
        },
      ],
    };
  }
  const meeting = nextClassMeeting(meetings, now);
  if (!meeting || meeting.startAt === null) {
    return {
      state: "CLARIFY",
      decision: classDecision("CLARIFY", "class.no_meeting_time"),
      blocks: [
        {
          kind: "ANSWER",
          text: "I can't determine your next class from the current connected calendar meeting records.",
        },
      ],
    };
  }
  const label = meeting.courseCode ?? meeting.courseName ?? "your class";
  let text = `Your next class is ${label} at ${clock(meeting.startAt, timezone)}.`;
  if (meeting.location) text += ` Location: ${meeting.location}.`;
  if (meeting.conflictState !== "NONE") {
    const schedules = meeting.sourceSchedules.map(
      (row) =>
        `${row.provider === "google" ? "Google Calendar" : "Canvas"} shows ${clock(row.startAt, timezone)}${row.location ? ` at ${row.location}` : ""}${row.status === "CANCELLED" ? " (cancelled)" : ""}`,
    );
    text += ` The sources conflict: ${schedules.join("; ")}. ${meeting.scheduleAuthority === "google" ? "The Google Calendar entry" : "The Canvas entry"} was updated more recently. You may want to confirm the change.`;
  }
  const navigation = meeting.sourceRefs.find(
    (source) =>
      source.provider === meeting.scheduleAuthority &&
      source.navigationAvailable &&
      (source.provider === "google" ||
        source.externalResourceId.startsWith("event:")),
  );
  return {
    state: "READY",
    decision: classDecision(
      "READY",
      meeting.conflictState === "NONE"
        ? "class.meeting"
        : "class.conflicted_meeting",
    ),
    blocks: [
      { kind: "ANSWER", text },
      ...(navigation
        ? ([
            {
              kind: "CLASS_NAVIGATION" as const,
              label:
                navigation.provider === "google"
                  ? ("Open Calendar" as const)
                  : ("Open Class" as const),
              connection_id: navigation.connectionId,
              resource_id: navigation.resourceId,
            },
          ] as const)
        : []),
    ],
  };
}
