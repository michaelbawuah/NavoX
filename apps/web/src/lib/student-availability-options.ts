import type {
  AvailabilityOpening,
  AvailabilityResult,
} from "./student-calendar-api";

const MINUTE = 60_000;
const BUFFER = 10 * MINUTE;
const ROUND_TO = 5 * MINUTE;

export interface AvailabilityOption {
  start_at: string;
  end_at: string;
  reserved_start_at: string;
  reserved_end_at: string;
  duration_minutes: number;
  buffer_before_minutes: 10;
  buffer_after_minutes: 10;
  /** Server-certified proposal only; exact replacements still need review. */
  displaced_study_block_ids?: string[];
}

/** Position choices only inside confirmed gaps; this does not recalculate availability. */
export function selectAvailabilityOptions(
  result: AvailabilityResult,
  durationMinutes: number,
  now: number,
): AvailabilityOption[] {
  if (
    !Number.isSafeInteger(durationMinutes) ||
    durationMinutes < 1 ||
    durationMinutes > 480 ||
    !Number.isFinite(now)
  )
    throw new Error("Choose a meeting duration from 1 to 480 minutes.");
  if (result.status === "needs_routine") return [];
  const duration = durationMinutes * MINUTE;
  const openings = result.confirmed_openings
    .filter(
      (opening) =>
        opening.reasons.length === 0 &&
        opening.unchecked_source_ids.length === 0 &&
        opening.unknown_travel_record_ids.length === 0 &&
        opening.displaced_study_block_ids.length === 0,
    )
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
  const choices: AvailabilityOption[] = [];
  const positionWithin = (opening: AvailabilityOpening, after = now) => {
    const first = Date.parse(opening.start_at);
    const last = Date.parse(opening.end_at);
    if (!Number.isFinite(first) || !Number.isFinite(last) || first >= last)
      return null;
    const earliest =
      Math.ceil(Math.max(first + BUFFER, after + BUFFER) / MINUTE) * MINUTE;
    const rounded = Math.ceil(earliest / ROUND_TO) * ROUND_TO;
    const start = rounded + duration + BUFFER <= last ? rounded : earliest;
    const end = start + duration;
    if (start - BUFFER < first || end + BUFFER > last) return null;
    return {
      start_at: new Date(start).toISOString(),
      end_at: new Date(end).toISOString(),
      reserved_start_at: new Date(start - BUFFER).toISOString(),
      reserved_end_at: new Date(end + BUFFER).toISOString(),
      duration_minutes: durationMinutes,
      buffer_before_minutes: 10 as const,
      buffer_after_minutes: 10 as const,
    };
  };
  // Prefer different opening windows before offering a second time in one gap.
  for (const opening of openings) {
    const choice = positionWithin(opening);
    if (!choice) continue;
    if (
      choices.some(
        (existing) =>
          Date.parse(choice.reserved_start_at) <
            Date.parse(existing.reserved_end_at) &&
          Date.parse(existing.reserved_start_at) <
            Date.parse(choice.reserved_end_at),
      )
    )
      continue;
    choices.push(choice);
    if (choices.length === 2) return choices;
  }
  if (choices.length === 1) {
    const first = choices[0];
    for (const opening of openings) {
      const choice = positionWithin(opening, Date.parse(first.reserved_end_at));
      if (choice) {
        choices.push(choice);
        break;
      }
    }
  }
  return choices;
}
