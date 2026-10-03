interface CategorizedItem {
  id: string;
  status: string;
  type: string;
}

export const todayFilters = [
  "Needs Attention",
  "Coming Up",
  "Waiting On",
  "Money / Renewals",
] as const;
export type TodayFilter = (typeof todayFilters)[number];

export function todaySections<T extends CategorizedItem>(
  payload: {
    needs_attention: T[];
    coming_up: T[];
    waiting_on: T[];
    renewals: T[];
  } | null,
): Record<TodayFilter, T[]> {
  const groups: Record<TodayFilter, T[]> = {
    "Needs Attention": [],
    "Coming Up": [],
    "Waiting On": [],
    "Money / Renewals": [],
  };
  if (!payload) return groups;
  const urgent = new Set(payload.needs_attention.map((item) => item.id));
  const seen = new Set<string>();
  for (const item of [
    ...payload.needs_attention,
    ...payload.coming_up,
    ...payload.waiting_on,
    ...payload.renewals,
  ]) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    const section =
      item.status === "waiting" || item.status === "waiting_on_external"
        ? "Waiting On"
        : urgent.has(item.id)
          ? "Needs Attention"
          : item.type === "renewal"
            ? "Money / Renewals"
            : "Coming Up";
    groups[section].push(item);
  }
  return groups;
}

export const todayConsumerSections = [
  "Needs your attention",
  "Coming up",
  "Updates",
] as const;
export type TodayConsumerSection = (typeof todayConsumerSections)[number];

/** Show saved facts once, with resolved items kept out of the action list. */
export function consumerTodaySections<
  T extends CategorizedItem & { due_at: string | null },
>(
  payload: {
    needs_attention: T[];
    coming_up: T[];
    waiting_on: T[];
    renewals: T[];
    completed_recently: T[];
  } | null,
  now: number,
): Record<TodayConsumerSection, T[]> {
  const groups: Record<TodayConsumerSection, T[]> = {
    "Needs your attention": [],
    "Coming up": [],
    Updates: [],
  };
  if (!payload) return groups;
  const resolved = new Set(payload.completed_recently.map((item) => item.id));
  const attention = new Set(payload.needs_attention.map((item) => item.id));
  const seen = new Set<string>();
  for (const item of [
    ...payload.completed_recently,
    ...payload.needs_attention,
    ...payload.coming_up,
    ...payload.waiting_on,
    ...payload.renewals,
  ]) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    if (
      resolved.has(item.id) ||
      [
        "completed",
        "cancelled",
        "dismissed",
        "waiting",
        "waiting_on_external",
      ].includes(item.status)
    ) {
      groups.Updates.push(item);
    } else if (
      attention.has(item.id) ||
      (item.type === "renewal" &&
        item.due_at !== null &&
        Number.isFinite(Date.parse(item.due_at)) &&
        Date.parse(item.due_at) <= now + 48 * 3_600_000)
    ) {
      groups["Needs your attention"].push(item);
    } else {
      groups["Coming up"].push(item);
    }
  }
  groups["Coming up"].sort(
    (a, b) =>
      (a.due_at ? Date.parse(a.due_at) : Infinity) -
      (b.due_at ? Date.parse(b.due_at) : Infinity),
  );
  return groups;
}
