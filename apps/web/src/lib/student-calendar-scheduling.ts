/** Reviewed scheduling uses its own Google permission; busy-time consent never grants writes. */
import {
  type AvailabilityPreviewInput,
  CALENDAR_READ_SCOPES,
  type CalendarAuthorization,
  type CalendarCommand,
  CalendarRequestError,
  createStudentCalendarApi,
  createStudentCalendarTransport,
  readAvailabilityPreviewInput,
  readCalendarAuthorization,
  readCalendarCommand,
  type StudentCalendarApi,
  type StudentCalendarTransportOptions,
} from "./student-calendar-api";

export const CALENDAR_SCHEDULING_CONSENT_VERSION = "calendar-scheduling.v1";
export const CALENDAR_SCHEDULING_SCOPES = [
  ...CALENDAR_READ_SCOPES,
  "https://www.googleapis.com/auth/calendar.events.owned",
] as const;
export interface CalendarSchedulingGrant {
  id: string;
  provider: "google";
  connection_id: string | null;
  account_label: string | null;
  status: "pending" | "connected" | "unavailable" | "revoked";
  revision: number;
}
export interface CalendarSchedulingView {
  generation: number;
  revision: number;
  consent_version: typeof CALENDAR_SCHEDULING_CONSENT_VERSION;
  grants: CalendarSchedulingGrant[];
  changes_applied: false;
}
export interface CalendarSchedulingStart extends CalendarCommand {
  consent_version: typeof CALENDAR_SCHEDULING_CONSENT_VERSION;
  confirmed: true;
  connection_id: string;
}
export interface CalendarSchedulingTarget {
  id: string;
  label: string;
  timezone: string;
}
export interface CalendarSchedulingTargets {
  generation: number;
  revision: number;
  grant_id: string;
  targets: CalendarSchedulingTarget[];
  changes_applied: false;
}
export interface CalendarEventPreviewInput extends CalendarCommand {
  grant_id: string;
  calendar_id: string;
  title: string;
  location: string;
  start_at: string;
  end_at: string;
  timezone: string;
  availability: AvailabilityPreviewInput;
}
export type CalendarEventStatus =
  | "pending"
  | "executing"
  | "uncertain"
  | "created"
  | "changed"
  | "undoing"
  | "undone"
  | "failed";
export interface CalendarEventView {
  generation: number;
  calendar_revision: number;
  id: string;
  revision: number;
  digest: string;
  status: CalendarEventStatus;
  calendar_label: string;
  title: string;
  location: string;
  start_at: string;
  end_at: string;
  timezone: string;
  expires_at: string;
  changes_applied: boolean | null;
  can_undo: boolean;
  message: string;
}
export interface CalendarEventsView {
  generation: number;
  calendar_revision: number;
  events: CalendarEventView[];
  next_cursor: string | null;
}
export interface CalendarEventCommand extends CalendarCommand {
  event_revision: number;
  digest: string;
}
export interface CalendarEventApproval extends CalendarEventCommand {
  confirmed: true;
}
export interface StudentCalendarSchedulingApi extends StudentCalendarApi {
  scheduling(signal: AbortSignal): Promise<CalendarSchedulingView>;
  startScheduling(
    input: CalendarSchedulingStart,
    signal: AbortSignal,
  ): Promise<CalendarAuthorization>;
  stopScheduling(
    grantId: string,
    input: CalendarCommand,
    signal: AbortSignal,
  ): Promise<CalendarSchedulingView>;
  targets(
    grantId: string,
    input: CalendarCommand,
    signal: AbortSignal,
  ): Promise<CalendarSchedulingTargets>;
  previewEvent(
    input: CalendarEventPreviewInput,
    signal: AbortSignal,
  ): Promise<CalendarEventView>;
  events(signal: AbortSignal, cursor?: string): Promise<CalendarEventsView>;
  approveEvent(
    eventId: string,
    input: CalendarEventApproval,
    signal: AbortSignal,
  ): Promise<CalendarEventView>;
  reconcileEvent(
    eventId: string,
    input: CalendarEventCommand,
    signal: AbortSignal,
  ): Promise<CalendarEventView>;
  undoEvent(
    eventId: string,
    input: CalendarEventApproval,
    signal: AbortSignal,
  ): Promise<CalendarEventView>;
}
function invalid(): never {
  throw new CalendarRequestError("invalid-response");
}
function obj(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  const v = value as Record<string, unknown>;
  if (
    Object.keys(v).length !== keys.length ||
    Object.keys(v).some((key) => !keys.includes(key))
  )
    return invalid();
  return v;
}
function text(value: unknown, max: number, min = 0): string {
  if (
    typeof value !== "string" ||
    value.length > max ||
    value.length < min ||
    [...value].some((char) => {
      const code = char.charCodeAt(0);
      return code < 32 || (code >= 127 && code <= 159);
    })
  )
    return invalid();
  return value;
}
function id(value: unknown): string {
  const v = text(value, 36, 36);
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      v,
    )
  )
    return invalid();
  return v.toLowerCase();
}
function num(value: unknown): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < 0 ||
    value > 2147483647
  )
    return invalid();
  return value;
}
function digest(value: unknown): string {
  const v = text(value, 64, 64);
  if (!/^[a-f0-9]{64}$/.test(v)) return invalid();
  return v;
}
function instant(value: unknown): string {
  const v = text(value, 40, 20);
  if (
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(
      v,
    ) ||
    !Number.isFinite(Date.parse(v))
  )
    return invalid();
  return v;
}
function timezone(value: unknown): string {
  const v = text(value, 64, 1);
  try {
    new Intl.DateTimeFormat("en", { timeZone: v }).format();
  } catch {
    return invalid();
  }
  return v;
}
function array<T>(
  value: unknown,
  read: (item: unknown) => T,
  max: number,
  key: (item: T) => string,
): T[] {
  if (!Array.isArray(value) || value.length > max) return invalid();
  const result = value.map(read);
  if (new Set(result.map(key)).size !== result.length) return invalid();
  return result;
}
export function readCalendarSchedulingView(
  value: unknown,
): CalendarSchedulingView {
  const v = obj(value, [
    "generation",
    "revision",
    "consent_version",
    "grants",
    "changes_applied",
  ]);
  if (
    v.consent_version !== CALENDAR_SCHEDULING_CONSENT_VERSION ||
    v.changes_applied !== false
  )
    return invalid();
  return {
    generation: num(v.generation),
    revision: num(v.revision),
    consent_version: CALENDAR_SCHEDULING_CONSENT_VERSION,
    changes_applied: false,
    grants: array(
      v.grants,
      (item) => {
        const g = obj(item, [
          "id",
          "provider",
          "connection_id",
          "account_label",
          "status",
          "revision",
        ]);
        if (
          g.provider !== "google" ||
          (g.status === "connected" && g.connection_id === null)
        )
          return invalid();
        if (
          !["pending", "connected", "unavailable", "revoked"].includes(
            String(g.status),
          )
        )
          return invalid();
        return {
          id: id(g.id),
          provider: "google" as const,
          connection_id: g.connection_id === null ? null : id(g.connection_id),
          account_label:
            g.account_label === null ? null : text(g.account_label, 320),
          status: g.status as CalendarSchedulingGrant["status"],
          revision: num(g.revision),
        };
      },
      200,
      (g) => g.id,
    ),
  };
}
export function readCalendarEventView(value: unknown): CalendarEventView {
  const v = obj(value, [
    "generation",
    "calendar_revision",
    "id",
    "revision",
    "digest",
    "status",
    "calendar_label",
    "title",
    "location",
    "start_at",
    "end_at",
    "timezone",
    "expires_at",
    "changes_applied",
    "can_undo",
    "message",
  ]);
  if (
    ![
      "pending",
      "executing",
      "uncertain",
      "created",
      "changed",
      "undoing",
      "undone",
      "failed",
    ].includes(String(v.status)) ||
    v.changes_applied !==
      (v.status === "created"
        ? true
        : ["pending", "failed", "undone"].includes(String(v.status))
          ? false
          : null) ||
    typeof v.can_undo !== "boolean" ||
    (v.can_undo && v.status !== "created")
  )
    return invalid();
  const start_at = instant(v.start_at),
    end_at = instant(v.end_at);
  if (
    Date.parse(end_at) <= Date.parse(start_at) ||
    Date.parse(end_at) - Date.parse(start_at) > 480 * 60_000
  )
    return invalid();
  return {
    generation: num(v.generation),
    calendar_revision: num(v.calendar_revision),
    id: id(v.id),
    revision: num(v.revision),
    digest: digest(v.digest),
    status: v.status as CalendarEventStatus,
    calendar_label: text(v.calendar_label, 1024, 1),
    title: text(v.title, 200, 1),
    location: text(v.location, 500),
    start_at,
    end_at,
    timezone: timezone(v.timezone),
    expires_at: instant(v.expires_at),
    changes_applied: v.changes_applied as boolean | null,
    can_undo: v.can_undo,
    message: text(v.message, 1000),
  };
}
function eventCommand(input: CalendarEventCommand): CalendarEventCommand {
  obj(input, [
    "generation",
    "revision",
    "request_id",
    "event_revision",
    "digest",
  ]);
  if (num(input.event_revision) < 1) return invalid();
  return {
    ...readCalendarCommand({
      generation: input.generation,
      revision: input.revision,
      request_id: input.request_id,
    }),
    event_revision: num(input.event_revision),
    digest: digest(input.digest),
  };
}
function approval(input: CalendarEventApproval): CalendarEventApproval {
  obj(input, [
    "generation",
    "revision",
    "request_id",
    "event_revision",
    "digest",
    "confirmed",
  ]);
  if (input.confirmed !== true) return invalid();
  const { confirmed: _, ...rest } = input;
  return { ...eventCommand(rest), confirmed: true };
}
function boundEvent(
  value: unknown,
  eventId: string,
  request: CalendarEventCommand,
): CalendarEventView {
  const event = readCalendarEventView(value);
  if (
    event.id !== eventId ||
    event.generation !== request.generation ||
    event.digest !== request.digest ||
    event.revision < request.event_revision ||
    event.calendar_revision < request.revision
  )
    return invalid();
  return event;
}
export function createStudentCalendarSchedulingApi(
  accountId: string,
  options: StudentCalendarTransportOptions = {},
): StudentCalendarSchedulingApi {
  const send = createStudentCalendarTransport(accountId, options);
  return {
    ...createStudentCalendarApi(accountId, options),
    scheduling: (signal) =>
      send(
        "/student/calendars/scheduling",
        readCalendarSchedulingView,
        signal,
        undefined,
        512 * 1024,
      ),
    startScheduling: (input, signal) => {
      obj(input, [
        "generation",
        "revision",
        "request_id",
        "consent_version",
        "confirmed",
        "connection_id",
      ]);
      if (
        input.consent_version !== CALENDAR_SCHEDULING_CONSENT_VERSION ||
        input.confirmed !== true
      )
        return invalid();
      const checked = {
        ...readCalendarCommand({
          generation: input.generation,
          revision: input.revision,
          request_id: input.request_id,
        }),
        consent_version: CALENDAR_SCHEDULING_CONSENT_VERSION,
        confirmed: true,
        connection_id: id(input.connection_id),
      };
      return send(
        "/student/calendars/google/scheduling/start",
        (value) =>
          readCalendarAuthorization(value, checked, CALENDAR_SCHEDULING_SCOPES),
        signal,
        checked,
      );
    },
    stopScheduling: (grantId, input, signal) =>
      send(
        `/student/calendars/scheduling/${id(grantId)}/disconnect`,
        readCalendarSchedulingView,
        signal,
        readCalendarCommand(input),
        512 * 1024,
      ),
    targets: (grantId, input, signal) => {
      const checked = { ...readCalendarCommand(input), grant_id: id(grantId) };
      return send(
        "/student/calendars/scheduling/targets",
        (value) => {
          const v = obj(value, [
            "generation",
            "revision",
            "grant_id",
            "targets",
            "changes_applied",
          ]);
          if (
            v.changes_applied !== false ||
            v.grant_id !== checked.grant_id ||
            num(v.generation) !== checked.generation ||
            num(v.revision) < checked.revision
          )
            return invalid();
          return {
            generation: num(v.generation),
            revision: num(v.revision),
            grant_id: id(v.grant_id),
            changes_applied: false,
            targets: array(
              v.targets,
              (item) => {
                const t = obj(item, ["id", "label", "timezone"]);
                return {
                  id: text(t.id, 1024, 1),
                  label: text(t.label, 1024, 1),
                  timezone: timezone(t.timezone),
                };
              },
              100,
              (t) => t.id,
            ),
          };
        },
        signal,
        checked,
        1024 * 1024,
      );
    },
    previewEvent: (input, signal) => {
      obj(input, [
        "generation",
        "revision",
        "request_id",
        "grant_id",
        "calendar_id",
        "title",
        "location",
        "start_at",
        "end_at",
        "timezone",
        "availability",
      ]);
      const checked = {
        ...readCalendarCommand({
          generation: input.generation,
          revision: input.revision,
          request_id: input.request_id,
        }),
        grant_id: id(input.grant_id),
        calendar_id: text(input.calendar_id, 1024, 1),
        title: text(input.title, 200, 1),
        location: text(input.location, 500),
        start_at: instant(input.start_at),
        end_at: instant(input.end_at),
        timezone: timezone(input.timezone),
        availability: readAvailabilityPreviewInput(input.availability),
      };
      if (Date.parse(checked.end_at) <= Date.parse(checked.start_at))
        return invalid();
      return send(
        "/student/calendars/events/preview",
        (value) => {
          const event = readCalendarEventView(value);
          if (
            event.generation !== checked.generation ||
            event.calendar_revision < checked.revision ||
            event.revision < 1 ||
            event.status !== "pending" ||
            event.title !== checked.title ||
            event.location !== checked.location ||
            event.timezone !== checked.timezone ||
            Date.parse(event.start_at) !== Date.parse(checked.start_at) ||
            Date.parse(event.end_at) !== Date.parse(checked.end_at) ||
            event.changes_applied ||
            event.can_undo
          )
            return invalid();
          return event;
        },
        signal,
        checked,
        65536,
        55_000,
      );
    },
    events: (signal, cursor) =>
      send(
        `/student/calendars/events${cursor ? `?cursor=${id(cursor)}` : ""}`,
        (value) => {
          const v = obj(value, [
              "generation",
              "calendar_revision",
              "events",
              "next_cursor",
            ]),
            generation = num(v.generation);
          const events = array(
            v.events,
            readCalendarEventView,
            20,
            (e) => e.id,
          );
          if (
            events.some(
              (e) =>
                e.generation !== generation ||
                e.calendar_revision !== num(v.calendar_revision),
            )
          )
            return invalid();
          const next_cursor = v.next_cursor === null ? null : id(v.next_cursor);
          if (
            next_cursor !== null &&
            (events.at(-1)?.id !== next_cursor || next_cursor === cursor)
          )
            return invalid();
          return {
            generation,
            calendar_revision: num(v.calendar_revision),
            events,
            next_cursor,
          };
        },
        signal,
        undefined,
        256 * 1024,
      ),
    approveEvent: (eventId, input, signal) => {
      const selected = id(eventId),
        checked = approval(input);
      return send(
        `/student/calendars/events/${selected}/approve`,
        (value) => boundEvent(value, selected, checked),
        signal,
        checked,
        65536,
        55_000,
      );
    },
    reconcileEvent: async (eventId, input, signal) => {
      const selected = id(eventId),
        checked = eventCommand(input);
      // A write may have advanced the native receipt before its HTTP response
      // was lost. Refresh only that owned receipt; this never renews approval.
      const latest = await send(
        `/student/calendars/events/${selected}`,
        (value) => {
          const current = readCalendarEventView(value);
          if (
            current.id !== selected ||
            current.digest !== checked.digest ||
            current.generation < checked.generation ||
            current.revision < checked.event_revision ||
            current.calendar_revision < checked.revision
          )
            return invalid();
          return current;
        },
        signal,
      );
      const fresh = {
        ...checked,
        generation: latest.generation,
        revision: latest.calendar_revision,
        event_revision: latest.revision,
      };
      return send(
        `/student/calendars/events/${selected}/reconcile`,
        (value) => boundEvent(value, selected, fresh),
        signal,
        fresh,
        65536,
        55_000,
      );
    },
    undoEvent: (eventId, input, signal) => {
      const selected = id(eventId),
        checked = approval(input);
      return send(
        `/student/calendars/events/${selected}/undo`,
        (value) => boundEvent(value, selected, checked),
        signal,
        checked,
        65536,
        55_000,
      );
    },
  };
}
