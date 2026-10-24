import type { StudyBlock, StudyPlan, StudyTask } from "./student-planning";
import type { StudentTimetable } from "./student-schedule";
import { studentClock, studentDateInZone } from "./student-schedule";
export interface HomeItem {
  id: string;
  title: string;
  type: string;
  status: string;
  due_at: string | null;
  sources: {
    provider?: string;
    source_type?: string;
    connection_id?: string;
    external_resource_id?: string | null;
  }[];
}
export function readHomeItems(value: unknown): HomeItem[] {
  if (!value || typeof value !== "object") throw Error("Invalid day");
  const payload = value as Record<string, unknown>;
  const seen = new Set<string>();
  const rows: HomeItem[] = [];
  for (const key of [
    "completed_recently",
    "needs_attention",
    "coming_up",
    "waiting_on",
  ]) {
    const group = payload[key];
    if (!Array.isArray(group)) throw Error("Invalid day");
    for (const entry of group) {
      if (!entry || typeof entry !== "object") throw Error("Invalid item");
      const v = entry as Record<string, unknown>;
      if (
        typeof v.id !== "string" ||
        typeof v.title !== "string" ||
        typeof v.type !== "string" ||
        typeof v.status !== "string" ||
        !(v.due_at === null || typeof v.due_at === "string") ||
        !Array.isArray(v.sources)
      )
        throw Error("Invalid item");
      if (seen.has(v.id)) continue;
      seen.add(v.id);
      if (
        key === "completed_recently" ||
        [
          "candidate",
          "completed",
          "cancelled",
          "dismissed",
          "waiting",
          "waiting_on_external",
        ].includes(v.status) ||
        v.type === "renewal"
      )
        continue;
      rows.push(v as unknown as HomeItem);
    }
  }
  return rows;
}
export interface DayRow {
  id: string;
  title: string;
  start: string;
  end: string | null;
  courseId: string | null;
  detail: string;
  href: string;
  walkMinutes: number | null;
  study?: StudyBlock;
}
export function dayRows(
  timetable: StudentTimetable | null,
  plan: StudyPlan | null,
  tasks: StudyTask[],
  items: HomeItem[],
  now: number,
  zone: string,
): DayRow[] {
  const rows: DayRow[] = [];
  for (const c of timetable?.occurrences ?? [])
    if (!c.cancelled)
      rows.push({
        id: c.id,
        title: `${c.course_code || c.course_name} · ${c.kind}`,
        start: c.start_at,
        end: c.end_at,
        courseId: c.course_id,
        detail: [c.building, c.room].filter(Boolean).join(" · "),
        href: "/classes",
        walkMinutes: c.walk_minutes,
      });
  for (const c of timetable?.fixed_occurrences ?? [])
    rows.push({
      id: c.id,
      title: c.title,
      start: c.start_at,
      end: c.end_at,
      courseId: null,
      detail: "Shift",
      href: "/classes",
      walkMinutes: null,
    });
  for (const b of plan?.blocks ?? [])
    if (b.status !== "superseded")
      rows.push({
        id: b.id,
        title: b.title,
        start: b.start_at,
        end: b.end_at,
        courseId: tasks.find((t) => t.id === b.task_id)?.course_id ?? null,
        detail:
          b.status === "needs_review" || plan?.needs_review
            ? "Review study time"
            : "Study",
        href: "/planner",
        walkMinutes: null,
        study: b,
      });
  for (const c of items)
    if (c.type === "meeting" && c.due_at)
      rows.push({
        id: c.id,
        title: c.title,
        start: c.due_at,
        end: null,
        courseId: null,
        detail: "Meeting",
        href: "/planner",
        walkMinutes: null,
      });
  const day = studentDateInZone(new Date(now).toISOString(), zone);
  return rows
    .filter(
      (r) =>
        Number.isFinite(Date.parse(r.start)) &&
        studentDateInZone(r.start, zone) === day,
    )
    .sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
}
export function dueSoon(tasks: StudyTask[], items: HomeItem[], now: number) {
  const taskSources = new Set(
    tasks.map((t) => t.source_item_id).filter(Boolean),
  );
  return [
    ...tasks
      .filter((t) => t.status === "active")
      .map((t) => ({
        id: t.id,
        title: t.title,
        due: t.due_at,
        courseId: t.course_id,
      })),
    ...items
      .filter(
        (i) => ["task", "deadline"].includes(i.type) && !taskSources.has(i.id),
      )
      .map((i) => ({
        id: i.id,
        title: i.title,
        due: i.due_at,
        courseId: null,
      })),
  ]
    .filter(
      (
        i,
      ): i is {
        id: string;
        title: string;
        due: string;
        courseId: string | null;
      } =>
        i.due !== null &&
        Date.parse(i.due) >= now &&
        Date.parse(i.due) <= now + 3 * 86400000,
    )
    .sort((a, b) => Date.parse(a.due) - Date.parse(b.due))
    .slice(0, 3);
}
export function relativeTime(value: string, now: number, zone: string): string {
  const at = Date.parse(value);
  if (!Number.isFinite(at)) return "";
  const minutes = Math.ceil((at - now) / 60000);
  if (minutes >= 0 && minutes <= 60)
    return minutes === 0 ? "Now" : `in ${minutes} min`;
  const day = studentDateInZone(value, zone),
    today = studentDateInZone(new Date(now).toISOString(), zone);
  if (day === today) return `today at ${studentClock(value, zone)}`;
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
    timeZone: zone,
  }).format(new Date(at));
}
