/** Phase 2 shared text/voice brain. Models propose; this registry alone dispatches. */
import { createHash, randomUUID } from "node:crypto";
import { AssistantError } from "./errors";
import manifest from "./student-tools-manifest.json";
import {
  answerStudentLookup,
  parseStudentLookup,
  type StudentLookupRequest,
  studentLookupRequest,
} from "./student-voice";
import { isRecord, isUuid } from "./validate";

export const STUDENT_AGENT_LIMITS = Object.freeze({
  steps: 4,
  toolCalls: 4,
  invalidReplans: 1,
  turnMs: 10_000,
  questionChars: 500,
  toolBytes: 8192,
  resultBytes: 32_768,
  modelContextBytes: 32_768,
  totalReservedUsd: 0.02,
  selectionReservedUsd: 0.005,
});
export const STUDENT_TOOL_NAMES = [
  "clock.read",
  "classes.lookup",
  "course.info",
  "tasks.read",
  "plan.preview",
  "feeds.read",
  "feed.refresh_review",
  "course.materials.search",
  "course.materials.page",
] as const;
export type StudentToolName = (typeof STUDENT_TOOL_NAMES)[number];
export type StudentMetadataToolName = Exclude<
  StudentToolName,
  "course.materials.search" | "course.materials.page"
>;
export type StudentToolEffect = "read" | "review";
export interface StudentAgentScope {
  user_id: string;
  workspace_id: string;
  personal: boolean;
  paused: boolean;
}
export interface StudentToolCall {
  call_id: string;
  name: StudentToolName;
  args: Record<string, unknown>;
}
export interface StudentToolObservation {
  call_id: string;
  name: StudentToolName;
  effect: StudentToolEffect;
  data: Record<string, unknown>;
}
export interface StudentToolResult extends StudentToolObservation {
  summary: string;
}
export type StudentToolSelection =
  | { kind: "tool"; name: StudentToolName; args: Record<string, unknown> }
  | { kind: "finish"; evidence_call_ids: string[] }
  | { kind: "clarify"; question: string };

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (isRecord(value))
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`)
      .join(",")}}`;
  return JSON.stringify(value);
}
export const STUDENT_TOOL_MANIFEST_JSON = canonical(manifest);
export const STUDENT_TOOL_MANIFEST_HASH = createHash("sha256")
  .update(STUDENT_TOOL_MANIFEST_JSON)
  .digest("hex");

export class StudentToolError extends AssistantError {
  constructor(
    public readonly reasonCode:
      | "unknown_tool"
      | "invalid_args"
      | "unverified_result"
      | "scope_changed"
      | "budget"
      | "model_unconfigured"
      | "review_required",
    message: string,
  ) {
    super(
      reasonCode === "scope_changed"
        ? "forbidden"
        : reasonCode === "budget" || reasonCode === "model_unconfigured"
          ? "unavailable"
          : "invalid_request",
      message,
    );
  }
}
function invalidArgs(): never {
  throw new StudentToolError(
    "invalid_args",
    "That tool request needs more precise details. Nothing was changed.",
  );
}
function invalidResult(): never {
  throw new StudentToolError(
    "unverified_result",
    "The student tool returned data that could not be verified.",
  );
}
function exact(
  value: unknown,
  allowed: readonly string[],
): Record<string, unknown> {
  if (
    !isRecord(value) ||
    Object.keys(value).some((key) => !allowed.includes(key))
  )
    invalidArgs();
  return value;
}
function str(value: unknown, max: number, empty = false): string {
  if (
    typeof value !== "string" ||
    value.length > max ||
    (!empty && !value.trim()) ||
    Array.from(value).some(
      (char) => char.charCodeAt(0) < 32 && !"\t\n\r".includes(char),
    )
  )
    invalidResult();
  return value;
}
function id(value: unknown): string {
  if (!isUuid(value)) invalidResult();
  return value;
}
function integer(value: unknown, min: number, max: number): number {
  if (
    !Number.isSafeInteger(value) ||
    (value as number) < min ||
    (value as number) > max
  )
    invalidResult();
  return value as number;
}
function timestamp(value: unknown): string {
  const result = str(value, 64);
  if (
    !/(?:Z|[+-]\d{2}:\d{2})$/i.test(result) ||
    !Number.isFinite(Date.parse(result))
  )
    invalidResult();
  return result;
}
function isoDate(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(`${value}T00:00:00Z`)) &&
    new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value
  );
}
function day(value: unknown): string {
  if (!isoDate(value)) invalidResult();
  return value;
}
function boolean(value: unknown): boolean {
  if (typeof value !== "boolean") invalidResult();
  return value;
}
function planResult(value: unknown) {
  if (
    !isRecord(value) ||
    value.planning_only !== true ||
    !Array.isArray(value.warnings) ||
    value.warnings.length > 30
  )
    invalidResult();
  return {
    start_at: timestamp(value.start_at),
    end_at: timestamp(value.end_at),
    planning_only: true,
    blocks: rawRows(value, "blocks", 600).map((row) => ({
      task_id: id(row.task_id),
      title: str(row.title, 200),
      start_at: timestamp(row.start_at),
      end_at: timestamp(row.end_at),
      minutes: integer(row.minutes, 0, 10080),
      focus_window: boolean(row.focus_window),
    })),
    unfitted_work: rawRows(value, "unfitted_work", 200).map((row) => {
      if (
        ![
          "deadline_passed",
          "cannot_meet_deadline",
          "outside_horizon",
          "insufficient_time",
        ].includes(String(row.reason))
      )
        invalidResult();
      return {
        task_id: id(row.task_id),
        title: str(row.title, 200),
        minutes: integer(row.minutes, 0, 10080),
        due_at: row.due_at === null ? null : timestamp(row.due_at),
        reason: row.reason,
      };
    }),
    capacities: rawRows(value, "capacities", 14).map((row) => ({
      day: day(row.day),
      free_minutes: integer(row.free_minutes, 0, 10080),
      buffer_minutes: integer(row.buffer_minutes, 0, 10080),
      planned_minutes: integer(row.planned_minutes, 0, 10080),
    })),
    warnings: value.warnings.map((warning) => str(warning, 500)),
  };
}
function shortLabel(value: string, maximum = 120): string {
  return value.length <= maximum
    ? value
    : `${value.slice(0, maximum - 1).replace(/[\uD800-\uDBFF]$/u, "")}…`;
}
function summaryWithin(value: string, maximum = 4000): string {
  if (value.length <= maximum) return value;
  const note = "\nMore details are available on the linked student screen.";
  const prefix = value.slice(0, maximum - note.length - 1);
  const line = prefix.lastIndexOf("\n");
  const kept = line > prefix.length / 2 ? prefix.slice(0, line) : prefix;
  return `${kept.replace(/[\uD800-\uDBFF]$/u, "")}…${note}`;
}
function calendarDate(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(`${value}T00:00:00Z`));
}
function previewTime(start: string, end: string): string {
  // Preview results provide instants, not an authoritative student timezone.
  const first = new Date(start).toISOString().slice(0, 16);
  const last = new Date(end).toISOString().slice(0, 16);
  return `${first.replace("T", " ")}–${first.slice(0, 10) === last.slice(0, 10) ? last.slice(11) : last.replace("T", " ")} UTC`;
}
function planSummary(result: ReturnType<typeof planResult>): string {
  const reasons: Record<string, string> = {
    deadline_passed: "the deadline has passed",
    cannot_meet_deadline: "the available time cannot meet the deadline",
    outside_horizon: "outside this preview's date range",
    insufficient_time: "not enough available time",
  };
  const lines = [
    `Plan preview: ${result.blocks.length} proposed blocks, ${result.unfitted_work.length} unfitted tasks, ${result.warnings.length} warnings. Review in Planner before saving. No plan or calendar changes have been applied.`,
  ];
  if (!result.blocks.length) lines.push("No proposed study blocks.");
  for (const block of result.blocks.slice(0, 3))
    lines.push(
      `${shortLabel(block.title)}: ${block.minutes} minutes, ${previewTime(block.start_at, block.end_at)}${block.focus_window ? "; in a focus window" : ""}.`,
    );
  if (result.blocks.length > 3)
    lines.push(`${result.blocks.length - 3} more proposed blocks in Planner.`);
  for (const work of result.unfitted_work.slice(0, 3))
    lines.push(
      `Unfitted — ${shortLabel(work.title)}: ${work.minutes} minutes; ${reasons[String(work.reason)]}${work.due_at ? `; due ${new Date(work.due_at).toISOString().replace("T", " ").slice(0, 16)} UTC` : ""}.`,
    );
  if (result.unfitted_work.length > 3)
    lines.push(
      `${result.unfitted_work.length - 3} more unfitted tasks in Planner.`,
    );
  for (const capacity of result.capacities.slice(0, 3))
    lines.push(
      `${calendarDate(capacity.day)}: ${capacity.free_minutes} free minutes, ${capacity.planned_minutes} planned, ${capacity.buffer_minutes} buffer minutes.`,
    );
  if (result.capacities.length > 3)
    lines.push(`${result.capacities.length - 3} more days in Planner.`);
  for (const warning of result.warnings.slice(0, 2))
    lines.push(`Warning: ${shortLabel(warning, 300)}`);
  if (result.warnings.length > 2)
    lines.push(`${result.warnings.length - 2} more warnings in Planner.`);
  return summaryWithin(lines.join("\n"));
}
function optionalId(args: Record<string, unknown>, key: string) {
  if (args[key] !== undefined && !isUuid(args[key])) invalidArgs();
}
function bytes(value: unknown): number {
  return Buffer.byteLength(JSON.stringify(value), "utf8");
}
function knownName(value: unknown): value is StudentToolName {
  return (
    typeof value === "string" &&
    (STUDENT_TOOL_NAMES as readonly string[]).includes(value)
  );
}

/** Extra authority fields, caller scopes, destinations, tokens and loose coercions are rejected. */
export function parseStudentToolArgs(
  name: StudentToolName,
  value: unknown,
): Record<string, unknown> {
  let args: Record<string, unknown>;
  switch (name) {
    case "clock.read":
    case "feeds.read":
      return exact(value, []);
    case "classes.lookup":
      args = exact(value, ["mode", "date", "time", "day_offset", "weekday"]);
      if (!["now", "next", "at", "day"].includes(String(args.mode)))
        invalidArgs();
      if (args.date !== undefined && !isoDate(args.date)) invalidArgs();
      if (
        args.time !== undefined &&
        (typeof args.time !== "string" ||
          !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(args.time))
      )
        invalidArgs();
      for (const key of ["day_offset", "weekday"])
        if (
          args[key] !== undefined &&
          (!Number.isSafeInteger(args[key]) ||
            Number(args[key]) < 0 ||
            Number(args[key]) > 6)
        )
          invalidArgs();
      if (
        [args.date, args.day_offset, args.weekday].filter(
          (item) => item !== undefined,
        ).length > 1 ||
        (args.mode === "at" && args.time === undefined) ||
        (["now", "next"].includes(String(args.mode)) &&
          Object.keys(args).length !== 1) ||
        (args.mode === "day" && args.time !== undefined)
      )
        invalidArgs();
      return { ...args };
    case "course.info":
      args = exact(value, ["course_id", "code"]);
      optionalId(args, "course_id");
      if (
        args.code !== undefined &&
        (typeof args.code !== "string" ||
          !args.code.trim() ||
          args.code.length > 40)
      )
        invalidArgs();
      if (args.course_id !== undefined && args.code !== undefined)
        invalidArgs();
      return { ...args };
    case "tasks.read":
      args = exact(value, ["course_id", "status", "limit"]);
      optionalId(args, "course_id");
      if (
        args.status !== undefined &&
        !["active", "completed", "archived", "all"].includes(
          String(args.status),
        )
      )
        invalidArgs();
      if (
        args.limit !== undefined &&
        (!Number.isSafeInteger(args.limit) ||
          Number(args.limit) < 1 ||
          Number(args.limit) > 12)
      )
        invalidArgs();
      return { status: "active", limit: 12, ...args };
    case "plan.preview":
      args = exact(value, ["date", "days"]);
      if (args.date !== undefined && !isoDate(args.date)) invalidArgs();
      if (
        args.days !== undefined &&
        (!Number.isSafeInteger(args.days) ||
          Number(args.days) < 1 ||
          Number(args.days) > 7)
      )
        invalidArgs();
      return { days: 1, ...args };
    case "feed.refresh_review":
      args = exact(value, ["feed_id"]);
      if (!isUuid(args.feed_id)) invalidArgs();
      return { ...args };
    case "course.materials.search": {
      args = exact(value, ["course_id", "query"]);
      if (
        !isUuid(args.course_id) ||
        typeof args.query !== "string" ||
        !args.query.trim() ||
        args.query.length > 100 ||
        Array.from(args.query).some((character) => character.charCodeAt(0) < 32)
      )
        invalidArgs();
      const words = args.query.match(/[\p{L}\p{N}_]+/gu) ?? [];
      if (!words.length || words.length > 8) invalidArgs();
      return { course_id: args.course_id.toLowerCase(), query: args.query };
    }
    case "course.materials.page":
      args = exact(value, ["course_id", "material_id", "revision", "page"]);
      if (
        !isUuid(args.course_id) ||
        !isUuid(args.material_id) ||
        !Number.isSafeInteger(args.revision) ||
        Number(args.revision) < 1 ||
        Number(args.revision) > 2_147_483_647 ||
        !Number.isSafeInteger(args.page) ||
        Number(args.page) < 1 ||
        Number(args.page) > 100
      )
        invalidArgs();
      return {
        course_id: args.course_id.toLowerCase(),
        material_id: args.material_id.toLowerCase(),
        revision: args.revision,
        page: args.page,
      };
  }
}

export function parseStudentToolSelection(
  value: unknown,
  observations: readonly StudentToolObservation[],
): StudentToolSelection {
  if (!isRecord(value)) invalidArgs();
  if (value.kind === "tool") {
    exact(value, ["kind", "name", "args"]);
    if (!knownName(value.name))
      throw new StudentToolError(
        "unknown_tool",
        "The model named a tool NavoX does not provide. Nothing was changed.",
      );
    return {
      kind: "tool",
      name: value.name,
      args: parseStudentToolArgs(value.name, value.args),
    };
  }
  if (value.kind === "finish") {
    exact(value, ["kind", "evidence_call_ids"]);
    const identifiers = value.evidence_call_ids;
    if (
      !Array.isArray(identifiers) ||
      !identifiers.length ||
      identifiers.length > 4 ||
      identifiers.some(
        (item) =>
          !isUuid(item) ||
          !observations.some((observation) => observation.call_id === item),
      ) ||
      new Set(identifiers).size !== identifiers.length
    )
      invalidArgs();
    return { kind: "finish", evidence_call_ids: identifiers as string[] };
  }
  if (value.kind === "clarify") {
    exact(value, ["kind", "question"]);
    return { kind: "clarify", question: str(value.question, 240) };
  }
  invalidArgs();
}

export interface StudentSelectorInput {
  session_id: string;
  turn_id: string;
  step: number;
  question: string;
  manifest_hash: string;
  observations: readonly (Omit<StudentToolObservation, "name"> & {
    name: StudentMetadataToolName;
  })[];
  previous_error: "invalid_selection" | null;
  max_cost: "0.005";
}
export interface StudentToolUpstream {
  authorize(signal: AbortSignal): Promise<StudentAgentScope>;
  clock(signal: AbortSignal): Promise<unknown>;
  lookup(args: StudentLookupRequest, signal: AbortSignal): Promise<unknown>;
  courses(signal: AbortSignal): Promise<unknown>;
  tasks(signal: AbortSignal): Promise<unknown>;
  preview(
    args: { date?: string; days: number },
    signal: AbortSignal,
  ): Promise<unknown>;
  feeds(signal: AbortSignal): Promise<unknown>;
  materialSearch?(
    args: { course_id: string; query: string },
    signal: AbortSignal,
  ): Promise<unknown>;
  materials?(courseId: string, signal: AbortSignal): Promise<unknown>;
  materialPage?(
    args: {
      course_id: string;
      material_id: string;
      revision: number;
      page: number;
    },
    signal: AbortSignal,
  ): Promise<unknown>;
  select(input: StudentSelectorInput, signal: AbortSignal): Promise<unknown>;
  converse(
    input: { session_id: string; question: string; recent_turns: readonly [] },
    signal: AbortSignal,
  ): Promise<string>;
}

function sameScope(a: StudentAgentScope, b: StudentAgentScope) {
  return (
    a.user_id === b.user_id && a.workspace_id === b.workspace_id && b.personal
  );
}
export function requireStudentScope(
  value: StudentAgentScope,
  expected?: StudentAgentScope,
): StudentAgentScope {
  if (
    !isUuid(value.user_id) ||
    !isUuid(value.workspace_id) ||
    !value.personal ||
    typeof value.paused !== "boolean" ||
    (expected && !sameScope(expected, value))
  )
    throw new StudentToolError(
      "scope_changed",
      "Your account or workspace changed. Open a new conversation.",
    );
  return value;
}
function rawRows(
  value: unknown,
  key: string,
  maximum: number,
): Record<string, unknown>[] {
  if (
    !isRecord(value) ||
    !Array.isArray(value[key]) ||
    value[key].length > maximum ||
    value[key].some((row) => !isRecord(row))
  )
    invalidResult();
  return value[key] as Record<string, unknown>[];
}
function feedsFrom(value: unknown) {
  return rawRows(value, "feeds", 20)
    .filter((row) => row.state !== "preview")
    .map((row) => {
      const domain = str(row.domain, 253);
      if (row.state !== "active" && row.state !== "paused") invalidResult();
      if (!/^[a-z0-9.-]+$/i.test(domain)) invalidResult();
      return {
        id: id(row.id),
        name: str(row.name, 160),
        domain,
        state: str(row.state, 24),
        revision: integer(row.revision, 1, 2_147_483_647),
        last_synced_at:
          row.last_synced_at === null ? null : timestamp(row.last_synced_at),
      };
    });
}

function materialTool(name: StudentToolName): boolean {
  return name === "course.materials.search" || name === "course.materials.page";
}
function clipUtf8(value: string, maximum: number): string {
  let result = "";
  let length = 0;
  for (const character of value) {
    const next = Buffer.byteLength(character, "utf8");
    if (length + next > maximum) break;
    result += character;
    length += next;
  }
  return result;
}
async function materialIndex(
  upstream: StudentToolUpstream,
  courseId: string,
  signal: AbortSignal,
) {
  if (!upstream.materials)
    throw new StudentToolError(
      "model_unconfigured",
      "Course material lookup is unavailable.",
    );
  return rawRows(
    await upstream.materials(courseId, signal),
    "materials",
    40,
  ).map((row) => ({
    id: id(row.id),
    revision: integer(row.revision, 1, 2_147_483_647),
    title: str(row.title, 160),
    page_count: integer(row.page_count, 1, 100),
  }));
}

/** A proposed file read must be tied to this question, never guessed from an identifier. */
function materialSelectionMatchesQuestion(
  selection: Extract<StudentToolSelection, { kind: "tool" }>,
  question: string,
  courses: readonly Record<string, unknown>[],
): void {
  const args = selection.args;
  const lower = question.toLowerCase();
  let courseSelected = lower.includes(String(args.course_id).toLowerCase());
  const namedCourses = new Set<string>();
  const escaped = (value: string) =>
    value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  for (const course of courses) {
    for (const label of [course.code, course.name])
      if (
        typeof label === "string" &&
        label.trim() &&
        new RegExp(
          `(?:^|[^\\p{L}\\p{N}_])${label.trim().split(/\s+/).map(escaped).join("\\s*")}(?![\\p{L}\\p{N}_])`,
          "iu",
        ).test(question)
      )
        namedCourses.add(id(course.id));
  }
  courseSelected =
    courseSelected ||
    (namedCourses.size === 1 && namedCourses.has(String(args.course_id)));
  if (!courseSelected)
    throw new StudentToolError(
      "invalid_args",
      "Which saved course should I search?",
    );
  if (selection.name === "course.materials.search") {
    const words =
      String(args.query)
        .toLowerCase()
        .match(/[\p{L}\p{N}_]+/gu) ?? [];
    const questionWords = new Set(lower.match(/[\p{L}\p{N}_]+/gu) ?? []);
    if (words.some((word) => !questionWords.has(word)))
      throw new StudentToolError(
        "invalid_args",
        "Which literal words should I find in that course?",
      );
  } else if (
    !lower.includes(String(args.material_id).toLowerCase()) ||
    !new RegExp(`\\bpage\\s+${args.page}\\b`, "i").test(question) ||
    !new RegExp(`\\b(?:revision|version)\\s+${args.revision}\\b`, "i").test(
      question,
    )
  )
    throw new StudentToolError(
      "invalid_args",
      "Choose the exact material, version and page first.",
    );
}

/** No tool accepts an approval; review tools create proposals, never saved plans or external changes. */
export async function executeStudentTool(
  upstream: StudentToolUpstream,
  expectedScope: StudentAgentScope,
  call: StudentToolCall,
  signal: AbortSignal,
): Promise<StudentToolResult> {
  if (!knownName(call.name) || !isUuid(call.call_id)) invalidArgs();
  const args = parseStudentToolArgs(call.name, call.args);
  requireStudentScope(await upstream.authorize(signal), expectedScope);
  let data: Record<string, unknown>;
  let summary: string;
  let effect: StudentToolEffect = "read";
  switch (call.name) {
    case "clock.read": {
      const value = await upstream.clock(signal);
      if (!isRecord(value)) invalidResult();
      const instant = timestamp(value.server_now);
      const timezone = str(value.timezone, 100);
      let clock: string;
      try {
        clock = new Intl.DateTimeFormat("en-US", {
          timeZone: timezone,
          hour: "numeric",
          minute: "2-digit",
        }).format(new Date(instant));
      } catch {
        invalidResult();
      }
      data = { server_now: instant, timezone };
      summary = `It is ${clock} in your saved timezone.`;
      break;
    }
    case "classes.lookup": {
      const request = args as unknown as StudentLookupRequest;
      const snapshot = parseStudentLookup(
        await upstream.lookup(request, signal),
        request,
      );
      data = { ...snapshot };
      summary = answerStudentLookup(snapshot).text;
      break;
    }
    case "course.info": {
      const owned = rawRows(await upstream.courses(signal), "courses", 60).map(
        (row) => {
          const termStart = day(row.term_start);
          const termEnd = day(row.term_end);
          if (termEnd < termStart) invalidResult();
          return {
            id: id(row.id),
            code: str(row.code, 40, true),
            name: str(row.name, 200),
            professor: row.professor === null ? null : str(row.professor, 120),
            term_start: termStart,
            term_end: termEnd,
          };
        },
      );
      const matching = owned.filter(
        (row) =>
          (!args.course_id || row.id === args.course_id) &&
          (!args.code ||
            row.code.toLowerCase().replace(/\s/g, "") ===
              String(args.code).toLowerCase().replace(/\s/g, "")),
      );
      if ((args.course_id || args.code) && !matching.length)
        throw new StudentToolError(
          "invalid_args",
          "That course is not in your saved timetable.",
        );
      data = {
        courses: matching.slice(0, 10),
        truncated: matching.length > 10,
      };
      summary = matching.length
        ? summaryWithin(
            matching
              .slice(0, 10)
              .map(
                (row) =>
                  `${row.code ? `${row.code} — ` : ""}${row.name}. Professor: ${row.professor ?? "not recorded"}. Term: ${calendarDate(row.term_start)} to ${calendarDate(row.term_end)}.`,
              )
              .concat(
                matching.length > 10
                  ? [`${matching.length - 10} more saved courses in Classes.`]
                  : [],
              )
              .join("\n"),
          )
        : "You have no saved courses yet.";
      break;
    }
    case "tasks.read": {
      const owned = rawRows(await upstream.tasks(signal), "tasks", 200).map(
        (row) => {
          const status = str(row.status, 16);
          if (!["active", "completed", "archived"].includes(status))
            invalidResult();
          return {
            id: id(row.id),
            title: str(row.title, 200),
            course_id: row.course_id === null ? null : id(row.course_id),
            status,
            due_at: row.due_at === null ? null : timestamp(row.due_at),
            remaining_minutes: integer(row.remaining_minutes, 0, 10080),
          };
        },
      );
      const matching = owned.filter(
        (row) =>
          (!args.course_id || row.course_id === args.course_id) &&
          (args.status === "all" || row.status === args.status),
      );
      const selected = matching.slice(0, Number(args.limit));
      data = { tasks: selected, truncated: matching.length > selected.length };
      summary = selected.length
        ? selected
            .map(
              (row) =>
                `${row.title}: ${row.remaining_minutes} minutes left${row.due_at ? `, due ${row.due_at}` : ", no deadline set"}`,
            )
            .join("; ")
        : "No saved tasks match that request.";
      break;
    }
    case "plan.preview": {
      const preview = await upstream.preview(
        {
          days: Number(args.days),
          ...(typeof args.date === "string" ? { date: args.date } : {}),
        },
        signal,
      );
      if (
        !isRecord(preview) ||
        preview.requires_acceptance !== true ||
        !isUuid(preview.preview_id) ||
        typeof preview.preview_hash !== "string" ||
        !/^[a-f0-9]{64}$/.test(preview.preview_hash) ||
        !isRecord(preview.result) ||
        preview.result.planning_only !== true ||
        !Array.isArray(preview.result.blocks) ||
        preview.result.blocks.length > 600
      )
        invalidResult();
      timestamp(preview.expires_at);
      integer(preview.base_revision, 0, 2_147_483_647);
      const result = planResult(preview.result);
      data = {
        preview_id: preview.preview_id,
        preview_hash: preview.preview_hash,
        expires_at: timestamp(preview.expires_at),
        base_revision: integer(preview.base_revision, 0, 2_147_483_647),
        result,
        requires_acceptance: true,
      };
      effect = "review";
      summary = planSummary(result);
      break;
    }
    case "feeds.read": {
      const feeds = feedsFrom(await upstream.feeds(signal));
      data = { feeds };
      summary = feeds.length
        ? feeds
            .map((row) => `${row.name} (${row.domain}): ${row.state}`)
            .join("; ")
        : "You have no connected learning calendar feeds.";
      break;
    }
    case "feed.refresh_review": {
      const feed = feedsFrom(await upstream.feeds(signal)).find(
        (row) => row.id === args.feed_id,
      );
      if (!feed)
        throw new StudentToolError(
          "invalid_args",
          "That feed is not connected to your account.",
        );
      data = {
        review_kind: "feed_refresh",
        feed_id: feed.id,
        domain: feed.domain,
        revision: feed.revision,
        requires_confirmation: true,
        refresh_queued: false,
      };
      effect = "review";
      summary = `Review refreshing ${feed.name} (${feed.domain}) in Connections. The feed has not been queued or fetched.`;
      break;
    }
    case "course.materials.search": {
      if (!upstream.materialSearch)
        throw new StudentToolError(
          "model_unconfigured",
          "Course material search is unavailable.",
        );
      const request = args as unknown as { course_id: string; query: string };
      const raw = await upstream.materialSearch(request, signal);
      if (
        !isRecord(raw) ||
        raw.course_id !== request.course_id ||
        typeof raw.limit_reached !== "boolean"
      )
        invalidResult();
      const rows = rawRows(raw, "results", 8);
      const index = await materialIndex(upstream, request.course_id, signal);
      const seen = new Set<string>();
      const matches = rows.slice(0, 5).map((row) => {
        const materialId = id(row.material_id);
        const revision = integer(row.revision, 1, 2_147_483_647);
        const page = integer(row.page, 1, 100);
        const title = str(row.title, 160);
        const current = index.find((item) => item.id === materialId);
        const key = `${materialId}:${page}`;
        if (
          !current ||
          current.revision !== revision ||
          current.title !== title ||
          page > current.page_count ||
          seen.has(key)
        )
          throw new StudentToolError(
            "unverified_result",
            "A source changed. Search the current course materials again.",
          );
        seen.add(key);
        return {
          material_id: materialId,
          revision,
          page,
          title: clipUtf8(title, 160),
          snippet: clipUtf8(str(row.snippet, 320), 256),
        };
      });
      data = {
        course_id: request.course_id,
        query: request.query,
        results: matches,
        limit_reached: raw.limit_reached || rows.length > matches.length,
      };
      summary = matches.length
        ? `Literal source excerpts from your saved course materials:\n\n${matches.map((row) => `${row.title} — page ${row.page}, version ${row.revision}\n${row.snippet}`).join("\n\n")}${data.limit_reached ? "\n\nOpen the course workspace for more matches." : ""}`
        : "No saved material page in this course contains all those words. Try fewer words or add the relevant notes.";
      break;
    }
    case "course.materials.page": {
      if (!upstream.materialPage)
        throw new StudentToolError(
          "model_unconfigured",
          "Course material pages are unavailable.",
        );
      const request = args as unknown as {
        course_id: string;
        material_id: string;
        revision: number;
        page: number;
      };
      const raw = await upstream.materialPage(request, signal);
      if (
        !isRecord(raw) ||
        raw.course_id !== request.course_id ||
        raw.material_id !== request.material_id ||
        raw.revision !== request.revision ||
        raw.page !== request.page ||
        typeof raw.truncated !== "boolean"
      )
        invalidResult();
      const text = str(raw.text, 2048, true);
      if (Buffer.byteLength(text, "utf8") > 2048) invalidResult();
      const title = str(raw.title, 160);
      const index = await materialIndex(upstream, request.course_id, signal);
      const current = index.find((item) => item.id === request.material_id);
      if (
        !current ||
        current.revision !== request.revision ||
        current.title !== title ||
        request.page > current.page_count
      )
        throw new StudentToolError(
          "unverified_result",
          "This source changed. Open its current version in the course workspace.",
        );
      data = {
        ...request,
        title: clipUtf8(title, 160),
        text,
        truncated: raw.truncated,
      };
      summary = `Literal source excerpt: ${data.title} — page ${request.page}, version ${request.revision}\n\n${text || "This page has no extracted text."}${raw.truncated ? "\n\nThis excerpt is shortened. Open the source page for the full text." : ""}`;
      break;
    }
  }
  // Read fresh authorization after slow reads and preview generation as well.
  requireStudentScope(await upstream.authorize(signal), expectedScope);
  const result = {
    call_id: call.call_id,
    name: call.name,
    effect,
    data,
    summary,
  };
  if (bytes(result) > STUDENT_AGENT_LIMITS.toolBytes)
    throw new StudentToolError(
      "budget",
      "This result is too large to review safely here. Open the student screen for the full details.",
    );
  return result;
}

export interface StudentAgentTurn {
  turn_id: string;
  session_id: string;
  question: string;
  modality: "TEXT" | "VOICE";
  allow_tool_context: boolean;
}
export interface StudentAgentAnswer {
  status: "answer" | "review" | "clarify" | "unavailable" | "limit";
  answer: string;
  observations: StudentToolResult[];
  actions_executed: false;
  transcript_saved: false;
  source: "deterministic" | "tools" | "question_only_conversation" | "none";
  metrics: {
    steps: number;
    tool_calls: number;
    model_attempts: number;
    reserved_cost_usd: string;
    elapsed_ms: number;
  };
}

/** A deterministic shortcut is an explicit full question; unrelated requests are never partly run. */
function directStudentTool(
  question: string,
): { name: StudentToolName; args: Record<string, unknown> } | null {
  const classes = studentLookupRequest(question);
  if (classes) return { name: "classes.lookup", args: { ...classes } };
  const normalized = question
    .trim()
    .toLowerCase()
    .replace(/[?!.]+$/, "")
    .replace(/[’']/g, "");
  if (
    /^(?:what(?: is|s) the time|what time is it|current time)$/.test(normalized)
  )
    return { name: "clock.read", args: {} };
  if (/^(?:show|list|read)(?: me)? my (?:saved )?tasks$/.test(normalized))
    return { name: "tasks.read", args: {} };
  if (/^(?:show|list|read)(?: me)? my (?:saved )?courses$/.test(normalized))
    return { name: "course.info", args: {} };
  if (
    /^(?:show|list)(?: me)? my (?:learning |calendar )?feeds$/.test(normalized)
  )
    return { name: "feeds.read", args: {} };
  if (
    /^(?:preview|show) (?:a |my )?(?:study |daily )?plan(?: for today)?$/.test(
      normalized,
    )
  )
    return { name: "plan.preview", args: { days: 1 } };
  return null;
}

/** Model tool context is a separate, default-off contract. Ordinary chat receives zero history or tool results. */
export function createStudentToolAgent(
  upstream: StudentToolUpstream,
  options: {
    modelToolsEnabled?: boolean;
    ordinaryConversationEnabled?: boolean;
    now?: () => number;
    newId?: () => string;
  } = {},
) {
  const now = options.now ?? Date.now;
  const newId = options.newId ?? randomUUID;
  return {
    async ask(
      input: StudentAgentTurn,
      parentSignal?: AbortSignal,
    ): Promise<StudentAgentAnswer> {
      if (
        !isUuid(input.turn_id) ||
        !isUuid(input.session_id) ||
        typeof input.question !== "string" ||
        !input.question.trim() ||
        input.question.length > 500 ||
        !["TEXT", "VOICE"].includes(input.modality) ||
        typeof input.allow_tool_context !== "boolean"
      )
        invalidArgs();
      const started = now();
      const controller = new AbortController();
      const abort = () => controller.abort();
      if (parentSignal?.aborted) controller.abort();
      parentSignal?.addEventListener("abort", abort, { once: true });
      let timer: ReturnType<typeof setTimeout> | undefined;
      let steps = 0;
      let modelAttempts = 0;
      let reserved = 0;
      let replans = 0;
      const results: StudentToolResult[] = [];
      const completedCalls = new Set<string>();
      let initialScope: StudentAgentScope | null = null;
      const deadline = new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(
            new StudentToolError(
              "budget",
              "This request reached its time limit. Try a smaller question.",
            ),
          );
        }, STUDENT_AGENT_LIMITS.turnMs);
        timer.unref?.();
      });
      const within = <T>(promise: Promise<T>): Promise<T> =>
        Promise.race([promise, deadline]);
      const result = (
        status: StudentAgentAnswer["status"],
        answer: string,
        source: StudentAgentAnswer["source"],
      ): StudentAgentAnswer => ({
        status,
        answer,
        observations: [...results],
        actions_executed: false,
        transcript_saved: false,
        source,
        metrics: {
          steps,
          tool_calls: completedCalls.size,
          model_attempts: modelAttempts,
          reserved_cost_usd: reserved.toFixed(3),
          elapsed_ms: Math.max(0, now() - started),
        },
      });
      try {
        const scope = requireStudentScope(
          await within(upstream.authorize(controller.signal)),
        );
        initialScope = scope;
        const run = async (
          name: StudentToolName,
          args: Record<string, unknown>,
        ) => {
          if (
            controller.signal.aborted ||
            now() - started >= STUDENT_AGENT_LIMITS.turnMs ||
            results.length >= STUDENT_AGENT_LIMITS.toolCalls
          )
            throw new StudentToolError(
              "budget",
              "This request reached its tool limit. Review the results already shown.",
            );
          const key = `${name}:${canonical(parseStudentToolArgs(name, args))}`;
          if (completedCalls.has(key))
            throw new StudentToolError(
              "budget",
              "The model repeated the same lookup. Ask a more specific question.",
            );
          const observation = await within(
            executeStudentTool(
              upstream,
              scope,
              { call_id: newId(), name, args },
              controller.signal,
            ),
          );
          if (
            bytes([...results, observation]) > STUDENT_AGENT_LIMITS.resultBytes
          )
            throw new StudentToolError(
              "budget",
              "This request returned too much data to review safely. Open the student screen for the full details.",
            );
          results.push(observation);
          completedCalls.add(key);
        };
        const direct = directStudentTool(input.question);
        if (direct) {
          await run(direct.name, direct.args);
          const last = results[0];
          return result(
            last.effect === "review" ? "review" : "answer",
            last.summary,
            "deterministic",
          );
        }
        if (!options.modelToolsEnabled || !input.allow_tool_context) {
          if (options.ordinaryConversationEnabled) {
            if (scope.paused)
              return result(
                "unavailable",
                "Resume assistance before asking a model. Your saved timetable remains available.",
                "none",
              );
            reserved = 0.01;
            modelAttempts = 1;
            const answer = str(
              await within(
                upstream.converse(
                  {
                    session_id: input.session_id,
                    question: input.question.trim(),
                    recent_turns: [],
                  },
                  controller.signal,
                ),
              ),
              3000,
            );
            requireStudentScope(
              await within(upstream.authorize(controller.signal)),
              scope,
            );
            return result("answer", answer, "question_only_conversation");
          }
          return result(
            "unavailable",
            "The student tool-calling model is not qualified or enabled yet. You can still ask about your current class, next class, courses or saved tasks.",
            "none",
          );
        }
        if (scope.paused)
          return result(
            "unavailable",
            "Resume assistance before asking the student tool-calling model.",
            "none",
          );
        let previousError: "invalid_selection" | null = null;
        while (steps < STUDENT_AGENT_LIMITS.steps) {
          const currentScope = requireStudentScope(
            await within(upstream.authorize(controller.signal)),
            scope,
          );
          if (currentScope.paused)
            return result(
              "unavailable",
              "Assistance was paused. Your request stopped before another model call.",
              "tools",
            );
          if (
            reserved + STUDENT_AGENT_LIMITS.selectionReservedUsd >
            STUDENT_AGENT_LIMITS.totalReservedUsd + 0.0000001
          )
            throw new StudentToolError(
              "budget",
              "This request reached its model budget.",
            );
          steps += 1;
          modelAttempts += 1;
          reserved += STUDENT_AGENT_LIMITS.selectionReservedUsd;
          const observations = results.map(
            ({ call_id, name, effect, data }) => {
              // File data is terminal user evidence, never selector context.
              if (
                name === "course.materials.search" ||
                name === "course.materials.page"
              )
                invalidResult();
              return { call_id, name, effect, data };
            },
          );
          const request: StudentSelectorInput = {
            session_id: input.session_id,
            turn_id: input.turn_id,
            step: steps,
            question: input.question.trim(),
            manifest_hash: STUDENT_TOOL_MANIFEST_HASH,
            observations,
            previous_error: previousError,
            max_cost: "0.005",
          };
          if (bytes(request) > STUDENT_AGENT_LIMITS.modelContextBytes)
            throw new StudentToolError(
              "budget",
              "The tool context reached its limit.",
            );
          let selection: StudentToolSelection;
          try {
            const raw = await within(
              upstream.select(request, controller.signal),
            );
            if (
              !isRecord(raw) ||
              Object.keys(raw).some(
                (key) =>
                  ![
                    "turn_id",
                    "step",
                    "manifest_hash",
                    "selection",
                    "task_id",
                    "trace_id",
                    "estimated_cost",
                    "actions_executed",
                  ].includes(key),
              ) ||
              raw.turn_id !== input.turn_id ||
              raw.step !== steps ||
              raw.manifest_hash !== STUDENT_TOOL_MANIFEST_HASH ||
              raw.actions_executed !== false ||
              !isUuid(raw.task_id) ||
              !isUuid(raw.trace_id) ||
              (raw.estimated_cost !== null &&
                ((typeof raw.estimated_cost !== "string" &&
                  typeof raw.estimated_cost !== "number") ||
                  !Number.isFinite(Number(raw.estimated_cost)) ||
                  Number(raw.estimated_cost) < 0 ||
                  Number(raw.estimated_cost) >
                    STUDENT_AGENT_LIMITS.selectionReservedUsd))
            )
              invalidArgs();
            selection = parseStudentToolSelection(raw.selection, observations);
            if (selection.kind === "tool" && materialTool(selection.name))
              materialSelectionMatchesQuestion(
                selection,
                input.question,
                rawRows(
                  await within(upstream.courses(controller.signal)),
                  "courses",
                  60,
                ),
              );
          } catch (error) {
            if (
              !(error instanceof StudentToolError) ||
              !["invalid_args", "unknown_tool"].includes(error.reasonCode) ||
              replans >= STUDENT_AGENT_LIMITS.invalidReplans
            )
              throw error;
            replans += 1;
            previousError = "invalid_selection";
            continue;
          }
          previousError = null;
          if (selection.kind === "clarify") {
            requireStudentScope(
              await within(upstream.authorize(controller.signal)),
              scope,
            );
            return result("clarify", selection.question, "tools");
          }
          if (selection.kind === "finish") {
            requireStudentScope(
              await within(upstream.authorize(controller.signal)),
              scope,
            );
            const selected = results.filter((row) =>
              selection.evidence_call_ids.includes(row.call_id),
            );
            const perSummary = Math.floor(
              (4000 - (selected.length - 1) * 2) / selected.length,
            );
            const fullSummary = selected.map((row) => row.summary).join("\n\n");
            const combined =
              fullSummary.length <= 4000
                ? fullSummary
                : selected
                    .map((row) => summaryWithin(row.summary, perSummary))
                    .join("\n\n");
            return result(
              selected.some((row) => row.effect === "review")
                ? "review"
                : "answer",
              combined,
              "tools",
            );
          }
          if (
            requireStudentScope(
              await within(upstream.authorize(controller.signal)),
              scope,
            ).paused
          )
            return result(
              "unavailable",
              "Assistance was paused. No further tools were run.",
              "tools",
            );
          await run(selection.name, selection.args);
          if (materialTool(selection.name))
            return result(
              "answer",
              results[results.length - 1].summary,
              "tools",
            );
        }
        return result(
          "limit",
          "The request reached its step limit. The verified results are shown for review; nothing was accepted or executed.",
          "tools",
        );
      } catch (error) {
        if (
          error instanceof StudentToolError &&
          error.reasonCode === "scope_changed"
        )
          throw error;
        if (
          error instanceof AssistantError &&
          (error.code === "unauthorized" || error.code === "forbidden")
        )
          throw error;
        if (initialScope && results.length) {
          try {
            requireStudentScope(
              await within(upstream.authorize(controller.signal)),
              initialScope,
            );
          } catch (authorizationError) {
            results.length = 0;
            if (
              authorizationError instanceof AssistantError &&
              (authorizationError.code === "unauthorized" ||
                authorizationError.code === "forbidden")
            )
              throw authorizationError;
          }
        }
        return result(
          error instanceof StudentToolError && error.reasonCode === "budget"
            ? "limit"
            : "unavailable",
          error instanceof AssistantError
            ? error.message
            : "This student request could not finish. Open the student screen or try a smaller question.",
          results.length ? "tools" : "none",
        );
      } finally {
        if (timer) clearTimeout(timer);
        controller.abort();
        parentSignal?.removeEventListener("abort", abort);
      }
    },
  };
}
