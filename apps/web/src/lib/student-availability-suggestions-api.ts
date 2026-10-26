/** Fresh, read-only ranked meeting options. Calendar writes use separate review. */

import type { AvailabilityOption } from "./student-availability-options";
import {
  type AvailabilityPreviewInput,
  type AvailabilitySnapshot,
  CalendarRequestError,
  createStudentCalendarTransport,
  readAvailabilityPreviewInput,
  readAvailabilitySnapshot,
  type StudentCalendarTransportOptions,
} from "./student-calendar-api";

export type MeetingPurpose =
  | "other"
  | "lunch"
  | "coffee"
  | "office_hours"
  | "project_meeting"
  | "study_session";
export type MeetingSuggestionReason =
  | "within_their_hours"
  | "right_after_commitment"
  | "right_before_commitment"
  | "lunch_time"
  | "soonest"
  | "clear_of_deadlines"
  | "moves_study_block";
export interface MeetingSuggestionsInput extends AvailabilityPreviewInput {
  purpose?: MeetingPurpose;
}
export interface MeetingSuggestion {
  start_at: string;
  end_at: string;
  minutes: number;
  reasons: MeetingSuggestionReason[];
  displaced_study_block_ids: string[];
}
export interface MeetingSuggestions {
  changes_applied: false;
  snapshot: AvailabilitySnapshot;
  selection: {
    status: "ok" | "partial_information" | "needs_routine" | "no_time";
    timezone: string;
    duration_minutes: number;
    suggestions: MeetingSuggestion[];
    alternatives_available: number;
    changes_applied: false;
  };
}
export interface StudentAvailabilitySuggestionsApi {
  suggest(
    input: MeetingSuggestionsInput,
    signal: AbortSignal,
  ): Promise<MeetingSuggestions>;
}
const purposes: MeetingPurpose[] = [
  "other",
  "lunch",
  "coffee",
  "office_hours",
  "project_meeting",
  "study_session",
];
const reasons: MeetingSuggestionReason[] = [
  "within_their_hours",
  "right_after_commitment",
  "right_before_commitment",
  "lunch_time",
  "soonest",
  "clear_of_deadlines",
  "moves_study_block",
];
const MINUTE = 60_000;
const BUFFER = 10 * MINUTE;
function invalid(): never {
  throw new CalendarRequestError("invalid-response");
}
function object(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  const result = value as Record<string, unknown>;
  if (
    Object.keys(result).length !== keys.length ||
    keys.some((key) => !(key in result))
  )
    return invalid();
  return result;
}
function timestamp(value: unknown): string {
  if (
    typeof value !== "string" ||
    value.length > 40 ||
    !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/.test(
      value,
    ) ||
    !Number.isFinite(Date.parse(value))
  )
    return invalid();
  return value;
}
export function readMeetingSuggestions(
  value: unknown,
  accountId: string,
  request: MeetingSuggestionsInput,
  now = Date.now(),
): MeetingSuggestions {
  const envelope = object(value, ["snapshot", "selection", "changes_applied"]);
  if (envelope.changes_applied !== false) return invalid();
  const snapshot = readAvailabilitySnapshot(
    envelope.snapshot,
    accountId,
    request,
    now,
  );
  const selection = object(envelope.selection, [
    "status",
    "timezone",
    "duration_minutes",
    "suggestions",
    "alternatives_available",
    "changes_applied",
  ]);
  if (
    selection.timezone !== snapshot.result.timezone ||
    selection.changes_applied !== false ||
    selection.duration_minutes !== request.duration_minutes ||
    !Number.isSafeInteger(selection.alternatives_available) ||
    (selection.alternatives_available as number) < 0 ||
    (selection.alternatives_available as number) > 5_000 ||
    !Array.isArray(selection.suggestions) ||
    selection.suggestions.length > 2
  )
    return invalid();
  const suggestions = selection.suggestions.map((value): MeetingSuggestion => {
    const item = object(value, [
      "start_at",
      "end_at",
      "minutes",
      "reasons",
      "displaced_study_block_ids",
    ]);
    const start_at = timestamp(item.start_at),
      end_at = timestamp(item.end_at);
    const start = Date.parse(start_at),
      end = Date.parse(end_at);
    if (!Array.isArray(item.displaced_study_block_ids)) return invalid();
    const displaced = item.displaced_study_block_ids;
    if (
      displaced.length > 24 ||
      displaced.some((id) => typeof id !== "string") ||
      new Set(displaced).size !== displaced.length
    )
      return invalid();
    const ordinary =
      displaced.length === 0 &&
      snapshot.result.confirmed_openings.some(
        (opening) =>
          Date.parse(opening.start_at) <= start - BUFFER &&
          end + BUFFER <= Date.parse(opening.end_at),
      );
    const affected = snapshot.input.study_blocks.filter(
      (block) =>
        Date.parse(block.start_at) -
          snapshot.input.preferences.break_minutes * MINUTE <
          end + BUFFER &&
        Date.parse(block.end_at) +
          snapshot.input.preferences.break_minutes * MINUTE >
          start - BUFFER,
    );
    const certifiedWindow =
      snapshot.input.routine_confirmed &&
      snapshot.result.status !== "needs_routine" &&
      snapshot.input.uncertainties.length === 0 &&
      snapshot.input.coverage.length ===
        snapshot.input.required_source_ids.length &&
      snapshot.input.coverage.every((source) => source.state === "complete") &&
      snapshot.result.warnings.every(
        (warning) => warning.code === "movable_study",
      ) &&
      snapshot.source_summary.every((source) => source.state === "complete") &&
      snapshot.result.provisional_openings.some(
        (opening) =>
          opening.reasons.length === 1 &&
          opening.reasons[0] === "movable_study" &&
          opening.unchecked_source_ids.length === 0 &&
          opening.unknown_travel_record_ids.length === 0 &&
          Date.parse(opening.start_at) <= start - BUFFER &&
          end + BUFFER <= Date.parse(opening.end_at),
      ) &&
      affected.every(
        (block) =>
          block.movable &&
          !block.calendar_backed &&
          !block.needs_review &&
          Date.parse(block.start_at) > now,
      ) &&
      affected.length === displaced.length &&
      affected.every((block) => displaced.includes(block.id));
    if (
      end - start !== request.duration_minutes * MINUTE ||
      item.minutes !== request.duration_minutes ||
      start - BUFFER < Date.parse(snapshot.checked_at) ||
      !Array.isArray(item.reasons) ||
      item.reasons.length > reasons.length ||
      new Set(item.reasons).size !== item.reasons.length ||
      item.reasons.some((reason) => !reasons.includes(reason)) ||
      item.reasons.includes("moves_study_block") !== displaced.length > 0 ||
      (!ordinary && !certifiedWindow)
    )
      return invalid();
    if (request.purpose === "coffee" || request.purpose === "lunch") {
      const parts = (stamp: string) => {
        const parsed = new Intl.DateTimeFormat("en-CA", {
          timeZone: snapshot.result.timezone,
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hourCycle: "h23",
        }).formatToParts(new Date(stamp));
        const get = (kind: string) =>
          parsed.find((part) => part.type === kind)?.value ?? "";
        return {
          day: `${get("year")}-${get("month")}-${get("day")}`,
          minutes: Number(get("hour")) * 60 + Number(get("minute")),
        };
      };
      const first = parts(start_at),
        last = parts(end_at);
      const [from, until] =
        request.purpose === "lunch" ? [690, 840] : [480, 1080];
      if (
        first.day !== last.day ||
        first.minutes < from ||
        last.minutes > until
      )
        return invalid();
    }
    return {
      start_at,
      end_at,
      minutes: request.duration_minutes,
      reasons: item.reasons as MeetingSuggestionReason[],
      displaced_study_block_ids: displaced as string[],
    };
  });
  if (
    suggestions.length === 2 &&
    Date.parse(suggestions[0].start_at) < Date.parse(suggestions[1].end_at) &&
    Date.parse(suggestions[1].start_at) < Date.parse(suggestions[0].end_at)
  )
    return invalid();
  const expectedStatus =
    snapshot.result.status === "needs_routine"
      ? "needs_routine"
      : snapshot.result.status === "partial"
        ? "partial_information"
        : suggestions.length
          ? "ok"
          : "no_time";
  if (
    selection.status !== expectedStatus ||
    (expectedStatus === "needs_routine" && suggestions.length)
  )
    return invalid();
  return {
    changes_applied: false,
    snapshot,
    selection: {
      status: expectedStatus,
      timezone: snapshot.result.timezone,
      duration_minutes: request.duration_minutes,
      suggestions,
      alternatives_available: selection.alternatives_available as number,
      changes_applied: false,
    },
  };
}
export function meetingOption(item: MeetingSuggestion): AvailabilityOption {
  return {
    start_at: item.start_at,
    end_at: item.end_at,
    reserved_start_at: new Date(
      Date.parse(item.start_at) - BUFFER,
    ).toISOString(),
    reserved_end_at: new Date(Date.parse(item.end_at) + BUFFER).toISOString(),
    duration_minutes: item.minutes,
    buffer_before_minutes: 10,
    buffer_after_minutes: 10,
    ...(item.displaced_study_block_ids.length
      ? { displaced_study_block_ids: [...item.displaced_study_block_ids] }
      : {}),
  };
}
export function createStudentAvailabilitySuggestionsApi(
  accountId: string,
  options: StudentCalendarTransportOptions = {},
): StudentAvailabilitySuggestionsApi {
  const send = createStudentCalendarTransport(accountId, options);
  return {
    suggest(input, signal) {
      const { purpose: requestedPurpose, ...preview } = input;
      const request = readAvailabilityPreviewInput(preview);
      const purpose = requestedPurpose ?? "other";
      if (!purposes.includes(purpose))
        return Promise.reject(new CalendarRequestError("invalid-response"));
      const checked = { ...request, purpose };
      return send(
        "/student/availability/suggestions",
        (value) =>
          readMeetingSuggestions(
            value,
            accountId,
            checked,
            options.now?.() ?? Date.now(),
          ),
        signal,
        checked,
        9 * 1024 * 1024,
        90_000,
      );
    },
  };
}
