import type { StudyPreferences } from "./student-planning";
import { type StudentTimetable, studentDateInZone } from "./student-schedule";

export function setupClassDays(timetable: StudentTimetable): number[] {
  return [
    ...new Set(
      timetable.occurrences
        .filter(
          (row) =>
            !row.cancelled &&
            Date.parse(row.end_at) > Date.parse(timetable.server_now),
        )
        .map((row) => {
          const day = studentDateInZone(row.start_at, timetable.timezone);
          return (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7;
        }),
    ),
  ].sort();
}

export function setupWakeTimes(
  preferences: StudyPreferences,
  classDays: readonly number[],
  classWake: string | null,
  otherWake: string | null,
): string[] | null | undefined {
  if (classWake === null && otherWake === null) return preferences.wake_times;
  return (
    preferences.wake_times ?? Array<string>(7).fill(preferences.wake_time)
  ).map(
    (saved, day) => (classDays.includes(day) ? classWake : otherWake) ?? saved,
  );
}
