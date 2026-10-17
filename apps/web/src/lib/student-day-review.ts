import {
  type GoalProgress,
  goalShiftDay,
  readGoalProgress,
} from "./student-goals";
import { studyDeadlineChoices } from "./student-planning";
import {
  readStudentOccurrence,
  type StudentOccurrence,
  studentDateInZone,
} from "./student-schedule";

export type DayReviewKind = "morning" | "evening" | "weekly";
export type DayReviewStatus =
  | "complete"
  | "incomplete"
  | "disabled"
  | "unavailable";
export type DayReviewHref =
  | "/classes"
  | "/planner"
  | "/goals"
  | "/email-actions"
  | "/connections";
export interface ReviewWindow {
  starts_at: string;
  ends_at: string;
  observed_through: string;
}
export interface ReviewFixedBlock {
  id: string;
  fixed_block_id: string;
  title: string;
  start_at: string;
  end_at: string;
}
export interface ReviewSchedule {
  source: "saved_timetable";
  status: DayReviewStatus;
  window: ReviewWindow;
  classes: StudentOccurrence[];
  fixed_blocks: ReviewFixedBlock[];
  classes_complete: boolean;
  fixed_blocks_complete: boolean;
  class_count: number | null;
  fixed_block_count: number | null;
  next_class: StudentOccurrence | null;
  review_href: "/classes";
}
export interface ReviewTask {
  source: "saved_task";
  source_kind: "manual" | "lms" | "email";
  id: string;
  revision: number;
  title: string;
  course_id: string | null;
  due_at: string | null;
  deadline_meaning: "saved_task_deadline";
  remaining_minutes: number;
  priority: number;
  review_href: "/planner";
}
export interface ReviewWorkTotals {
  active_tasks: number;
  overdue_tasks: number;
  due_in_window_tasks: number;
}
export interface ReviewWork {
  source: "saved_task";
  status: DayReviewStatus;
  window: ReviewWindow;
  items: ReviewTask[];
  totals: ReviewWorkTotals | null;
  known_totals: ReviewWorkTotals | null;
  has_more: boolean;
}
export interface ReviewOutcomeTotals {
  done_blocks: number;
  took_longer_blocks: number;
  skipped_blocks: number;
  recorded_minutes: number;
}
export interface ReviewOutcomes {
  source: "recorded_planner_outcome";
  status: DayReviewStatus;
  window: ReviewWindow;
  totals: ReviewOutcomeTotals | null;
  known_totals: ReviewOutcomeTotals | null;
  done_block_is_assignment_submission: false;
}
export interface ReviewGoal {
  source: "self_report";
  id: string;
  revision: number;
  title: string;
  category: "academic" | "habit" | "personal";
  tracking: "qualitative" | "count";
  unit: string | null;
  cadence: "daily" | "weekly" | "total";
  starts_on: string;
  ends_on: string | null;
  progress: GoalProgress;
  review_href: "/goals";
}
export interface ReviewGoals {
  source: "self_report";
  status: DayReviewStatus;
  items: ReviewGoal[];
  periods_meaning: "each_goal_has_its_own_progress_period";
}
export interface ReviewNotice {
  code: string;
  message: string;
  review_href: DayReviewHref | null;
}
export interface ReviewQuestion {
  code: string;
  text: string;
  review_href: DayReviewHref;
}
export interface StudentDayReviewView {
  kind: DayReviewKind;
  server_now: string;
  timezone: string;
  local_date: string;
  week_starts_on: "monday";
  window: ReviewWindow;
  schedule: ReviewSchedule;
  outlook: ReviewSchedule;
  work: ReviewWork;
  outcomes: ReviewOutcomes;
  goals: ReviewGoals;
  summary_lines: string[];
  notices: ReviewNotice[];
  question: ReviewQuestion | null;
  basis: "saved_records_and_explicit_reports_only";
  automatic_rescheduling: false;
  attendance_sleep_grades_observed: false;
}
export interface DayReviewLinks {
  planning: boolean;
  goals: boolean;
  email: boolean;
}
export interface DayReviewApi {
  account(signal: AbortSignal): Promise<string>;
  review(
    kind: DayReviewKind,
    signal: AbortSignal,
  ): Promise<StudentDayReviewView>;
}
export interface DayReviewState {
  kind: DayReviewKind;
  review: StudentDayReviewView | null;
  busy: boolean;
  masked: boolean;
  blocked: boolean;
  error: string;
  notice: string;
  epoch: number;
}
function invalid(): never {
  throw new DayReviewError(
    "This review could not be checked. Open a fresh review.",
  );
}
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 2000): string {
  if (typeof value !== "string" || [...value].length > max) return invalid();
  return value;
}
function num(value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): number {
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
function option<T extends string>(value: unknown, values: readonly T[]): T {
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
function day(value: unknown): string {
  const v = text(value, 10),
    date = new Date(`${v}T12:00:00Z`);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(v) ||
    !Number.isFinite(date.valueOf()) ||
    date.toISOString().slice(0, 10) !== v
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
const statuses = ["complete", "incomplete", "disabled", "unavailable"] as const;
const hrefs = [
  "/classes",
  "/planner",
  "/goals",
  "/email-actions",
  "/connections",
] as const;
function window(value: unknown): ReviewWindow {
  const v = record(value),
    start = instant(v.starts_at),
    end = instant(v.ends_at);
  if (Date.parse(end) <= Date.parse(start)) return invalid();
  return {
    starts_at: start,
    ends_at: end,
    observed_through: instant(v.observed_through),
  };
}
function sameWindow(left: ReviewWindow, right: ReviewWindow) {
  return ["starts_at", "ends_at", "observed_through"].every(
    (key) =>
      Date.parse(left[key as keyof ReviewWindow]) ===
      Date.parse(right[key as keyof ReviewWindow]),
  );
}
function available(status: DayReviewStatus) {
  return status === "complete" || status === "incomplete";
}
function occurrence(value: unknown): StudentOccurrence {
  const row = readStudentOccurrence(value);
  id(row.course_id);
  id(row.meeting_id);
  text(row.id, 128);
  text(row.course_code, 40);
  text(row.course_name, 200);
  for (const field of [row.building, row.room, row.instructor])
    if (field !== null) text(field, 200);
  if (Date.parse(row.end_at) <= Date.parse(row.start_at) || row.cancelled)
    return invalid();
  return row;
}
function schedule(value: unknown): ReviewSchedule {
  const v = record(value);
  if (v.source !== "saved_timetable" || v.review_href !== "/classes")
    return invalid();
  const row: ReviewSchedule = {
    source: "saved_timetable",
    status: option(v.status, statuses),
    window: window(v.window),
    classes: list(v.classes, occurrence, 250),
    fixed_blocks: list(
      v.fixed_blocks,
      (item) => {
        const x = record(item),
          start = instant(x.start_at),
          end = instant(x.end_at);
        if (Date.parse(end) <= Date.parse(start)) return invalid();
        return {
          id: text(x.id, 128),
          fixed_block_id: id(x.fixed_block_id),
          title: text(x.title, 200),
          start_at: start,
          end_at: end,
        };
      },
      100,
    ),
    classes_complete: bool(v.classes_complete),
    fixed_blocks_complete: bool(v.fixed_blocks_complete),
    class_count: nullable(v.class_count, (item) => num(item, 0, 250)),
    fixed_block_count: nullable(v.fixed_block_count, (item) =>
      num(item, 0, 100),
    ),
    next_class: nullable(v.next_class, occurrence),
    review_href: "/classes",
  };
  if (available(row.status)) {
    if (
      row.classes_complete
        ? row.class_count !== row.classes.length
        : row.class_count !== null
    )
      return invalid();
    if (
      row.fixed_blocks_complete
        ? row.fixed_block_count !== row.fixed_blocks.length
        : row.fixed_block_count !== null
    )
      return invalid();
    if (
      row.next_class &&
      (!row.classes_complete ||
        !row.classes.some((item) => item.id === row.next_class?.id) ||
        Date.parse(row.next_class.start_at) <
          Date.parse(row.window.observed_through))
    )
      return invalid();
  } else if (
    row.classes.length ||
    row.fixed_blocks.length ||
    row.class_count !== null ||
    row.fixed_block_count !== null ||
    row.next_class !== null
  )
    return invalid();
  return row;
}
function workTotals(value: unknown): ReviewWorkTotals {
  const v = record(value),
    row = {
      active_tasks: num(v.active_tasks, 0, 200),
      overdue_tasks: num(v.overdue_tasks, 0, 200),
      due_in_window_tasks: num(v.due_in_window_tasks, 0, 200),
    };
  if (
    row.overdue_tasks > row.active_tasks ||
    row.due_in_window_tasks > row.active_tasks
  )
    return invalid();
  return row;
}
function work(value: unknown): ReviewWork {
  const v = record(value);
  if (v.source !== "saved_task") return invalid();
  const row: ReviewWork = {
    source: "saved_task",
    status: option(v.status, statuses),
    window: window(v.window),
    items: list(
      v.items,
      (item) => {
        const x = record(item);
        if (
          x.source !== "saved_task" ||
          x.deadline_meaning !== "saved_task_deadline" ||
          x.review_href !== "/planner"
        )
          return invalid();
        return {
          source: "saved_task",
          source_kind: option(x.source_kind, [
            "manual",
            "lms",
            "email",
          ] as const),
          id: id(x.id),
          revision: num(x.revision, 1),
          title: text(x.title, 200),
          course_id: nullable(x.course_id, id),
          due_at: nullable(x.due_at, instant),
          deadline_meaning: "saved_task_deadline",
          remaining_minutes: num(x.remaining_minutes, 1, 10080),
          priority: num(x.priority, 1, 5),
          review_href: "/planner",
        };
      },
      20,
    ),
    totals: nullable(v.totals, workTotals),
    known_totals: nullable(v.known_totals, workTotals),
    has_more: bool(v.has_more),
  };
  if (
    !available(row.status) &&
    (row.items.length || row.totals || row.known_totals || row.has_more)
  )
    return invalid();
  if (
    (row.status === "complete" && !row.totals) ||
    (row.status !== "complete" && row.totals !== null)
  )
    return invalid();
  return row;
}
function outcomeTotals(value: unknown): ReviewOutcomeTotals {
  const v = record(value);
  return {
    done_blocks: num(v.done_blocks),
    took_longer_blocks: num(v.took_longer_blocks),
    skipped_blocks: num(v.skipped_blocks),
    recorded_minutes: num(v.recorded_minutes),
  };
}
function outcomes(value: unknown): ReviewOutcomes {
  const v = record(value);
  if (
    v.source !== "recorded_planner_outcome" ||
    v.done_block_is_assignment_submission !== false
  )
    return invalid();
  const row: ReviewOutcomes = {
    source: "recorded_planner_outcome",
    status: option(v.status, statuses),
    window: window(v.window),
    totals: nullable(v.totals, outcomeTotals),
    known_totals: nullable(v.known_totals, outcomeTotals),
    done_block_is_assignment_submission: false,
  };
  if (!available(row.status) && (row.totals || row.known_totals))
    return invalid();
  if (
    (row.status === "complete" && !row.totals) ||
    (row.status !== "complete" && row.totals !== null)
  )
    return invalid();
  return row;
}
function goals(value: unknown): ReviewGoals {
  const v = record(value);
  if (
    v.source !== "self_report" ||
    v.periods_meaning !== "each_goal_has_its_own_progress_period"
  )
    return invalid();
  const row: ReviewGoals = {
    source: "self_report",
    status: option(v.status, statuses),
    items: list(
      v.items,
      (item) => {
        const x = record(item);
        if (x.source !== "self_report" || x.review_href !== "/goals")
          return invalid();
        const tracking = option(x.tracking, ["qualitative", "count"] as const),
          progress = readGoalProgress(x.progress),
          unit = nullable(x.unit, (item) => text(item, 40));
        if (
          (tracking === "count" && (!unit || progress.target === null)) ||
          (tracking === "qualitative" &&
            (unit !== null ||
              progress.target !== null ||
              progress.reported_count !== null))
        )
          return invalid();
        return {
          source: "self_report",
          id: id(x.id),
          revision: num(x.revision, 1),
          title: text(x.title, 160),
          category: option(x.category, [
            "academic",
            "habit",
            "personal",
          ] as const),
          tracking,
          unit,
          cadence: option(x.cadence, ["daily", "weekly", "total"] as const),
          starts_on: day(x.starts_on),
          ends_on: nullable(x.ends_on, day),
          progress,
          review_href: "/goals",
        };
      },
      3,
    ),
    periods_meaning: "each_goal_has_its_own_progress_period",
  };
  if (!available(row.status) && row.items.length) return invalid();
  return row;
}
export function readStudentDayReview(value: unknown): StudentDayReviewView {
  const v = record(value);
  if (
    v.week_starts_on !== "monday" ||
    v.basis !== "saved_records_and_explicit_reports_only" ||
    v.automatic_rescheduling !== false ||
    v.attendance_sleep_grades_observed !== false
  )
    return invalid();
  const row: StudentDayReviewView = {
    kind: option(v.kind, ["morning", "evening", "weekly"] as const),
    server_now: instant(v.server_now),
    timezone: zone(v.timezone),
    local_date: day(v.local_date),
    week_starts_on: "monday",
    window: window(v.window),
    schedule: schedule(v.schedule),
    outlook: schedule(v.outlook),
    work: work(v.work),
    outcomes: outcomes(v.outcomes),
    goals: goals(v.goals),
    summary_lines: list(v.summary_lines, (item) => text(item), 10),
    notices: list(
      v.notices,
      (item) => {
        const x = record(item);
        return {
          code: text(x.code, 100),
          message: text(x.message),
          review_href: nullable(x.review_href, (item) => option(item, hrefs)),
        };
      },
      20,
    ),
    question: nullable(v.question, (item) => {
      const x = record(item);
      return {
        code: text(x.code, 100),
        text: text(x.text, 1000),
        review_href: option(x.review_href, hrefs),
      };
    }),
    basis: "saved_records_and_explicit_reports_only",
    automatic_rescheduling: false,
    attendance_sleep_grades_observed: false,
  };
  if (
    row.local_date !== studentDateInZone(row.server_now, row.timezone) ||
    Date.parse(row.server_now) < Date.parse(row.window.starts_at) ||
    Date.parse(row.server_now) >= Date.parse(row.window.ends_at) ||
    Date.parse(row.outlook.window.starts_at) !==
      Date.parse(row.window.ends_at) ||
    Date.parse(row.window.observed_through) !== Date.parse(row.server_now) ||
    !sameWindow(row.window, row.schedule.window) ||
    !sameWindow(row.window, row.work.window) ||
    !sameWindow(row.window, row.outcomes.window) ||
    Date.parse(row.outlook.window.observed_through) !==
      Date.parse(row.server_now) ||
    row.goals.items.some((goal) => goal.progress.timezone !== row.timezone)
  )
    return invalid();
  return row;
}
export function dayReviewLinkAllowed(
  href: DayReviewHref,
  links: DayReviewLinks,
) {
  return href === "/planner"
    ? links.planning
    : href === "/goals"
      ? links.goals
      : href === "/email-actions"
        ? links.email
        : true;
}
/** Expire at an upcoming saved boundary or local midnight; never predict a live new fact. */
export function dayReviewMilliseconds(view: StudentDayReviewView): number {
  const now = Date.parse(view.server_now),
    limits = [5 * 60_000];
  for (const item of [...view.schedule.classes, ...view.schedule.fixed_blocks])
    for (const instant of [item.start_at, item.end_at]) {
      const delay = Date.parse(instant) - now;
      if (delay > 0) limits.push(delay);
    }
  for (const task of view.work.items) {
    const delay = task.due_at ? Date.parse(task.due_at) - now : 0;
    if (delay > 0) limits.push(delay);
  }
  const nextDay = goalShiftDay(view.local_date, 1);
  for (let minute = 0; minute < 180; minute++) {
    const time = `${Math.floor(minute / 60)
        .toString()
        .padStart(2, "0")}:${(minute % 60).toString().padStart(2, "0")}`,
      options = studyDeadlineChoices(`${nextDay}T${time}`, view.timezone);
    if (options.length) {
      limits.push(Date.parse(options[0]) - now);
      break;
    }
  }
  return Math.max(0, Math.min(...limits));
}
export class DayReviewError extends Error {
  constructor(
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "DayReviewError";
  }
}
const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export const studentDayReviewApi: DayReviewApi = {
  async account(signal) {
    const response = await fetch(`${apiBaseUrl}/auth/me`, {
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
    });
    if (!response.ok)
      throw new DayReviewError(
        "Sign in again before opening private reviews.",
        response.status,
      );
    const v = record(await response.json());
    return `${id(record(v.workspace).id)}:${id(v.id)}`;
  },
  async review(kind, signal) {
    option(kind, ["morning", "evening", "weekly"] as const);
    const response = await fetch(
      `${apiBaseUrl}/student/coach/review?kind=${kind}`,
      {
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]),
      },
    );
    if (!response.ok)
      throw new DayReviewError(
        response.status === 404
          ? "Day reviews are not available here yet."
          : response.status === 401 || response.status === 403
            ? "Your access to this private review could not be checked."
            : "The day review is unavailable. Try a fresh review later.",
        response.status,
      );
    return readStudentDayReview(await response.json());
  },
};
export function initialDayReviewState(): DayReviewState {
  return {
    kind: "morning",
    review: null,
    busy: false,
    masked: false,
    blocked: false,
    error: "",
    notice: "Choose a review and open it when you’re ready.",
    epoch: 0,
  };
}
export class StudentDayReviewController {
  private state = initialDayReviewState();
  private listeners = new Set<() => void>();
  private generation = 0;
  private request: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  constructor(
    readonly accountId: string,
    private api: DayReviewApi = studentDayReviewApi,
    private now: () => number = () => performance.now(),
  ) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private update(change: Partial<DayReviewState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...change };
    for (const listener of this.listeners) listener();
  }
  clear = (
    notice = "The review was cleared. Open a fresh review when ready.",
  ) => {
    ++this.generation;
    this.request?.abort();
    this.request = null;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const kind = this.state.kind;
    this.update({
      ...initialDayReviewState(),
      kind,
      notice,
      epoch: this.state.epoch + 1,
    });
  };
  dispose = () => {
    this.clear();
    this.disposed = true;
    this.listeners.clear();
  };
  choose = (kind: DayReviewKind) => {
    if (this.state.busy || this.state.masked || this.state.blocked) return;
    option(kind, ["morning", "evening", "weekly"] as const);
    this.clear("Open this review when you’re ready.");
    this.update({ kind });
  };
  private current(token: number, signal: AbortSignal) {
    return !this.disposed && token === this.generation && !signal.aborted;
  }
  open = async () => {
    if (
      this.disposed ||
      this.state.busy ||
      this.state.masked ||
      this.state.blocked
    )
      return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const token = ++this.generation,
      request = new AbortController(),
      kind = this.state.kind,
      start = this.now();
    this.request = request;
    this.update({
      review: null,
      busy: true,
      error: "",
      notice: "Opening your saved records…",
    });
    let checked = false;
    try {
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new Error("Account changed");
      checked = true;
      if (!this.current(token, request.signal)) return;
      let review: StudentDayReviewView;
      try {
        review = await this.api.review(kind, request.signal);
      } catch (cause) {
        checked = false;
        if (!this.current(token, request.signal)) return;
        if ((await this.api.account(request.signal)) !== this.accountId)
          throw new Error("Account changed");
        checked = true;
        throw cause;
      }
      checked = false;
      if (!this.current(token, request.signal)) return;
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new Error("Account changed");
      checked = true;
      if (!this.current(token, request.signal)) return;
      if (review.kind !== kind)
        throw new DayReviewError(
          "The review type changed. Open a fresh review.",
        );
      const lifetime = dayReviewMilliseconds(review) - (this.now() - start);
      if (lifetime <= 0) {
        this.clear("The saved snapshot needs refreshing. Open a fresh review.");
        return;
      }
      this.timer = setTimeout(
        () =>
          this.clear(
            "The saved snapshot needs refreshing. Open a fresh review.",
          ),
        lifetime,
      );
      this.update({
        review,
        notice: "Saved records, as of the time shown below.",
        epoch: this.state.epoch + 1,
      });
    } catch (cause) {
      if (!this.current(token, request.signal)) return;
      this.clear(
        checked
          ? "Open a fresh review when you’re ready."
          : "Your account could not be checked. The private review was cleared.",
      );
      this.update({
        blocked:
          !checked ||
          (cause instanceof DayReviewError &&
            [401, 403].includes(cause.status ?? 0)),
        error: !checked
          ? "Reload this page after signing in to the intended account."
          : cause instanceof DayReviewError
            ? cause.message
            : "The review could not be checked. Open a fresh review later.",
      });
    } finally {
      if (this.current(token, request.signal)) {
        this.request = null;
        this.update({ busy: false });
      }
    }
  };
  suspend = () => {
    if (this.disposed) return;
    ++this.generation;
    this.request?.abort();
    this.request = null;
    this.update({ masked: true, busy: false, error: "" });
  };
  resume = async () => {
    if (this.disposed || !this.state.masked || this.state.busy) return;
    const token = ++this.generation,
      request = new AbortController();
    this.request = request;
    this.update({ busy: true });
    try {
      const owner = await this.api.account(request.signal);
      if (!this.current(token, request.signal)) return;
      if (owner !== this.accountId) throw new Error("Account changed");
      this.clear("Open a fresh review when you’re ready.");
    } catch {
      if (!this.current(token, request.signal)) return;
      this.clear(
        "Your account could not be checked. The private review was cleared.",
      );
      this.update({
        blocked: true,
        error: "Reload this page after signing in to the intended account.",
      });
    }
  };
}
export function observeDayReviewPrivacy(
  clear: () => void,
  suspend: () => void,
  resume: () => void,
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
) {
  let focused = surface.document.hasFocus?.() ?? true;
  const refocus = () => {
      if (focused && surface.document.visibilityState === "visible") resume();
    },
    blur = () => {
      focused = false;
      suspend();
    },
    focus = () => {
      focused = true;
      refocus();
    },
    visibility = () => {
      if (surface.document.visibilityState !== "visible") suspend();
      else refocus();
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
