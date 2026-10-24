import {
  type AlarmOccurrence,
  defaultAlarm,
  type StudyOpenings,
  type StudyPlan,
  type StudyPreferences,
  type StudyTask,
  studyDeadlineChoices,
} from "./student-planning";
import { type StudentTimetable, studentDateInZone } from "./student-schedule";
import { studentLocalTime } from "./student-time";

/** Suggest only a real, later opening; the Move API checks it again before approval. */
export function lateNightStudyMove(
  plan: StudyPlan | null,
  tasks: StudyTask[],
  openings: StudyOpenings | null,
  now: number,
  timezone: string,
) {
  if (
    !plan ||
    plan.needs_review ||
    !openings ||
    !openings.calendar_checked ||
    !openings.routine_confirmed ||
    openings.warnings.length ||
    openings.timezone !== timezone ||
    Date.parse(openings.expires_at) <= now ||
    studentLocalTime(now, timezone).period !== "late-night"
  )
    return null;
  const today = studentDateInZone(new Date(now).toISOString(), timezone);
  const noon = studyDeadlineChoices(`${today}T12:00`, timezone);
  if (noon.length !== 1) return null;
  const blocks = plan.blocks
    .filter(
      (block) =>
        block.status === "planned" &&
        Date.parse(block.start_at) > now &&
        studentDateInZone(block.start_at, timezone) === today &&
        studentLocalTime(Date.parse(block.start_at), timezone).hour < 12,
    )
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
  for (const block of blocks) {
    const task = tasks.find(
      (row) => row.id === block.task_id && row.status === "active",
    );
    if (!task) continue;
    for (const gap of [...openings.openings].sort(
      (a, b) => Date.parse(a.start_at) - Date.parse(b.start_at),
    )) {
      const start = Math.max(
        Date.parse(gap.start_at),
        Date.parse(noon[0]),
        Date.parse(block.end_at),
      );
      const end = start + block.minutes * 60000;
      if (
        end > Date.parse(gap.end_at) ||
        studentDateInZone(new Date(start).toISOString(), timezone) !== today ||
        studentDateInZone(new Date(end - 1).toISOString(), timezone) !==
          today ||
        (task.due_at && end > Date.parse(task.due_at))
      )
        continue;
      return { block, start_at: new Date(start).toISOString() };
    }
  }
  return null;
}

/** Actual timetable attendance drives wake proposals; a task deadline never does. */
export function earlyClassAlarm(
  preferences: StudyPreferences,
  timetable: StudentTimetable,
  current: AlarmOccurrence | undefined,
  day: string,
  now: number,
) {
  const alarm = preferences.alarm ?? defaultAlarm;
  if (
    preferences.revision < 1 ||
    !alarm.enabled ||
    alarm.smart ||
    alarm.skip_date === day ||
    !current?.enabled ||
    current.day !== day ||
    !current.scheduled_at ||
    timetable.warnings.length ||
    timetable.timezone !== preferences.timezone
  )
    return null;
  const first = timetable.occurrences
    .filter(
      (row) =>
        !row.cancelled &&
        studentDateInZone(row.start_at, preferences.timezone) === day &&
        Date.parse(row.start_at) > now,
    )
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at))[0];
  if (!first || first.walk_minutes === null) return null;
  const start =
    Date.parse(first.start_at) -
    (alarm.prep_minutes + first.walk_minutes) * 60000;
  if (
    start <= now ||
    start >= Date.parse(current.scheduled_at) ||
    studentDateInZone(new Date(start).toISOString(), preferences.timezone) !==
      day
  )
    return null;
  return {
    first,
    scheduled_at: new Date(start).toISOString(),
    preferences: { ...preferences, alarm: { ...alarm, smart: true } },
  };
}
