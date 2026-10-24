import { readStudyBlock, readStudyTask } from "./student-planning";

export type StudyWindowIntent = {
  accountKey: string;
  start_at: string;
  end_at: string;
  timezone: string;
  task_id: string | null;
  created_at: string;
};
export const studyWindowKey = (owner: string) =>
  `navox:study-window:v1:${owner}`;
export function consumeStudyWindow(
  storage: Pick<Storage, "getItem" | "removeItem">,
  owner: string,
  now = Date.now(),
): StudyWindowIntent | null {
  const key = studyWindowKey(owner),
    raw = storage.getItem(key);
  if (!raw) return null;
  storage.removeItem(key);
  try {
    const value = JSON.parse(raw) as StudyWindowIntent;
    if (
      value.accountKey !== owner ||
      typeof value.timezone !== "string" ||
      (value.task_id !== null &&
        (typeof value.task_id !== "string" ||
          !/^[a-f\d-]{36}$/i.test(value.task_id))) ||
      !Number.isFinite(Date.parse(value.start_at)) ||
      !Number.isFinite(Date.parse(value.end_at)) ||
      !Number.isFinite(Date.parse(value.created_at)) ||
      Date.parse(value.end_at) <= now ||
      Date.parse(value.end_at) <= Date.parse(value.start_at) ||
      Date.parse(value.end_at) - Date.parse(value.start_at) > 86400000 ||
      now - Date.parse(value.created_at) > 600000 ||
      Date.parse(value.created_at) > now + 60000
    )
      return null;
    new Intl.DateTimeFormat("en", { timeZone: value.timezone });
    return value;
  } catch {
    return null;
  }
}

export function readStudyPlacement(value: unknown) {
  if (!value || typeof value !== "object")
    throw Error("This study review could not be checked.");
  const row = value as Record<string, unknown>;
  if (
    typeof row.preview_hash !== "string" ||
    !/^[a-f0-9]{64}$/.test(row.preview_hash) ||
    row.other_blocks_change !== false ||
    row.alarms_change !== false ||
    row.calendar_events_change !== false ||
    typeof row.remaining_unplanned_minutes !== "number" ||
    !Number.isInteger(row.remaining_unplanned_minutes) ||
    row.remaining_unplanned_minutes < 0 ||
    typeof row.recovery_required !== "boolean" ||
    !Array.isArray(row.preserved_blocks) ||
    !Array.isArray(row.unplanned_tasks) ||
    row.unplanned_tasks.some(
      (item) =>
        !item ||
        typeof item !== "object" ||
        typeof item.task_id !== "string" ||
        typeof item.title !== "string" ||
        !Number.isInteger(item.minutes) ||
        item.minutes <= 0,
    ) ||
    !Array.isArray(row.warnings) ||
    row.warnings.some((item) => typeof item !== "string")
  )
    throw Error("This study review could not be checked.");
  return {
    preview_hash: row.preview_hash,
    after: readStudyBlock(row.after),
    task: readStudyTask(row.task),
    remaining_unplanned_minutes: Number(row.remaining_unplanned_minutes),
    warnings: row.warnings as string[],
    recovery_required: row.recovery_required,
    preserved_blocks: row.preserved_blocks.map(readStudyBlock),
    unplanned_tasks: row.unplanned_tasks as {
      task_id: string;
      title: string;
      minutes: number;
      due_at: string | null;
    }[],
  };
}
export type StudyPlacement = ReturnType<typeof readStudyPlacement>;
