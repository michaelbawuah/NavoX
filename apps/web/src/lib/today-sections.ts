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
