import type {
  AssistantBlock,
  AssistantMeetingBriefing,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import { blocksFromToday, type TodayQueryResult } from "./today";
import { clampText, isRecord, isUuid } from "./validate";

function unreadable(): never {
  throw new AssistantError(
    "unavailable",
    "Meeting preparation returned information this runtime could not verify.",
  );
}

function text(value: unknown, max: number): string {
  if (typeof value !== "string" || !value.trim()) unreadable();
  return clampText(value, max);
}

/** The existing SPEC-002 proactive service owns the facts; this only bounds
 * its authenticated wire response before the assistant stores or displays it.
 */
export function parseMeetingPrep(
  payload: unknown,
): AssistantMeetingBriefing | null {
  if (payload === null) return null;
  if (!isRecord(payload)) unreadable();
  if (!isUuid(payload.commitment_id)) unreadable();
  if (
    !Number.isInteger(payload.minutes_until) ||
    (payload.minutes_until as number) < 0 ||
    (payload.minutes_until as number) > 525_600
  )
    unreadable();
  if (
    !Array.isArray(payload.related_commitments) ||
    payload.related_commitments.length > 20
  )
    unreadable();
  if (!Array.isArray(payload.prep_points) || payload.prep_points.length > 12)
    unreadable();
  const startsAt = text(payload.starts_at, 64);
  if (!Number.isFinite(Date.parse(startsAt))) unreadable();
  const description = payload.description;
  if (description !== null && typeof description !== "string") unreadable();
  return {
    commitment_id: payload.commitment_id,
    title: text(payload.title, 300),
    starts_at: startsAt,
    minutes_until: payload.minutes_until as number,
    description: description === null ? null : clampText(description, 1000),
    related_commitments: payload.related_commitments.map((entry) => {
      if (!isRecord(entry) || !isUuid(entry.id)) unreadable();
      return {
        id: entry.id,
        title: text(entry.title, 300),
        status: text(entry.status, 64),
      };
    }),
    prep_points: payload.prep_points.map((point) => text(point, 400)),
  };
}

/** Refuse a cross-snapshot mismatch; never attach another meeting's details to
 * a Today answer just because both endpoints returned plausible payloads.
 */
export function meetingBlocks(
  today: TodayQueryResult,
  meeting: AssistantMeetingBriefing | null,
): AssistantBlock[] {
  if (today.intent !== "meeting_prep") unreadable();
  if (meeting === null && today.items.length === 0)
    return blocksFromToday(today);
  if (
    meeting === null ||
    today.items.length !== 1 ||
    today.items[0]?.id !== meeting.commitment_id ||
    today.items[0]?.type !== "meeting" ||
    today.items[0]?.title !== meeting.title ||
    Date.parse(today.items[0]?.due_at ?? "") !== Date.parse(meeting.starts_at)
  ) {
    throw new AssistantError(
      "unavailable",
      "The meeting changed while it was being prepared. Please ask again.",
    );
  }
  return [
    ...blocksFromToday(today).filter((block) => block.kind !== "DETAILS"),
    { kind: "MEETING_BRIEFING", meeting },
  ];
}
