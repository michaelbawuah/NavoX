import { studyDeadlineChoices } from "./student-planning";
import { studentDateInZone } from "./student-schedule";

export type GoalCategory = "academic" | "habit" | "personal";
export type GoalTracking = "qualitative" | "count";
export type GoalCadence = "daily" | "weekly" | "total";
export type GoalStatus = "active" | "paused" | "completed";
export interface GoalDefinition {
  title: string;
  description: string;
  category: GoalCategory;
  tracking: GoalTracking;
  cadence: GoalCadence;
  target: number | null;
  unit: string | null;
  starts_on: string;
  ends_on: string | null;
}
export interface GoalCheckIn {
  id: string;
  goal_id: string;
  revision: number;
  goal_revision_when_reported: number;
  on_date: string;
  reported_timezone: string;
  count: number | null;
  completed: boolean | null;
  note: string;
  recorded_at: string;
  source: "self_report";
  count_meaning: "count_for_reported_local_day";
}
export interface GoalProgress {
  source: "self_report";
  timezone: string;
  week_starts_on: "monday";
  period_start: string;
  period_end: string;
  days_reported: number;
  reported_count: number | null;
  target: number | null;
  remaining_count: number | null;
  target_met_from_reports: boolean | null;
  qualitative_latest: "yes" | "no" | "unknown";
  latest_reported_on: string | null;
}
export interface StudentGoal extends GoalDefinition {
  id: string;
  revision: number;
  status: GoalStatus;
  progress: GoalProgress;
  recent_checkins: GoalCheckIn[];
  tracking_definition_locked: boolean;
  status_source: "student_selected";
}
export interface GoalsPlannerMetrics {
  source: "recorded_planner_outcomes";
  timezone: string;
  period_start: string;
  period_end: string;
  completed_blocks: number;
  skipped_blocks: number;
  recorded_minutes: number;
  attendance_sleep_and_grades_observed: false;
}
export interface StudentGoalsBoard {
  revision: number;
  generation: number;
  server_now: string;
  timezone: string;
  active_limit: 3;
  goals: StudentGoal[];
  planner_metrics: GoalsPlannerMetrics;
  notices: string[];
}
export interface GoalCreate extends GoalDefinition {
  request_id: string;
  generation: number;
}
export interface GoalUpdate extends GoalDefinition {
  request_id: string;
  revision: number;
  status: GoalStatus;
}
export interface GoalCheckInInput {
  request_id: string;
  goal_revision: number;
  revision: number;
  on_date: string;
  expected_timezone: string;
  count: number | null;
  completed: boolean | null;
  note: string;
}
export interface GoalErase {
  request_id: string;
  revision: number;
}
export interface GoalHandle {
  id: string;
  revision: number;
}
export type GoalCommand =
  | { kind: "create"; input: GoalCreate }
  | { kind: "update"; goal_id: string; input: GoalUpdate }
  | { kind: "checkin"; goal_id: string; input: GoalCheckInInput }
  | { kind: "erase_goal"; goal_id: string; input: GoalErase }
  | {
      kind: "erase_checkin";
      goal_id: string;
      checkin_id: string;
      input: GoalErase;
    }
  | { kind: "reset"; input: GoalErase };
export interface GoalsApi {
  account(signal: AbortSignal): Promise<string>;
  board(signal: AbortSignal): Promise<StudentGoalsBoard>;
  day(
    goalId: string,
    date: string,
    signal: AbortSignal,
  ): Promise<GoalCheckIn | null>;
  create(input: GoalCreate, signal: AbortSignal): Promise<StudentGoal>;
  update(
    goalId: string,
    input: GoalUpdate,
    signal: AbortSignal,
  ): Promise<StudentGoal>;
  checkin(
    goalId: string,
    input: GoalCheckInInput,
    signal: AbortSignal,
  ): Promise<GoalCheckIn>;
  eraseGoal(
    goalId: string,
    input: GoalErase,
    signal: AbortSignal,
  ): Promise<void>;
  eraseCheckin(
    goalId: string,
    checkinId: string,
    input: GoalErase,
    signal: AbortSignal,
  ): Promise<void>;
  reset(input: GoalErase, signal: AbortSignal): Promise<void>;
}
export interface GoalReportDraft {
  goal_id: string;
  on_date: string;
  entry: GoalCheckIn | null;
  goal_revision: number;
  timezone: string;
}
export interface GoalsState {
  board: StudentGoalsBoard | null;
  report: GoalReportDraft | null;
  handles: GoalHandle[];
  board_revision: number | null;
  pending: GoalCommand | null;
  busy: boolean;
  masked: boolean;
  blocked: boolean;
  error: string;
  notice: string;
  epoch: number;
}
function invalid(): never {
  throw new Error(
    "These saved goals could not be checked. Reload them before making a change.",
  );
}
function obj(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 1000): string {
  if (typeof value !== "string" || [...value].length > max) return invalid();
  return value;
}
function num(value: unknown, min = 0, max = 1_000_000): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < min ||
    value > max
  )
    return invalid();
  return value;
}
function bool(value: unknown): boolean {
  if (typeof value !== "boolean") return invalid();
  return value;
}
function choice<T extends string>(value: unknown, values: readonly T[]): T {
  if (!values.includes(value as T)) return invalid();
  return value as T;
}
function nullable<T>(value: unknown, parse: (item: unknown) => T): T | null {
  return value === null ? null : parse(value);
}
function list<T>(
  value: unknown,
  parse: (item: unknown) => T,
  max: number,
): T[] {
  if (!Array.isArray(value) || value.length > max) return invalid();
  return value.map(parse);
}
function id(value: unknown): string {
  const v = text(value, 36);
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      v,
    )
  )
    return invalid();
  return v;
}
function day(value: unknown): string {
  const v = text(value, 10);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(v) ||
    new Date(`${v}T12:00:00Z`).toISOString().slice(0, 10) !== v
  )
    return invalid();
  return v;
}
function instant(value: unknown): string {
  const v = text(value, 64);
  if (
    !/^\d{4}-\d{2}-\d{2}T/.test(v) ||
    !/(Z|[+-]\d{2}:\d{2})$/.test(v) ||
    !Number.isFinite(Date.parse(v))
  )
    return invalid();
  return v;
}
function zone(value: unknown): string {
  const v = text(value, 100);
  try {
    new Intl.DateTimeFormat("en", { timeZone: v });
  } catch {
    return invalid();
  }
  return v;
}
export function readGoalDefinition(value: unknown): GoalDefinition {
  const v = obj(value);
  const definition = {
    title: text(v.title, 160),
    description: text(v.description),
    category: choice(v.category, ["academic", "habit", "personal"] as const),
    tracking: choice(v.tracking, ["qualitative", "count"] as const),
    cadence: choice(v.cadence, ["daily", "weekly", "total"] as const),
    target: nullable(v.target, (item) => num(item, 1, 100000)),
    unit: nullable(v.unit, (item) => text(item, 40)),
    starts_on: day(v.starts_on),
    ends_on: nullable(v.ends_on, day),
  };
  if (
    !definition.title.trim() ||
    (definition.ends_on && definition.ends_on < definition.starts_on) ||
    (definition.tracking === "count" &&
      (!definition.target || !definition.unit?.trim())) ||
    (definition.tracking === "qualitative" &&
      (definition.target !== null || definition.unit !== null))
  )
    return invalid();
  return definition;
}
export function readGoalCheckIn(value: unknown): GoalCheckIn {
  const v = obj(value);
  if (
    v.source !== "self_report" ||
    v.count_meaning !== "count_for_reported_local_day"
  )
    return invalid();
  const count = nullable(v.count, (item) => num(item, 0, 100000)),
    completed = nullable(v.completed, bool);
  if ((count === null) === (completed === null)) return invalid();
  return {
    id: id(v.id),
    goal_id: id(v.goal_id),
    revision: num(v.revision, 1),
    goal_revision_when_reported: num(v.goal_revision_when_reported, 1),
    on_date: day(v.on_date),
    reported_timezone: zone(v.reported_timezone),
    count,
    completed,
    note: text(v.note),
    recorded_at: instant(v.recorded_at),
    source: "self_report",
    count_meaning: "count_for_reported_local_day",
  };
}
export function readGoalProgress(value: unknown): GoalProgress {
  const v = obj(value);
  if (v.source !== "self_report" || v.week_starts_on !== "monday")
    return invalid();
  const start = day(v.period_start),
    end = day(v.period_end);
  if (end < start) return invalid();
  return {
    source: "self_report",
    timezone: zone(v.timezone),
    week_starts_on: "monday",
    period_start: start,
    period_end: end,
    days_reported: num(v.days_reported, 0, 3000),
    reported_count: nullable(v.reported_count, (item) =>
      num(item, 0, 300_000_000),
    ),
    target: nullable(v.target, (item) => num(item, 1, 100000)),
    remaining_count: nullable(v.remaining_count, (item) =>
      num(item, 0, 100000),
    ),
    target_met_from_reports: nullable(v.target_met_from_reports, bool),
    qualitative_latest: choice(v.qualitative_latest, [
      "yes",
      "no",
      "unknown",
    ] as const),
    latest_reported_on: nullable(v.latest_reported_on, day),
  };
}
export function readStudentGoal(value: unknown): StudentGoal {
  const v = obj(value);
  if (v.status_source !== "student_selected") return invalid();
  const goal = {
    ...readGoalDefinition(v),
    id: id(v.id),
    revision: num(v.revision, 1),
    status: choice(v.status, ["active", "paused", "completed"] as const),
    progress: readGoalProgress(v.progress),
    recent_checkins: list(v.recent_checkins, readGoalCheckIn, 14),
    tracking_definition_locked: bool(v.tracking_definition_locked),
    status_source: "student_selected" as const,
  };
  if (
    goal.recent_checkins.some((entry) => entry.goal_id !== goal.id) ||
    (goal.tracking === "count" &&
      goal.progress.qualitative_latest !== "unknown") ||
    (goal.tracking === "qualitative" &&
      (goal.progress.reported_count !== null || goal.progress.target !== null))
  )
    return invalid();
  return goal;
}
export function readStudentGoalsBoard(value: unknown): StudentGoalsBoard {
  const v = obj(value),
    metrics = obj(v.planner_metrics);
  if (
    v.active_limit !== 3 ||
    metrics.source !== "recorded_planner_outcomes" ||
    metrics.attendance_sleep_and_grades_observed !== false
  )
    return invalid();
  const board = {
    revision: num(v.revision),
    generation: num(v.generation),
    server_now: instant(v.server_now),
    timezone: zone(v.timezone),
    active_limit: 3 as const,
    goals: list(v.goals, readStudentGoal, 30),
    planner_metrics: {
      source: "recorded_planner_outcomes" as const,
      timezone: zone(metrics.timezone),
      period_start: day(metrics.period_start),
      period_end: day(metrics.period_end),
      completed_blocks: num(metrics.completed_blocks),
      skipped_blocks: num(metrics.skipped_blocks),
      recorded_minutes: num(metrics.recorded_minutes),
      attendance_sleep_and_grades_observed: false as const,
    },
    notices: list(v.notices, (item) => text(item, 2000), 10),
  };
  if (
    board.goals.filter((goal) => goal.status === "active").length > 3 ||
    new Set(board.goals.map((goal) => goal.id)).size !== board.goals.length ||
    board.goals.some((goal) => goal.progress.timezone !== board.timezone) ||
    board.planner_metrics.timezone !== board.timezone
  )
    return invalid();
  return board;
}
export function goalDefinition(goal: GoalDefinition): GoalDefinition {
  return {
    title: goal.title,
    description: goal.description,
    category: goal.category,
    tracking: goal.tracking,
    cadence: goal.cadence,
    target: goal.target,
    unit: goal.unit,
    starts_on: goal.starts_on,
    ends_on: goal.ends_on,
  };
}
export function goalToday(board: StudentGoalsBoard): string {
  return studentDateInZone(board.server_now, board.timezone);
}
export function goalShiftDay(value: string, offset: number): string {
  const date = new Date(`${day(value)}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}
/** Server clock bounds the visible local-day summary; the browser clock is never an authority. */
export function goalFreshMilliseconds(board: StudentGoalsBoard): number {
  const tomorrow = goalShiftDay(goalToday(board), 1);
  let next: number | null = null;
  for (let minute = 0; minute < 180; minute++) {
    const clock = `${Math.floor(minute / 60)
      .toString()
      .padStart(2, "0")}:${(minute % 60).toString().padStart(2, "0")}`;
    const options = studyDeadlineChoices(
      `${tomorrow}T${clock}`,
      board.timezone,
    );
    if (options.length) {
      next = Date.parse(options[0]);
      break;
    }
  }
  return Math.max(
    0,
    Math.min(
      15 * 60_000,
      next === null ? 15 * 60_000 : next - Date.parse(board.server_now),
    ),
  );
}
export class GoalsRequestError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly uncertain: boolean,
  ) {
    super(message);
    this.name = "GoalsRequestError";
  }
}
const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export async function goalsRequest<T>(
  path: string,
  parse: (value: unknown) => T,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
  accountKey?: string,
): Promise<T> {
  try {
    const response = await fetch(`${apiBaseUrl}/student/goals${path}`, {
      method,
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(15_000)])
        : AbortSignal.timeout(15_000),
      headers: {
        ...(accountKey ? { "X-NavoX-Account": accountKey } : {}),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined
        ? {}
        : {
            body: JSON.stringify(body),
          }),
    });
    const value: unknown =
      response.status === 204
        ? undefined
        : await response.json().catch(() => null);
    if (!response.ok) {
      const detail =
        value && typeof value === "object"
          ? (value as Record<string, unknown>).detail
          : null;
      throw new GoalsRequestError(
        typeof detail === "string" && detail.length <= 1000
          ? detail
          : "The goals response could not be checked. Reload saved goals before making a change.",
        response.status,
        method !== "GET" && (response.status >= 500 || response.status === 408),
      );
    }
    try {
      return parse(value);
    } catch {
      throw new GoalsRequestError(
        "The saved goals response could not be checked. Check the same change before making another.",
        response.status,
        method !== "GET",
      );
    }
  } catch (cause) {
    if (cause instanceof GoalsRequestError) throw cause;
    throw new GoalsRequestError(
      method === "GET"
        ? "Saved goals could not be loaded. Try reloading them."
        : "The change could not be confirmed. Check this same change before making another.",
      null,
      method !== "GET",
    );
  }
}
const empty = (value: unknown) => {
  if (value !== undefined) return invalid();
};
export function createStudentGoalsApi(accountKey?: string): GoalsApi {
  return {
    async account(signal) {
      const response = await fetch(`${apiBaseUrl}/auth/me`, {
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
      });
      if (!response.ok)
        throw new Error("Sign in again to open your private goals.");
      const v = obj(await response.json());
      return `${id(obj(v.workspace).id)}:${id(v.id)}`;
    },
    board: (signal) =>
      goalsRequest(
        "",
        readStudentGoalsBoard,
        "GET",
        undefined,
        signal,
        accountKey,
      ),
    day: (goalId, date, signal) =>
      goalsRequest(
        `/${goalId}/checkins?on_date=${encodeURIComponent(date)}`,
        (value) => nullable(value, readGoalCheckIn),
        "GET",
        undefined,
        signal,
        accountKey,
      ),
    create: (input, signal) =>
      goalsRequest("", readStudentGoal, "POST", input, signal, accountKey),
    update: (goalId, input, signal) =>
      goalsRequest(
        `/${goalId}`,
        readStudentGoal,
        "PATCH",
        input,
        signal,
        accountKey,
      ),
    checkin: (goalId, input, signal) =>
      goalsRequest(
        `/${goalId}/checkins`,
        readGoalCheckIn,
        "POST",
        input,
        signal,
        accountKey,
      ),
    eraseGoal: (goalId, input, signal) =>
      goalsRequest(`/${goalId}`, empty, "DELETE", input, signal, accountKey),
    eraseCheckin: (goalId, checkinId, input, signal) =>
      goalsRequest(
        `/${goalId}/checkins/${checkinId}`,
        empty,
        "DELETE",
        input,
        signal,
        accountKey,
      ),
    reset: (input, signal) =>
      goalsRequest("", empty, "DELETE", input, signal, accountKey),
  };
}
export const studentGoalsApi: GoalsApi = createStudentGoalsApi();
export function initialGoalsState(): GoalsState {
  return {
    board: null,
    report: null,
    handles: [],
    board_revision: null,
    pending: null,
    busy: false,
    masked: false,
    blocked: false,
    error: "",
    notice: "Loading your saved goals…",
    epoch: 0,
  };
}

/** In-memory private workspace with exact-command recovery and owner proofs around every call. */
export class StudentGoalsController {
  private state = initialGoalsState();
  private listeners = new Set<() => void>();
  private generation = 0;
  private request: AbortController | null = null;
  private inFlightCommand: GoalCommand | null = null;
  private freshness: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  constructor(
    readonly accountId: string,
    private api: GoalsApi = createStudentGoalsApi(accountId),
    private requestId: () => string = () => crypto.randomUUID(),
  ) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private update(change: Partial<GoalsState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...change };
    for (const listener of this.listeners) listener();
  }
  clear = (
    notice = "Private goal text was cleared. Reload saved goals when you are ready.",
  ) => {
    ++this.generation;
    this.request?.abort();
    this.request = null;
    this.inFlightCommand = null;
    if (this.freshness) clearTimeout(this.freshness);
    this.freshness = null;
    const { handles, board_revision } = this.state;
    this.update({
      ...initialGoalsState(),
      handles,
      board_revision,
      notice,
      epoch: this.state.epoch + 1,
    });
  };
  suspend = () => {
    if (this.disposed) return;
    ++this.generation;
    this.request?.abort();
    this.request = null;
    const pending = this.inFlightCommand ?? this.state.pending;
    this.inFlightCommand = null;
    this.update({ masked: true, busy: false, pending, error: "" });
  };
  resume = async () => {
    if (this.disposed || !this.state.masked || this.state.busy) return;
    const generation = ++this.generation,
      request = new AbortController();
    this.request = request;
    this.update({ busy: true });
    const current = () =>
      !this.disposed &&
      generation === this.generation &&
      !request.signal.aborted;
    try {
      const owner = await this.api.account(request.signal);
      if (!current()) return;
      if (owner !== this.accountId) throw new Error("Account changed");
      this.request = null;
      this.update({ masked: false, busy: false });
    } catch {
      if (!current()) return;
      this.clear(
        "Your account could not be checked. Private goal text was cleared.",
      );
      this.update({
        blocked: true,
        error: "Reload this page after signing in to the intended account.",
      });
    }
  };
  dispose = () => {
    this.clear();
    this.disposed = true;
    this.listeners.clear();
  };
  private show(board: StudentGoalsBoard) {
    if (this.freshness) clearTimeout(this.freshness);
    this.freshness = setTimeout(
      () =>
        this.clear(
          "The local-day summary needs refreshing. Reload your goals to continue.",
        ),
      goalFreshMilliseconds(board),
    );
    this.update({
      board,
      report: null,
      handles: board.goals.map((goal) => ({
        id: goal.id,
        revision: goal.revision,
      })),
      board_revision: board.revision,
      error: "",
      notice: board.goals.length
        ? "Your goals, at your pace. All check-ins below are reported by you."
        : "A goal can be small. Keep up to three active, or leave this space empty.",
      epoch: this.state.epoch + 1,
    });
  }
  private async run<T>(
    operation: (signal: AbortSignal) => Promise<T>,
    apply: (value: T) => void,
    command: GoalCommand | null = null,
  ) {
    if (
      this.disposed ||
      this.state.busy ||
      this.state.masked ||
      this.state.blocked ||
      (this.state.pending && this.state.pending !== command)
    )
      return;
    const generation = ++this.generation,
      request = new AbortController();
    this.request = request;
    this.inFlightCommand = command;
    const current = () =>
      !this.disposed &&
      generation === this.generation &&
      !request.signal.aborted;
    this.update({ busy: true, error: "" });
    let checked = false;
    try {
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new Error("Account changed");
      checked = true;
      let result: T;
      try {
        result = await operation(request.signal);
      } catch (cause) {
        checked = false;
        if (!current()) return;
        if ((await this.api.account(request.signal)) !== this.accountId)
          throw new Error("Account changed");
        checked = true;
        throw cause;
      }
      checked = false;
      if (!current()) return;
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new Error("Account changed");
      checked = true;
      if (current()) {
        this.update({ pending: null });
        apply(result);
      }
    } catch (cause) {
      if (!current()) return;
      if (!checked) {
        this.clear(
          "Your account could not be checked. Private goal text was cleared.",
        );
        this.update({
          blocked: true,
          error: "Reload this page after signing in to the intended account.",
        });
        return;
      }
      if (command && cause instanceof GoalsRequestError && cause.uncertain) {
        this.update({
          pending: command,
          error:
            "The change has an unknown result. Check this same change before making another.",
        });
      } else {
        const message =
          cause instanceof Error
            ? cause.message
            : "The goals could not be checked.";
        if (
          cause instanceof GoalsRequestError &&
          [401, 403, 404, 409, 410].includes(cause.status ?? 0)
        )
          this.clear(
            "The saved goals changed or are unavailable. Reload them before continuing.",
          );
        this.update({ pending: null, error: message });
      }
    } finally {
      if (current()) {
        this.request = null;
        this.inFlightCommand = null;
        this.update({ busy: false });
      }
    }
  }
  load = async () => {
    await this.run(
      (signal) => this.api.board(signal),
      (board) => this.show(board),
    );
  };
  loadDay = async (goalId: string, onDate: string) => {
    const known = this.state.board?.goals.find((goal) => goal.id === goalId),
      timezone = this.state.board?.timezone;
    if (!known || !timezone) return;
    day(onDate);
    await this.run(
      async (signal) => {
        const board = await this.api.board(signal);
        const fresh = board.goals.find((goal) => goal.id === goalId);
        if (
          board.timezone !== timezone ||
          !fresh ||
          fresh.revision !== known.revision
        )
          throw new GoalsRequestError(
            "Your goal or time zone changed. Reload before checking in.",
            409,
            false,
          );
        const entry = await this.api.day(goalId, onDate, signal);
        if (entry && (entry.goal_id !== goalId || entry.on_date !== onDate))
          throw new GoalsRequestError(
            "That report could not be matched to this date. Reload your saved goals.",
            409,
            false,
          );
        return { board, entry };
      },
      (result) => {
        this.show(result.board);
        this.update({
          report: {
            goal_id: goalId,
            on_date: onDate,
            entry: result.entry,
            goal_revision: known.revision,
            timezone,
          },
          notice: result.entry
            ? "A report already exists for this date. Saving replaces it; it does not add another count."
            : "No report is saved for this date. Choose what you want to report.",
        });
      },
    );
  };
  private execute = async (command: GoalCommand) => {
    await this.run(
      async (signal) => {
        let handle: GoalHandle | null = null;
        switch (command.kind) {
          case "create": {
            const goal = await this.api.create(command.input, signal);
            handle = { id: goal.id, revision: goal.revision };
            break;
          }
          case "update": {
            const goal = await this.api.update(
              command.goal_id,
              command.input,
              signal,
            );
            if (goal.id !== command.goal_id)
              throw new GoalsRequestError(
                "The changed goal could not be checked. Check this same change.",
                null,
                true,
              );
            handle = { id: goal.id, revision: goal.revision };
            break;
          }
          case "checkin": {
            const board = await this.api.board(signal),
              goal = board.goals.find((goal) => goal.id === command.goal_id);
            if (board.timezone !== command.input.expected_timezone || !goal)
              throw new GoalsRequestError(
                "Your goal or saved time zone changed. Reload before checking in.",
                409,
                false,
              );
            const entry = await this.api.checkin(
              command.goal_id,
              command.input,
              signal,
            );
            if (
              entry.goal_id !== command.goal_id ||
              entry.on_date !== command.input.on_date
            )
              throw new GoalsRequestError(
                "The changed report could not be checked. Check this same change.",
                null,
                true,
              );
            break;
          }
          case "erase_goal":
            await this.api.eraseGoal(command.goal_id, command.input, signal);
            break;
          case "erase_checkin":
            await this.api.eraseCheckin(
              command.goal_id,
              command.checkin_id,
              command.input,
              signal,
            );
            break;
          case "reset":
            await this.api.reset(command.input, signal);
            break;
        }
        let board: StudentGoalsBoard | null = null;
        try {
          board = await this.api.board(signal);
        } catch {}
        return { board, handle };
      },
      (result) => {
        if (result.board) {
          this.show(result.board);
          this.update({
            notice:
              command.kind === "checkin"
                ? "Your report was saved for that local day. It replaces any previous report for the same date."
                : command.kind === "erase_goal"
                  ? "That goal and its check-ins were permanently erased."
                  : command.kind === "erase_checkin"
                    ? "That check-in was erased. Missing reports remain unknown."
                    : command.kind === "reset"
                      ? "All saved goals and their reports were permanently erased."
                      : "Your goal was saved. It has not changed your study plan.",
          });
          return;
        }
        let handles = this.state.handles;
        if (command.kind === "reset") handles = [];
        else if (command.kind === "erase_goal")
          handles = handles.filter((goal) => goal.id !== command.goal_id);
        else if (result.handle)
          handles = [
            ...handles.filter((goal) => goal.id !== result.handle?.id),
            result.handle,
          ];
        this.clear(
          "The change was saved, but the updated summary could not be loaded. Reload saved goals to check it; do not submit it again.",
        );
        this.update({
          handles,
          board_revision: null,
          error:
            "The saved summary is unavailable. No further changes are enabled until it reloads.",
        });
      },
      command,
    );
  };
  create = async (definition: GoalDefinition) => {
    const board = this.state.board;
    if (!board || this.state.pending) return;
    if (board.goals.filter((goal) => goal.status === "active").length >= 3) {
      this.update({
        error:
          "Pause or complete an active goal before adding another. You can keep up to three active.",
      });
      return;
    }
    await this.execute({
      kind: "create",
      input: {
        ...readGoalDefinition(definition),
        request_id: this.requestId(),
        generation: board.generation,
      },
    });
  };
  updateGoal = async (
    goalId: string,
    definition: GoalDefinition,
    status: GoalStatus,
  ) => {
    const goal = this.state.board?.goals.find((goal) => goal.id === goalId);
    if (!goal || this.state.pending) return;
    await this.execute({
      kind: "update",
      goal_id: goalId,
      input: {
        ...readGoalDefinition(definition),
        request_id: this.requestId(),
        revision: goal.revision,
        status,
      },
    });
  };
  checkin = async (value: {
    count: number | null;
    completed: boolean | null;
    note: string;
  }) => {
    const report = this.state.report,
      goal = this.state.board?.goals.find(
        (goal) => goal.id === report?.goal_id,
      );
    if (!report || !goal || goal.status !== "active" || this.state.pending)
      return;
    if (
      (value.count === null) === (value.completed === null) ||
      (goal.tracking === "count" && value.count === null) ||
      (goal.tracking === "qualitative" && value.completed === null)
    )
      throw new Error(
        "Choose an explicit yes/no report or the count for this date.",
      );
    if (value.count !== null) num(value.count, 0, 100000);
    text(value.note);
    await this.execute({
      kind: "checkin",
      goal_id: goal.id,
      input: {
        request_id: this.requestId(),
        goal_revision: report.goal_revision,
        revision: report.entry?.revision ?? 0,
        on_date: report.on_date,
        expected_timezone: report.timezone,
        count: value.count,
        completed: value.completed,
        note: value.note,
      },
    });
  };
  eraseGoal = async (goalId: string, confirmed: boolean) => {
    const handle = this.state.handles.find((goal) => goal.id === goalId);
    if (!confirmed || !handle || this.state.pending) return;
    await this.execute({
      kind: "erase_goal",
      goal_id: handle.id,
      input: { request_id: this.requestId(), revision: handle.revision },
    });
  };
  eraseCheckin = async (confirmed: boolean) => {
    const report = this.state.report;
    if (!confirmed || !report?.entry || this.state.pending) return;
    await this.execute({
      kind: "erase_checkin",
      goal_id: report.goal_id,
      checkin_id: report.entry.id,
      input: { request_id: this.requestId(), revision: report.entry.revision },
    });
  };
  reset = async (confirmed: boolean) => {
    const revision = this.state.board_revision;
    if (!confirmed || revision === null || this.state.pending) return;
    await this.execute({
      kind: "reset",
      input: { request_id: this.requestId(), revision },
    });
  };
  checkPending = async () => {
    if (this.state.pending) await this.execute(this.state.pending);
  };
}

export function observeGoalsPrivacy(
  clear: () => void,
  surface: {
    window: Pick<Window, "addEventListener" | "removeEventListener">;
    document: Pick<
      Document,
      "addEventListener" | "removeEventListener" | "visibilityState"
    > &
      Partial<Pick<Document, "hasFocus">>;
    channel: Pick<
      BroadcastChannel,
      "addEventListener" | "removeEventListener" | "close"
    > | null;
  },
  transitions?: { suspend: () => void; resume: () => void },
) {
  let focused = surface.document.hasFocus?.() ?? true;
  const suspend = transitions?.suspend ?? clear;
  const resume = () => {
    if (focused && surface.document.visibilityState === "visible")
      transitions?.resume();
  };
  const blur = () => {
    focused = false;
    suspend();
  };
  const focus = () => {
    focused = true;
    resume();
  };
  const visibility = () => {
    if (surface.document.visibilityState !== "visible") suspend();
    else resume();
  };
  surface.window.addEventListener("blur", blur);
  surface.window.addEventListener("focus", focus);
  for (const event of ["pagehide", "storage"])
    surface.window.addEventListener(event, clear);
  surface.document.addEventListener("visibilitychange", visibility);
  surface.channel?.addEventListener("message", clear);
  if (!focused || surface.document.visibilityState !== "visible") suspend();
  return () => {
    surface.window.removeEventListener("blur", blur);
    surface.window.removeEventListener("focus", focus);
    for (const event of ["pagehide", "storage"])
      surface.window.removeEventListener(event, clear);
    surface.document.removeEventListener("visibilitychange", visibility);
    surface.channel?.removeEventListener("message", clear);
    surface.channel?.close();
    clear();
  };
}
