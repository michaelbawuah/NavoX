import { groupSourceReferences, type TodaySource } from "./source-references";

export interface DailyFocusItem {
  id: string;
  type: string;
  title: string;
  description: string | null;
  status: string;
  priority: number;
  score: number;
  due_at: string | null;
  created_by: string;
  sources: TodaySource[];
}

export interface DailyFocusPayload<T extends DailyFocusItem = DailyFocusItem> {
  generated_at: string;
  timezone: string;
  needs_attention: T[];
  coming_up: T[];
  renewals: T[];
  waiting_on: T[];
  completed_recently: T[];
  set_aside?: T[];
}

export const DAILY_FOCUS_STALE_AFTER_MS = 15 * 60_000;
const futureClockTolerance = 5 * 60_000;
const activeStatuses = new Set([
  "candidate",
  "confirmed",
  "attention",
  "upcoming",
]);

function timestamp(value: string | null): number {
  return value ? Date.parse(value) : Number.NaN;
}

/** Keep the server's score/date/title ranking; never re-score a person's work. */
export function selectDailyFocus<T extends DailyFocusItem>(
  payload: DailyFocusPayload<T> | null,
): T[] {
  if (!payload) return [];
  const items = [
    ...payload.needs_attention,
    ...payload.coming_up,
    ...payload.renewals,
    ...payload.waiting_on,
    ...payload.completed_recently,
    ...(payload.set_aside ?? []),
  ];
  // A newer outcome may also appear in an earlier active section. It wins.
  const excluded = new Set([
    ...payload.completed_recently.map((item) => item.id),
    ...payload.waiting_on.map((item) => item.id),
    ...(payload.set_aside ?? []).map((item) => item.id),
    ...items
      .filter((item) => !activeStatuses.has(item.status))
      .map((item) => item.id),
  ]);
  const unique = new Map<string, T>();
  for (const item of items) {
    if (!excluded.has(item.id) && !unique.has(item.id))
      unique.set(item.id, item);
  }
  const score = (item: T) => (Number.isFinite(item.score) ? item.score : 0);
  const due = (item: T) => {
    const value = timestamp(item.due_at);
    return Number.isFinite(value) ? value : Infinity;
  };
  return [...unique.values()]
    .sort((a, b) => {
      const scoreOrder = score(b) - score(a);
      if (scoreOrder !== 0) return scoreOrder;
      const aDue = due(a);
      const bDue = due(b);
      if (aDue !== bDue) return aDue < bDue ? -1 : 1;
      const aTitle = a.title.toLowerCase();
      const bTitle = b.title.toLowerCase();
      return aTitle < bTitle ? -1 : aTitle > bTitle ? 1 : 0;
    })
    .slice(0, 3);
}

export function dailyFocusFreshness(
  generatedAt: string,
  now: number,
): "current" | "stale" | "unknown" {
  const generated = timestamp(generatedAt);
  if (
    !Number.isFinite(generated) ||
    !Number.isFinite(now) ||
    generated > now + futureClockTolerance
  ) {
    return "unknown";
  }
  return now - generated > DAILY_FOCUS_STALE_AFTER_MS ? "stale" : "current";
}

function safeTimezone(timezone: string): string {
  try {
    new Intl.DateTimeFormat("en", { timeZone: timezone }).format(0);
    return timezone;
  } catch {
    return "UTC";
  }
}

export function dailyFocusDate(
  value: string | null,
  timezone: string,
): string | null {
  const parsed = timestamp(value);
  if (!Number.isFinite(parsed)) return null;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: safeTimezone(timezone),
    timeZoneName: "short",
  }).format(parsed);
}

export function dailyFocusSourceContext(item: DailyFocusItem): {
  count: number;
  label: string;
} {
  const names = new Set<string>();
  for (const source of item.sources) {
    if (
      source.provider === "google" &&
      source.source_type === "gmail_message"
    ) {
      names.add("Gmail");
    } else if (
      source.provider === "google" &&
      source.source_type === "calendar_event"
    ) {
      names.add("Google Calendar");
    } else if (source.provider === "canvas") {
      names.add("Canvas");
    } else {
      names.add("Connected app");
    }
  }
  return {
    count: groupSourceReferences(item.sources).length,
    label: names.size
      ? [...names].join(" · ")
      : item.created_by === "user"
        ? "You added this"
        : "Saved item",
  };
}

export function canPrepareDailyReply(item: DailyFocusItem): boolean {
  return item.sources.some(
    (source) =>
      source.provider === "google" &&
      source.source_type === "gmail_message" &&
      typeof source.external_resource_id === "string" &&
      source.external_resource_id.trim().length > 0,
  );
}

export type DailyFocusAction = "review" | "renewal" | "draft" | "plan";

export function dailyFocusAction<T extends DailyFocusItem>(
  item: T,
  canDraftReply: (item: T) => boolean = canPrepareDailyReply,
): DailyFocusAction {
  if (item.status === "candidate") return "review";
  if (item.type === "renewal") return "renewal";
  return canDraftReply(item) ? "draft" : "plan";
}

/** These are recorded completions, not a claim that the assistant did the work. */
export function dailyFocusCompletedCount(
  payload: DailyFocusPayload | null,
): number {
  return new Set(
    (payload?.completed_recently ?? [])
      .filter((item) => item.status === "completed")
      .map((item) => item.id),
  ).size;
}
