import { selectAvailabilityOptions } from "./student-availability-options";
import type { AvailabilitySnapshot } from "./student-calendar-api";

export type MeetingCheck = "clear" | "conflict" | "unchecked" | "expired";

/** A missing/partial source is never evidence that a proposed meeting is free. */
export function meetingCheck(
  snapshot: AvailabilitySnapshot,
  startAt: string,
  durationMinutes: number,
  now = Date.now(),
): MeetingCheck {
  const start = Date.parse(startAt);
  const end = start + durationMinutes * 60_000;
  if (
    !Number.isFinite(start) ||
    !Number.isInteger(durationMinutes) ||
    durationMinutes < 1 ||
    durationMinutes > 480 ||
    start <= now ||
    !Number.isFinite(Date.parse(snapshot.expires_at)) ||
    Date.parse(snapshot.expires_at) <= now
  )
    return "expired";
  const reservedStart = start - 10 * 60_000;
  const reservedEnd = end + 10 * 60_000;
  const contains = (window: { start_at: string; end_at: string }) =>
    Date.parse(window.start_at) <= reservedStart &&
    Date.parse(window.end_at) >= reservedEnd;
  if (
    snapshot.result.confirmed_openings.some(
      (window) =>
        contains(window) &&
        !window.reasons.length &&
        !window.unchecked_source_ids.length &&
        !window.unknown_travel_record_ids.length &&
        !window.displaced_study_block_ids.length,
    )
  )
    return "clear";
  if (
    snapshot.result.busy.some(
      (window) =>
        Date.parse(window.start_at) < reservedEnd &&
        reservedStart < Date.parse(window.end_at),
    ) ||
    snapshot.input.study_blocks.some(
      (window) =>
        Date.parse(window.start_at) < reservedEnd &&
        reservedStart < Date.parse(window.end_at),
    )
  )
    return "conflict";
  return "unchecked";
}

export function meetingAlternatives(
  snapshot: AvailabilitySnapshot,
  duration: number,
  now = Date.now(),
) {
  if (Date.parse(snapshot.expires_at) <= now) return [];
  return selectAvailabilityOptions(snapshot.result, duration, now);
}

export function meetingTimeLabel(
  startAt: string,
  endAt: string,
  timezone: string,
) {
  const date = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    weekday: "short",
    month: "short",
    day: "numeric",
  }).format(new Date(startAt));
  const clock = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    hour: "numeric",
    minute: "2-digit",
  });
  return `${date}, ${clock.format(new Date(startAt))}–${clock.format(new Date(endAt))}`;
}
