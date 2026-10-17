export const routineRecipes = [
  "morning_priorities",
  "friday_catch_up",
  "no_reply_check",
] as const;
export type RoutineRecipe = (typeof routineRecipes)[number];

export interface RoutineConfig {
  name: string;
  recipe: RoutineRecipe;
  timezone: string;
  hour: number;
  minute: number;
  days_of_week: number[];
}
export interface RoutineResult {
  checked_at: string;
  basis: "saved_data";
  headline: string;
  notice: string;
  items: {
    id: string;
    title: string;
    status: string;
    due_at: string | null;
    kind: "commitment" | "thread_watch";
  }[];
}
export interface PersonalRoutine extends RoutineConfig {
  id: string;
  enabled: boolean;
  schedule_status: string;
  next_run_at: string | null;
  last_run_at: string | null;
  last_result: RoutineResult | null;
  created_at: string;
}
export interface RoutinePreview {
  preview_token: string;
  expires_at: string;
  result: RoutineResult;
}
export interface RoutineRun {
  id: string;
  scheduled_for: string;
  checked_at: string;
  status: "completed" | "skipped" | "failed";
  result: RoutineResult | null;
  notice: string;
}
export const recipeLabels: Record<RoutineRecipe, string> = {
  morning_priorities: "Morning priorities",
  friday_catch_up: "Friday catch-up",
  no_reply_check: "No-reply check",
};
export const recipeDescriptions: Record<RoutineRecipe, string> = {
  morning_priorities:
    "Bring your saved priorities together at the start of the day.",
  friday_catch_up:
    "Review overdue work, waiting items, and recent completions.",
  no_reply_check:
    "Bring saved thread-watch results that need a follow-up to your attention.",
};
export const routineDays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function routineTemplate(
  recipe: RoutineRecipe,
  timezone = "UTC",
): RoutineConfig {
  return {
    name: recipeLabels[recipe],
    recipe,
    timezone,
    hour: recipe === "friday_catch_up" ? 16 : 8,
    minute: 0,
    days_of_week: recipe === "friday_catch_up" ? [4] : [0, 1, 2, 3, 4],
  };
}

export function routineConfiguration(config: RoutineConfig): RoutineConfig {
  return {
    name: config.name.trim(),
    recipe: config.recipe,
    timezone: config.timezone.trim(),
    hour: config.hour,
    minute: config.minute,
    days_of_week: [...new Set(config.days_of_week)].sort((a, b) => a - b),
  };
}

export function routineConfigKey(config: RoutineConfig): string {
  return JSON.stringify(routineConfiguration(config));
}

export function routineConfigError(config: RoutineConfig): string | null {
  const normalized = routineConfiguration(config);
  if (!normalized.name || normalized.name.length > 100)
    return "Give this routine a name of up to 100 characters.";
  if (!routineRecipes.includes(normalized.recipe))
    return "Choose a routine template.";
  if (
    !Number.isInteger(normalized.hour) ||
    normalized.hour < 0 ||
    normalized.hour > 23 ||
    !Number.isInteger(normalized.minute) ||
    normalized.minute < 0 ||
    normalized.minute > 59
  )
    return "Choose a valid time.";
  if (
    !normalized.days_of_week.length ||
    normalized.days_of_week.some(
      (day) => !Number.isInteger(day) || day < 0 || day > 6,
    )
  )
    return "Choose at least one day.";
  try {
    if (!normalized.timezone || normalized.timezone.length > 64)
      throw new Error();
    new Intl.DateTimeFormat("en", { timeZone: normalized.timezone }).format();
  } catch {
    return "Choose a valid timezone, such as America/New_York.";
  }
  return null;
}

/** An enabling receipt applies only to the exact reviewed configuration. */
export function canEnableRoutine(
  config: RoutineConfig,
  preview: (RoutinePreview & { configKey: string }) | null,
  now: number,
): boolean {
  return !!(
    preview &&
    preview.configKey === routineConfigKey(config) &&
    Number.isFinite(now) &&
    Date.parse(preview.expires_at) > now &&
    !routineConfigError(config)
  );
}

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function text(value: unknown, max = 1000): value is string {
  return typeof value === "string" && value.length <= max;
}
function date(value: unknown): value is string {
  return text(value, 64) && Number.isFinite(Date.parse(value));
}
function nullableDate(value: unknown): value is string | null {
  return value === null || date(value);
}
function invalid(): never {
  throw new Error("Routines could not be read. Please refresh and try again.");
}

export function readRoutineResult(value: unknown): RoutineResult {
  if (
    !record(value) ||
    !date(value.checked_at) ||
    value.basis !== "saved_data" ||
    !text(value.headline) ||
    !text(value.notice) ||
    !Array.isArray(value.items) ||
    value.items.length > 15 ||
    value.items.some(
      (item) =>
        !record(item) ||
        !text(item.id, 64) ||
        !text(item.title, 256) ||
        !text(item.status, 32) ||
        !nullableDate(item.due_at) ||
        !["commitment", "thread_watch"].includes(String(item.kind)),
    )
  )
    invalid();
  return value as unknown as RoutineResult;
}

export function readRoutine(value: unknown): PersonalRoutine {
  if (
    !record(value) ||
    !text(value.id, 64) ||
    !text(value.name, 100) ||
    !text(value.recipe, 32) ||
    !routineRecipes.includes(value.recipe as RoutineRecipe) ||
    !text(value.timezone, 64) ||
    typeof value.hour !== "number" ||
    typeof value.minute !== "number" ||
    !Array.isArray(value.days_of_week) ||
    value.days_of_week.length > 7 ||
    !value.days_of_week.every((day) => typeof day === "number") ||
    typeof value.enabled !== "boolean" ||
    !text(value.schedule_status, 32) ||
    !nullableDate(value.next_run_at) ||
    !nullableDate(value.last_run_at) ||
    !date(value.created_at)
  )
    invalid();
  if (routineConfigError(value as unknown as RoutineConfig)) invalid();
  if (value.last_result !== null) readRoutineResult(value.last_result);
  return value as unknown as PersonalRoutine;
}

export function readRoutineList(value: unknown): {
  routines: PersonalRoutine[];
  paused: boolean;
} {
  if (
    !record(value) ||
    typeof value.paused !== "boolean" ||
    !Array.isArray(value.routines) ||
    value.routines.length > 50
  )
    invalid();
  return { routines: value.routines.map(readRoutine), paused: value.paused };
}

export function readRoutinePreview(value: unknown): RoutinePreview {
  if (
    !record(value) ||
    !text(value.preview_token, 64) ||
    !date(value.expires_at)
  )
    invalid();
  return {
    preview_token: value.preview_token,
    expires_at: value.expires_at,
    result: readRoutineResult(value.result),
  };
}

export function readRoutineRuns(value: unknown): RoutineRun[] {
  if (!record(value) || !Array.isArray(value.runs) || value.runs.length > 20)
    invalid();
  return value.runs.map((run) => {
    if (
      !record(run) ||
      !text(run.id, 64) ||
      !date(run.checked_at) ||
      !date(run.scheduled_for) ||
      !["completed", "skipped", "failed"].includes(String(run.status)) ||
      !text(run.notice)
    )
      invalid();
    if (run.result !== null) readRoutineResult(run.result);
    return run as unknown as RoutineRun;
  });
}

export function routineTime(value: string, timezone = "UTC"): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "Time unavailable";
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: timezone,
      dateStyle: "medium",
      timeStyle: "short",
    }).format(timestamp);
  } catch {
    return "Time unavailable";
  }
}

export function routineSchedule(config: RoutineConfig): string {
  return `${routineConfiguration(config)
    .days_of_week.map((day) => routineDays[day])
    .join(
      ", ",
    )} at ${String(config.hour).padStart(2, "0")}:${String(config.minute).padStart(2, "0")} · ${config.timezone}`;
}
