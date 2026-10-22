/** Private calendar-purpose transport. Construction performs no I/O. */
import { googleAuthorizationUrl } from "./connection-management";
import {
  readStudyPreferences,
  type StudyPreferences,
} from "./student-planning";

export const CALENDAR_CONSENT_VERSION = "calendar-availability.v1";
export const CALENDAR_READ_SCOPES = [
  "openid",
  "https://www.googleapis.com/auth/userinfo.email",
  "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
  "https://www.googleapis.com/auth/calendar.events.freebusy",
] as const;
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DATE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;
const MAX_REVISION = 2147483647;
const MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024;
export type CalendarProviderId = "google" | "outlook" | "icloud";
export type CoverageState =
  | "complete"
  | "partial"
  | "stale"
  | "unavailable"
  | "revoked";
export interface CalendarGrant {
  id: string;
  provider: "google";
  connection_id: string | null;
  account_label: string | null;
  status: "pending" | "connected" | "unavailable" | "revoked";
  revision: number;
}
export interface CalendarView {
  generation: number;
  revision: number;
  consent_version: typeof CALENDAR_CONSENT_VERSION;
  providers: {
    id: CalendarProviderId;
    availability: "available" | "setup_pending" | "unavailable";
  }[];
  grants: CalendarGrant[];
  changes_applied: false;
}
export interface CalendarCommand {
  generation: number;
  revision: number;
  request_id: string;
}
export interface CalendarStartInput extends CalendarCommand {
  consent_version: typeof CALENDAR_CONSENT_VERSION;
  confirmed: true;
  connection_id?: string | null;
}
export interface CalendarAuthorization {
  authorization_url: string;
  requested_scopes: string[];
  generation: number;
  revision: number;
  grant_id: string;
}
export interface AvailabilityPreviewInput {
  start_at: string;
  end_at: string;
  duration_minutes: number;
  preferences_revision: number;
  expected_timezone: string;
  routine_confirmed: boolean;
  travel_assumption: "unknown" | "zero";
  include_short_slots: boolean;
}
export interface AvailabilityTimeInterval {
  start_at: string;
  end_at: string;
}
export interface AvailabilityOwnedFact {
  user_id: string;
  workspace_id: string;
}
export interface AvailabilityCoverage
  extends AvailabilityTimeInterval,
    AvailabilityOwnedFact {
  source_id: string;
  state: CoverageState;
  observed_at: string;
  fresh_until: string;
  revision: string;
}
export interface AvailabilityBusy
  extends AvailabilityTimeInterval,
    AvailabilityOwnedFact {
  id: string;
  source_id: string;
  kind: "class" | "meeting" | "shift" | "fixed" | "travel" | "all_day";
  travel_required: boolean;
  travel_before_minutes: number | null;
  travel_after_minutes: number | null;
}
export interface AvailabilityStudyBlock
  extends AvailabilityTimeInterval,
    AvailabilityOwnedFact {
  id: string;
  movable: boolean;
  calendar_backed: boolean;
  needs_review: boolean;
}
export interface AvailabilityUncertainty
  extends AvailabilityTimeInterval,
    AvailabilityOwnedFact {
  source_id: string;
  reason: "projection_warning" | "unresolved_time" | "unavailable_source";
}
export interface AvailabilityInput
  extends AvailabilityTimeInterval,
    AvailabilityOwnedFact {
  timezone: string;
  now: string;
  preferences: Omit<StudyPreferences, "revision" | "timezone">;
  routine_confirmed: boolean;
  required_source_ids: string[];
  coverage: AvailabilityCoverage[];
  busy: AvailabilityBusy[];
  study_blocks: AvailabilityStudyBlock[];
  uncertainties: AvailabilityUncertainty[];
  duration_minutes: number | null;
  include_short_slots: boolean;
}
export type AvailabilityReason =
  | "coverage_unknown"
  | "routine_unconfirmed"
  | "travel_unknown"
  | "movable_study";
export interface AvailabilityOpening extends AvailabilityTimeInterval {
  minutes: number;
  reasons: AvailabilityReason[];
  unchecked_source_ids: string[];
  unknown_travel_record_ids: string[];
  displaced_study_block_ids: string[];
}
export interface AvailabilityWarning {
  code:
    | "coverage_missing"
    | "coverage_partial"
    | "coverage_stale"
    | "coverage_unavailable"
    | "coverage_revoked"
    | "coverage_future"
    | "coverage_outside_window"
    | "projection_uncertainty"
    | "routine_unconfirmed"
    | "routine_clock_change"
    | "never_schedule_clock_change"
    | "travel_unknown"
    | "movable_study";
  source_id: string | null;
  record_id: string | null;
  start_at: string | null;
  end_at: string | null;
}
export interface AvailabilityResult {
  timezone: string;
  checked_at: string;
  scope: "provided_owned_sources_only";
  status: "complete" | "partial" | "needs_routine";
  confirmed_openings: AvailabilityOpening[];
  provisional_openings: AvailabilityOpening[];
  busy: AvailabilityTimeInterval[];
  warnings: AvailabilityWarning[];
  changes_applied: false;
}
export interface AvailabilitySnapshot {
  input: AvailabilityInput;
  result: AvailabilityResult;
  checked_at: string;
  expires_at: string;
  revision: {
    student_revision: string;
    planning_revision: number;
    preferences_revision: number;
    availability_generation: number;
    availability_revision: number;
  };
  source_summary: {
    source_id: string;
    state: CoverageState;
    calendar_count: number;
  }[];
  changes_applied: false;
}
export interface StudentCalendarApi {
  account(signal: AbortSignal): Promise<string>;
  preferences(signal: AbortSignal): Promise<StudyPreferences>;
  calendars(signal: AbortSignal): Promise<CalendarView>;
  start(
    input: CalendarStartInput,
    signal: AbortSignal,
  ): Promise<CalendarAuthorization>;
  disconnect(
    grantId: string,
    input: CalendarCommand,
    signal: AbortSignal,
  ): Promise<CalendarView>;
  preview(
    input: AvailabilityPreviewInput,
    signal: AbortSignal,
  ): Promise<AvailabilitySnapshot>;
}
export class CalendarRequestError extends Error {
  constructor(
    readonly code:
      | "unavailable"
      | "account-changed"
      | "cancelled"
      | "invalid-response",
    readonly status: number | null = null,
  ) {
    super(
      code === "account-changed"
        ? "Your account or calendar access changed. Refresh before continuing."
        : code === "cancelled"
          ? "This calendar request was cancelled."
          : code === "invalid-response"
            ? "Calendar availability could not be checked. Refresh before relying on these times."
            : "Calendar access is unavailable. Review your connection and try again.",
    );
    this.name = "CalendarRequestError";
  }
}
function invalid(): never {
  throw new CalendarRequestError("invalid-response");
}
function obj(
  value: unknown,
  keys?: readonly string[],
): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  if (
    keys &&
    (Object.keys(value).length !== keys.length ||
      Object.keys(value).some((key) => !keys.includes(key)))
  )
    return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 128, min = 0): string {
  if (
    typeof value !== "string" ||
    value.length < min ||
    value.length > max ||
    [...value].some((char) => {
      const code = char.charCodeAt(0);
      return (code < 32 && ![9, 10, 13].includes(code)) || code === 127;
    })
  )
    return invalid();
  return value;
}
function id(value: unknown): string {
  const v = text(value, 36, 36);
  if (!UUID.test(v)) return invalid();
  return v.toLowerCase();
}
function opaque(value: unknown): string {
  const v = text(value, 128, 1);
  if (
    /\s/.test(v) ||
    [...v].some((char) => {
      const code = char.charCodeAt(0);
      return code < 32 || (code >= 127 && code <= 159);
    })
  )
    return invalid();
  return v;
}
function num(value: unknown, min = 0, max = MAX_REVISION): number {
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
function en<T extends string>(value: unknown, values: readonly T[]): T {
  if (!values.includes(value as T)) return invalid();
  return value as T;
}
function list<T>(
  value: unknown,
  read: (item: unknown) => T,
  max: number,
  min = 0,
): T[] {
  if (!Array.isArray(value) || value.length > max || value.length < min)
    return invalid();
  return value.map(read);
}
function unique<T>(values: T[], key: (item: T) => string): T[] {
  if (new Set(values.map(key)).size !== values.length) return invalid();
  return values;
}
function nullable<T>(value: unknown, read: (item: unknown) => T): T | null {
  return value === null ? null : read(value);
}
function instant(value: unknown): string {
  const v = text(value, 40, 20),
    parsed = Date.parse(v),
    year = new Date(parsed).getUTCFullYear();
  if (!DATE.test(v) || !Number.isFinite(parsed) || year < 2 || year > 9998)
    return invalid();
  return v;
}
function zone(value: unknown): string {
  const v = text(value, 64, 1);
  try {
    new Intl.DateTimeFormat("en", { timeZone: v }).format();
  } catch {
    return invalid();
  }
  return v;
}
function interval(v: Record<string, unknown>): AvailabilityTimeInterval {
  const result = { start_at: instant(v.start_at), end_at: instant(v.end_at) };
  const elapsed = Date.parse(result.end_at) - Date.parse(result.start_at);
  if (elapsed <= 0 || elapsed > 366 * 86400_000) return invalid();
  return result;
}
function owner(
  v: Record<string, unknown>,
  accountId: string,
): AvailabilityOwnedFact {
  const result = { user_id: id(v.user_id), workspace_id: id(v.workspace_id) };
  if (`${result.workspace_id}:${result.user_id}` !== accountId)
    throw new CalendarRequestError("account-changed");
  return result;
}
function clock(value: unknown): string {
  const v = text(value, 5, 5);
  if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(v)) return invalid();
  return v;
}
function readWindow(value: unknown) {
  const v = obj(value, ["weekdays", "start_time", "end_time"]),
    weekdays = list(v.weekdays, (day) => num(day, 0, 6), 7, 1);
  if (new Set(weekdays).size !== weekdays.length) return invalid();
  const start_time = clock(v.start_time),
    end_time = clock(v.end_time);
  if (start_time === end_time) return invalid();
  return { weekdays, start_time, end_time };
}
function preferences(value: unknown): AvailabilityInput["preferences"] {
  const v = obj(value, [
    "wake_time",
    "sleep_time",
    "focus_windows",
    "never_schedule",
    "max_study_minutes",
    "break_minutes",
  ]);
  const result = {
    wake_time: clock(v.wake_time),
    sleep_time: clock(v.sleep_time),
    focus_windows: list(v.focus_windows, readWindow, 14),
    never_schedule: list(v.never_schedule, readWindow, 28),
    max_study_minutes: num(v.max_study_minutes, 15, 90),
    break_minutes: num(v.break_minutes, 5, 60),
  };
  if (result.wake_time === result.sleep_time) return invalid();
  return result;
}
const coverageStates = [
  "complete",
  "partial",
  "stale",
  "unavailable",
  "revoked",
] as const;
export function readCalendarView(value: unknown): CalendarView {
  const v = obj(value, [
    "generation",
    "revision",
    "consent_version",
    "providers",
    "grants",
    "changes_applied",
  ]);
  if (
    v.consent_version !== CALENDAR_CONSENT_VERSION ||
    v.changes_applied !== false
  )
    return invalid();
  const providers = unique(
    list(
      v.providers,
      (item) => {
        const p = obj(item, ["id", "availability"]);
        return {
          id: en(p.id, ["google", "outlook", "icloud"]),
          availability: en(p.availability, [
            "available",
            "setup_pending",
            "unavailable",
          ]),
        };
      },
      3,
      3,
    ),
    (p) => p.id,
  );
  if (
    providers.some((p) => p.id !== "google" && p.availability !== "unavailable")
  )
    return invalid();
  const grants = unique(
    list(
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
        if (g.provider !== "google") return invalid();
        return {
          id: id(g.id),
          provider: "google" as const,
          connection_id: nullable(g.connection_id, id),
          account_label: nullable(g.account_label, (label) => text(label, 320)),
          status: en(g.status, [
            "pending",
            "connected",
            "unavailable",
            "revoked",
          ]),
          revision: num(g.revision),
        };
      },
      200,
    ),
    (g) => g.id,
  );
  if (grants.some((g) => g.status === "connected" && g.connection_id === null))
    return invalid();
  return {
    generation: num(v.generation),
    revision: num(v.revision),
    consent_version: CALENDAR_CONSENT_VERSION,
    providers,
    grants,
    changes_applied: false,
  };
}
function command(value: CalendarCommand): CalendarCommand {
  const v = obj(value, ["generation", "revision", "request_id"]);
  return {
    generation: num(v.generation),
    revision: num(v.revision),
    request_id: id(v.request_id),
  };
}
function startInput(value: CalendarStartInput): CalendarStartInput {
  const v = obj(value);
  obj(value, [
    "generation",
    "revision",
    "request_id",
    "consent_version",
    "confirmed",
    ...(v.connection_id === undefined ? [] : ["connection_id"]),
  ]);
  if (v.consent_version !== CALENDAR_CONSENT_VERSION || v.confirmed !== true)
    return invalid();
  return {
    ...command({
      generation: num(v.generation),
      revision: num(v.revision),
      request_id: id(v.request_id),
    }),
    consent_version: CALENDAR_CONSENT_VERSION,
    confirmed: true,
    ...(v.connection_id === undefined
      ? {}
      : { connection_id: nullable(v.connection_id, id) }),
  };
}
function sameSet(values: string[], expected: readonly string[]): boolean {
  return (
    values.length === expected.length &&
    new Set(values).size === values.length &&
    values.every((item) => expected.includes(item))
  );
}
function authorization(
  value: unknown,
  request: CalendarStartInput,
): CalendarAuthorization {
  const v = obj(value, [
      "authorization_url",
      "requested_scopes",
      "generation",
      "revision",
      "grant_id",
    ]),
    scopes = list(v.requested_scopes, (item) => text(item, 200, 1), 4, 4);
  if (!sameSet(scopes, CALENDAR_READ_SCOPES)) return invalid();
  let target: URL;
  try {
    target = new URL(
      googleAuthorizationUrl(text(v.authorization_url, 8192, 1)),
    );
  } catch {
    return invalid();
  }
  const single = (key: string) => {
    const entries = target.searchParams.getAll(key);
    if (entries.length !== 1 || !entries[0]) return invalid();
    return entries[0];
  };
  if (
    !sameSet(single("scope").split(" "), CALENDAR_READ_SCOPES) ||
    single("response_type") !== "code" ||
    single("access_type") !== "offline" ||
    single("prompt") !== "consent" ||
    single("code_challenge_method") !== "S256" ||
    single("include_granted_scopes") !== "true" ||
    !/^[A-Za-z0-9_-]{43,128}$/.test(single("code_challenge")) ||
    single("state").length > 1024 ||
    single("client_id").length > 1024 ||
    single("redirect_uri").length > 2048 ||
    num(v.generation) !== request.generation ||
    num(v.revision) <= request.revision
  )
    return invalid();
  return {
    authorization_url: target.toString(),
    requested_scopes: scopes,
    generation: num(v.generation),
    revision: num(v.revision),
    grant_id: id(v.grant_id),
  };
}
function previewInput(
  value: AvailabilityPreviewInput,
): AvailabilityPreviewInput {
  const v = obj(value, [
      "start_at",
      "end_at",
      "duration_minutes",
      "preferences_revision",
      "expected_timezone",
      "routine_confirmed",
      "travel_assumption",
      "include_short_slots",
    ]),
    span = interval(v);
  if (Date.parse(span.end_at) - Date.parse(span.start_at) > 15 * 86400_000)
    return invalid();
  return {
    ...span,
    duration_minutes: num(v.duration_minutes, 1, 480),
    preferences_revision: num(v.preferences_revision),
    expected_timezone: zone(v.expected_timezone),
    routine_confirmed: bool(v.routine_confirmed),
    travel_assumption: en(v.travel_assumption, ["unknown", "zero"]),
    include_short_slots: bool(v.include_short_slots),
  };
}
function readAvailabilityInput(
  value: unknown,
  accountId: string,
): AvailabilityInput {
  const v = obj(value, [
      "start_at",
      "end_at",
      "user_id",
      "workspace_id",
      "timezone",
      "now",
      "preferences",
      "routine_confirmed",
      "required_source_ids",
      "coverage",
      "busy",
      "study_blocks",
      "uncertainties",
      "duration_minutes",
      "include_short_slots",
    ]),
    owned = owner(v, accountId),
    required = unique(
      list(v.required_source_ids, opaque, 200, 1),
      (source) => source,
    );
  const fact = (item: unknown, keys: string[]) => {
    const row = obj(item, [
      "start_at",
      "end_at",
      "user_id",
      "workspace_id",
      ...keys,
    ]);
    owner(row, accountId);
    return row;
  };
  const sourced = (source: unknown) => {
    const result = opaque(source);
    if (!required.includes(result)) return invalid();
    return result;
  };
  const coverage = unique(
    list(
      v.coverage,
      (item) => {
        const c = fact(item, [
            "source_id",
            "state",
            "observed_at",
            "fresh_until",
            "revision",
          ]),
          result = {
            ...interval(c),
            ...owned,
            source_id: sourced(c.source_id),
            state: en(c.state, coverageStates),
            observed_at: instant(c.observed_at),
            fresh_until: instant(c.fresh_until),
            revision: text(c.revision, 128, 1),
          };
        if (Date.parse(result.fresh_until) <= Date.parse(result.observed_at))
          return invalid();
        return result;
      },
      200,
    ),
    (c) => c.source_id,
  );
  const busy = unique(
    list(
      v.busy,
      (item) => {
        const b = fact(item, [
            "id",
            "source_id",
            "kind",
            "travel_required",
            "travel_before_minutes",
            "travel_after_minutes",
          ]),
          result = {
            ...interval(b),
            ...owned,
            id: opaque(b.id),
            source_id: sourced(b.source_id),
            kind: en(b.kind, [
              "class",
              "meeting",
              "shift",
              "fixed",
              "travel",
              "all_day",
            ]),
            travel_required: bool(b.travel_required),
            travel_before_minutes: nullable(
              b.travel_before_minutes,
              (minutes) => num(minutes, 0, 240),
            ),
            travel_after_minutes: nullable(b.travel_after_minutes, (minutes) =>
              num(minutes, 0, 240),
            ),
          };
        if (
          result.kind === "all_day" &&
          (result.travel_required ||
            ![null, 0].includes(result.travel_before_minutes) ||
            ![null, 0].includes(result.travel_after_minutes))
        )
          return invalid();
        return result;
      },
      10_000,
    ),
    (b) => b.id,
  );
  const study_blocks = unique(
    list(
      v.study_blocks,
      (item) => {
        const s = fact(item, [
          "id",
          "movable",
          "calendar_backed",
          "needs_review",
        ]);
        return {
          ...interval(s),
          ...owned,
          id: opaque(s.id),
          movable: bool(s.movable),
          calendar_backed: bool(s.calendar_backed),
          needs_review: bool(s.needs_review),
        };
      },
      600,
    ),
    (s) => s.id,
  );
  const uncertainties = list(
    v.uncertainties,
    (item) => {
      const u = fact(item, ["source_id", "reason"]);
      return {
        ...interval(u),
        ...owned,
        source_id: sourced(u.source_id),
        reason: en(u.reason, [
          "projection_warning",
          "unresolved_time",
          "unavailable_source",
        ]),
      };
    },
    1000,
  );
  return {
    ...interval(v),
    ...owned,
    timezone: zone(v.timezone),
    now: instant(v.now),
    preferences: preferences(v.preferences),
    routine_confirmed: bool(v.routine_confirmed),
    required_source_ids: required,
    coverage,
    busy,
    study_blocks,
    uncertainties,
    duration_minutes: nullable(v.duration_minutes, (minutes) =>
      num(minutes, 1, 480),
    ),
    include_short_slots: bool(v.include_short_slots),
  };
}
function readResult(
  value: unknown,
  input: AvailabilityInput,
  checked: number,
): AvailabilityResult {
  const v = obj(value, [
    "timezone",
    "checked_at",
    "scope",
    "status",
    "confirmed_openings",
    "provisional_openings",
    "busy",
    "warnings",
    "changes_applied",
  ]);
  if (
    v.scope !== "provided_owned_sources_only" ||
    v.changes_applied !== false ||
    v.timezone !== input.timezone ||
    Date.parse(instant(v.checked_at)) !== checked
  )
    return invalid();
  const busyIds = new Set(input.busy.map((item) => item.id));
  const studyIds = new Set(input.study_blocks.map((item) => item.id));
  const reasons = [
    "coverage_unknown",
    "routine_unconfirmed",
    "travel_unknown",
    "movable_study",
  ] as const;
  const opening = (value: unknown, confirmed: boolean): AvailabilityOpening => {
    const o = obj(value, [
        "start_at",
        "end_at",
        "minutes",
        "reasons",
        "unchecked_source_ids",
        "unknown_travel_record_ids",
        "displaced_study_block_ids",
      ]),
      span = interval(o);
    if (
      Date.parse(span.start_at) <
        Math.max(Date.parse(input.start_at), checked) ||
      Date.parse(span.end_at) > Date.parse(input.end_at)
    )
      return invalid();
    const reasonList = unique(
      list(o.reasons, (reason) => en(reason, reasons), 4),
      (reason) => reason,
    );
    const unchecked = unique(
        list(o.unchecked_source_ids, opaque, 200),
        (source) => source,
      ),
      travel = unique(
        list(o.unknown_travel_record_ids, opaque, 10_000),
        (source) => source,
      ),
      displaced = unique(
        list(o.displaced_study_block_ids, opaque, 600),
        (source) => source,
      );
    if (
      unchecked.some((source) => !input.required_source_ids.includes(source)) ||
      travel.some((record) => !busyIds.has(record)) ||
      displaced.some((record) => !studyIds.has(record)) ||
      (confirmed
        ? reasonList.length !== 0 ||
          unchecked.length !== 0 ||
          travel.length !== 0 ||
          displaced.length !== 0
        : reasonList.length === 0)
    )
      return invalid();
    return {
      ...span,
      minutes: num(o.minutes, 1, 15 * 1440),
      reasons: reasonList,
      unchecked_source_ids: unchecked,
      unknown_travel_record_ids: travel,
      displaced_study_block_ids: displaced,
    };
  };
  const warnings = list(
    v.warnings,
    (item) => {
      const w = obj(item, [
        "code",
        "source_id",
        "record_id",
        "start_at",
        "end_at",
      ]);
      return {
        code: en(w.code, [
          "coverage_missing",
          "coverage_partial",
          "coverage_stale",
          "coverage_unavailable",
          "coverage_revoked",
          "coverage_future",
          "coverage_outside_window",
          "projection_uncertainty",
          "routine_unconfirmed",
          "routine_clock_change",
          "never_schedule_clock_change",
          "travel_unknown",
          "movable_study",
        ]),
        source_id: nullable(w.source_id, opaque),
        record_id: nullable(w.record_id, opaque),
        start_at: nullable(w.start_at, instant),
        end_at: nullable(w.end_at, instant),
      };
    },
    20_000,
  );
  const confirmed = list(
    v.confirmed_openings,
    (item) => opening(item, true),
    2000,
  );
  const provisional = list(
    v.provisional_openings,
    (item) => opening(item, false),
    2000,
  );
  const status = en(v.status, ["complete", "partial", "needs_routine"]);
  if (
    confirmed.length + provisional.length > 2000 ||
    (!input.routine_confirmed &&
      (confirmed.length !== 0 || status !== "needs_routine")) ||
    (input.routine_confirmed && status === "needs_routine") ||
    (status === "complete" &&
      (warnings.length !== 0 || provisional.length !== 0))
  )
    return invalid();
  return {
    timezone: input.timezone,
    checked_at: instant(v.checked_at),
    scope: "provided_owned_sources_only",
    status,
    confirmed_openings: confirmed,
    provisional_openings: provisional,
    busy: list(
      v.busy,
      (item) => interval(obj(item, ["start_at", "end_at"])),
      11_000,
    ),
    warnings,
    changes_applied: false,
  };
}
export function readAvailabilitySnapshot(
  value: unknown,
  accountId: string,
  request: AvailabilityPreviewInput,
  now = Date.now(),
): AvailabilitySnapshot {
  const v = obj(value, [
    "input",
    "result",
    "checked_at",
    "expires_at",
    "revision",
    "source_summary",
    "changes_applied",
  ]);
  if (v.changes_applied !== false) return invalid();
  const input = readAvailabilityInput(v.input, accountId),
    checked_at = instant(v.checked_at),
    expires_at = instant(v.expires_at),
    checked = Date.parse(checked_at),
    expires = Date.parse(expires_at);
  if (
    !Number.isFinite(now) ||
    checked > now + 5000 ||
    expires <= now ||
    expires <= checked ||
    expires - checked > 60_000 ||
    Date.parse(input.now) !== checked ||
    Date.parse(input.start_at) !== Date.parse(request.start_at) ||
    Date.parse(input.end_at) !== Date.parse(request.end_at) ||
    input.timezone !== request.expected_timezone ||
    input.duration_minutes !== request.duration_minutes ||
    input.include_short_slots !== request.include_short_slots ||
    input.routine_confirmed !==
      (request.routine_confirmed && request.preferences_revision > 0)
  )
    return invalid();
  if (
    input.coverage.some(
      (c) =>
        Date.parse(c.observed_at) > checked ||
        (c.state === "complete" && Date.parse(c.fresh_until) < expires),
    )
  )
    return invalid();
  const r = obj(v.revision, [
      "student_revision",
      "planning_revision",
      "preferences_revision",
      "availability_generation",
      "availability_revision",
    ]),
    student_revision = text(r.student_revision, 64, 64);
  if (
    !/^[a-f0-9]{64}$/.test(student_revision) ||
    num(r.preferences_revision) !== request.preferences_revision
  )
    return invalid();
  const source_summary = unique(
    list(
      v.source_summary,
      (item) => {
        const s = obj(item, ["source_id", "state", "calendar_count"]),
          source_id = opaque(s.source_id);
        if (!input.required_source_ids.includes(source_id)) return invalid();
        return {
          source_id,
          state: en(s.state, coverageStates),
          calendar_count: num(s.calendar_count, 0, 1000),
        };
      },
      200,
    ),
    (s) => s.source_id,
  );
  return {
    input,
    result: readResult(v.result, input, checked),
    checked_at,
    expires_at,
    revision: {
      student_revision,
      planning_revision: num(r.planning_revision),
      preferences_revision: num(r.preferences_revision),
      availability_generation: num(r.availability_generation),
      availability_revision: num(r.availability_revision),
    },
    source_summary,
    changes_applied: false,
  };
}
function abortable<T>(pending: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const cancel = () => reject(new CalendarRequestError("cancelled"));
    if (signal.aborted) cancel();
    else signal.addEventListener("abort", cancel, { once: true });
    void pending.then(
      (value) => {
        signal.removeEventListener("abort", cancel);
        if (signal.aborted) cancel();
        else resolve(value);
      },
      () => {
        signal.removeEventListener("abort", cancel);
        reject(
          new CalendarRequestError(
            signal.aborted ? "cancelled" : "unavailable",
          ),
        );
      },
    );
  });
}
export function createStudentCalendarApi(
  accountId: string,
  options: {
    fetch?: typeof globalThis.fetch;
    baseUrl?: string;
    now?: () => number;
  } = {},
): StudentCalendarApi {
  const parts = accountId.split(":");
  if (parts.length !== 2 || `${id(parts[0])}:${id(parts[1])}` !== accountId)
    throw new CalendarRequestError("account-changed");
  const base = (
    options.baseUrl ??
    process.env.NEXT_PUBLIC_API_BASE_URL ??
    "http://localhost:8000/api/v1"
  ).replace(/\/$/, "");
  const send = async <T>(
    path: string,
    parse: (value: unknown) => T,
    signal: AbortSignal,
    payload?: unknown,
    limit = 65536,
    timeoutMs = 20_000,
  ): Promise<T> => {
    const timeout = new AbortController(),
      timer = setTimeout(() => timeout.abort(), timeoutMs),
      combined = AbortSignal.any([signal, timeout.signal]);
    let response: Response | null = null,
      reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    try {
      if (combined.aborted) throw new CalendarRequestError("cancelled");
      response = await abortable<Response>(
        (options.fetch ?? globalThis.fetch)(`${base}${path}`, {
          method: payload === undefined ? "GET" : "POST",
          credentials: "include",
          cache: "no-store",
          redirect: "error",
          headers: {
            "X-NavoX-Account": accountId,
            ...(payload === undefined
              ? {}
              : { "Content-Type": "application/json" }),
          },
          ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
          signal: combined,
        }).then((received) => {
          if (combined.aborted) void received.body?.cancel().catch(() => {});
          return received;
        }),
        combined,
      );
      if (response.status === 401 || response.status === 409)
        throw new CalendarRequestError("account-changed", response.status);
      if (!response.ok)
        throw new CalendarRequestError("unavailable", response.status);
      if (
        response.headers
          .get("content-type")
          ?.split(";", 1)[0]
          .trim()
          .toLowerCase() !== "application/json"
      )
        return invalid();
      const declared = response.headers.get("content-length");
      if (
        declared !== null &&
        (!/^\d+$/.test(declared) || Number(declared) > limit)
      )
        return invalid();
      if (!response.body) return invalid();
      reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let total = 0;
      for (;;) {
        const chunk = await abortable(reader.read(), combined);
        if (chunk.done) break;
        total += chunk.value.length;
        if (total > limit) return invalid();
        chunks.push(chunk.value);
      }
      const bytes = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.length;
      }
      if (combined.aborted) throw new CalendarRequestError("cancelled");
      return parse(
        JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)),
      );
    } catch (cause) {
      if (reader) void reader.cancel().catch(() => {});
      else if (response) void response.body?.cancel().catch(() => {});
      if (cause instanceof CalendarRequestError) throw cause;
      throw new CalendarRequestError(
        combined.aborted ? "cancelled" : "invalid-response",
      );
    } finally {
      clearTimeout(timer);
    }
  };
  return {
    account: (signal) =>
      send(
        "/auth/me",
        (value) => {
          const v = obj(value),
            scope = `${id(obj(v.workspace).id)}:${id(v.id)}`;
          if (scope !== accountId)
            throw new CalendarRequestError("account-changed");
          return scope;
        },
        signal,
      ),
    preferences: (signal) =>
      send(
        "/student/availability/routine",
        (value) => {
          obj(value, [
            "wake_time",
            "sleep_time",
            "focus_windows",
            "never_schedule",
            "max_study_minutes",
            "break_minutes",
            "revision",
            "timezone",
          ]);
          return readStudyPreferences(value);
        },
        signal,
      ),
    calendars: (signal) => send("/student/calendars", readCalendarView, signal),
    start: (input, signal) => {
      const checked = startInput(input);
      return send(
        "/student/calendars/google/start",
        (value) => authorization(value, checked),
        signal,
        checked,
      );
    },
    disconnect: (grantId, input, signal) =>
      send(
        `/student/calendars/${id(grantId)}/disconnect`,
        readCalendarView,
        signal,
        command(input),
      ),
    preview: (input, signal) => {
      const checked = previewInput(input);
      return send(
        "/student/availability/preview",
        (value) =>
          readAvailabilitySnapshot(
            value,
            accountId,
            checked,
            (options.now ?? Date.now)(),
          ),
        signal,
        checked,
        MAX_SNAPSHOT_BYTES,
        55_000,
      );
    },
  };
}
