import type {
  AssistantBlock,
  AssistantResponseState,
  CapabilityDecision,
  PlannedIntent,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import { LIMITS } from "./limits";
import {
  isIanaTimezone,
  isRecord,
  isUuid,
  parseCapabilityDecision,
} from "./validate";

/**
 * The bounded personal Activity answer for SPEC-008 R3 scenario O.
 *
 * The source of truth is the existing authenticated SPEC-001/003 `GET /actions`
 * list, which is already scoped by workspace and user. This module never reads
 * an action payload, result, recipient, token or copied body text: it binds only
 * the minimal action type, provider, status and time facts.
 *
 * A completed action is described as something NavoX "did" only when the record
 * carries an independent verification timestamp from its owning service. Every
 * other record - executed but unverified, pending approval, in progress, failed,
 * declined, cancelled, expired, stale, blocked, uncertain or unrecognized - is
 * labeled accurately and never reported as done.
 */

/** The `/api/v1/actions` bound this runtime requests and reports against. */
export const ACTION_HISTORY_LIMIT = 50;
/**
 * An informational age threshold only. It never overrides a live ledger
 * status: a record that is still awaiting approval after a day is reported
 * with its own status and a non-authoritative age note.
 */
export const ACTION_HISTORY_AGE_NOTE_MS = 24 * 60 * 60 * 1000;
/** Forward-dated timestamps beyond this skew are treated as unreadable. */
const ACTION_HISTORY_CLOCK_SKEW_MS = 60_000;

/** Minimal facts bound from one action ledger row. No payload is retained. */
export interface ActionHistoryRecord {
  id: string;
  action_type: string;
  provider: string;
  status: string;
  created_at: string;
  executed_at: string | null;
  verified_at: string | null;
}

export type ActionHistoryOutcome =
  | "VERIFIED"
  | "EXECUTED_UNVERIFIED"
  | "PENDING_APPROVAL"
  | "IN_PROGRESS"
  | "FAILED"
  | "DECLINED"
  | "CANCELLED"
  | "EXPIRED"
  | "UNCERTAIN"
  | "BLOCKED"
  | "UNRECOGNIZED";

/** A stated day for the answer, or the unbounded recent list. */
export interface ActionHistoryScope {
  kind: "DAY" | "RECENT";
}

/** Statuses that have not reached a terminal outcome yet. */
const NON_TERMINAL_STATUSES = new Set([
  "pending",
  "awaiting_approval",
  "approved",
  "executing",
  "running",
]);

function unreadable(): never {
  throw new AssistantError(
    "unavailable",
    "The action ledger returned information this runtime could not verify.",
  );
}

function bounded(value: unknown, max: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > max)
    unreadable();
  return value;
}

function timestamp(value: unknown, now: Date): string {
  const text = bounded(value, 64);
  const parsed = Date.parse(text);
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(text) || !Number.isFinite(parsed))
    unreadable();
  if (parsed > now.getTime() + ACTION_HISTORY_CLOCK_SKEW_MS) unreadable();
  return text;
}

function optionalTimestamp(value: unknown, now: Date): string | null {
  if (value === null || value === undefined) return null;
  return timestamp(value, now);
}

/**
 * A scoped list must not name another user or workspace. The owning service
 * already fences the query, so a foreign scope field present on a row means the
 * response is not the one this session asked for.
 */
function rejectForeignScope(
  record: Record<string, unknown>,
  scope: { user_id: string; workspace_id: string },
): void {
  const expected: readonly [string, string][] = [
    ["user_id", scope.user_id],
    ["workspace_id", scope.workspace_id],
  ];
  for (const [key, value] of expected) {
    const observed = record[key];
    if (observed === undefined || observed === null) continue;
    if (
      typeof observed !== "string" ||
      observed.toLowerCase() !== value.toLowerCase()
    )
      unreadable();
  }
}

/**
 * Validates one `/api/v1/actions` response before anything is trusted. A
 * malformed, oversized, duplicated or foreign-scope row becomes a qualified
 * failure; the runtime never repairs it into a plausible answer.
 */
export function parseActionHistory(
  payload: unknown,
  input: {
    scope: { user_id: string; workspace_id: string };
    now: Date;
  },
): ActionHistoryRecord[] {
  if (!Array.isArray(payload) || payload.length > ACTION_HISTORY_LIMIT)
    unreadable();
  const seen = new Set<string>();
  return payload.map((item) => {
    if (!isRecord(item)) unreadable();
    rejectForeignScope(item, input.scope);
    if (!isUuid(item.id)) unreadable();
    if (seen.has(item.id)) unreadable();
    seen.add(item.id);
    const status = bounded(item.status, 32);
    const executedAt = optionalTimestamp(item.executed_at, input.now);
    const verifiedAt = optionalTimestamp(item.verified_at, input.now);
    // A verification timestamp is the owning service's proof that an
    // independent check completed. The existing action contracts only ever
    // write it on a completed row that already recorded its execution, so a
    // timestamp without that execution - or one that precedes it - is not
    // proof of anything and is refused rather than described as verified.
    if (verifiedAt !== null && status.toLowerCase() !== "completed")
      unreadable();
    if (status.toLowerCase() === "completed" && executedAt === null)
      unreadable();
    if (verifiedAt !== null) {
      if (executedAt === null) unreadable();
      if (Date.parse(executedAt) > Date.parse(verifiedAt)) unreadable();
    }
    return {
      id: item.id,
      action_type: bounded(item.action_type, 128),
      provider: bounded(item.provider, 32),
      status,
      created_at: timestamp(item.created_at, input.now),
      executed_at: executedAt,
      verified_at: verifiedAt,
    };
  });
}

/**
 * Resolves the requested scope from the operator's own words.
 *
 * `undefined` is the wrong route, `null` is an unsupported request that needs
 * clarification, and a scope is a resolvable read. Only a literal local day
 * ("today") and the unbounded recent list are supported; a named person or an
 * unresolved date is clarified rather than silently re-scoped.
 */
export function actionHistorySelector(
  intent: PlannedIntent,
): ActionHistoryScope | undefined | null {
  if (intent.kind !== "action.history") return undefined;
  if (intent.entity.kind !== "NONE") return null;
  if (intent.time.kind === "NONE") return { kind: "RECENT" };
  const expression = (intent.time.expression ?? "").trim().toLowerCase();
  if (expression === "today") return { kind: "DAY" };
  return null;
}

/**
 * The source-backed time an action happened, which is what a report's day
 * filter and provenance must use. A verified action happened when the owning
 * service verified it, a completed-but-unverified action when it executed, and
 * any other record at the latest transition the ledger recorded for it (its
 * creation time when it has no transition yet). A row prepared yesterday but
 * verified today therefore belongs to today.
 */
export function actionEventAt(record: ActionHistoryRecord): string {
  if (record.status.trim().toLowerCase() === "completed") {
    return record.verified_at ?? record.executed_at ?? record.created_at;
  }
  return record.executed_at ?? record.created_at;
}

/**
 * Classifies one record from its own ledger status without ever upgrading it
 * into a completed claim or downgrading it by age. The live status is the
 * authority; age is only ever reported as an explicitly non-authoritative note.
 */
export function classifyAction(
  record: ActionHistoryRecord,
): ActionHistoryOutcome {
  const status = record.status.trim().toLowerCase();
  if (status === "completed") {
    return record.verified_at !== null ? "VERIFIED" : "EXECUTED_UNVERIFIED";
  }
  if (NON_TERMINAL_STATUSES.has(status)) {
    return status === "pending" || status === "awaiting_approval"
      ? "PENDING_APPROVAL"
      : "IN_PROGRESS";
  }
  switch (status) {
    case "failed":
      return "FAILED";
    case "rejected":
    case "declined":
      return "DECLINED";
    case "cancelled":
    case "canceled":
      return "CANCELLED";
    case "expired":
      return "EXPIRED";
    case "uncertain":
      return "UNCERTAIN";
    case "blocked":
      return "BLOCKED";
    default:
      return "UNRECOGNIZED";
  }
}

const OUTCOME_LABELS: Record<ActionHistoryOutcome, string> = {
  VERIFIED: "completed and independently verified",
  EXECUTED_UNVERIFIED: "executed, not independently verified",
  PENDING_APPROVAL: "waiting for approval",
  IN_PROGRESS: "in progress",
  FAILED: "failed",
  DECLINED: "declined",
  CANCELLED: "cancelled",
  EXPIRED: "expired",
  UNCERTAIN: "outcome uncertain",
  BLOCKED: "blocked by policy",
  UNRECOGNIZED: "unrecognized status, not verified",
};

/** The fixed summary order; only non-zero buckets are spoken. */
const SUMMARY_ORDER: readonly {
  outcome: ActionHistoryOutcome;
  describe: (count: number) => string;
}[] = [
  { outcome: "VERIFIED", describe: (n) => `${n} verified as done` },
  {
    outcome: "EXECUTED_UNVERIFIED",
    describe: (n) => `${n} executed but not independently verified`,
  },
  { outcome: "PENDING_APPROVAL", describe: (n) => `${n} waiting on approval` },
  { outcome: "IN_PROGRESS", describe: (n) => `${n} still in progress` },
  { outcome: "UNCERTAIN", describe: (n) => `${n} with an uncertain outcome` },
  { outcome: "BLOCKED", describe: (n) => `${n} blocked by policy` },
  { outcome: "FAILED", describe: (n) => `${n} failed` },
  { outcome: "DECLINED", describe: (n) => `${n} declined` },
  { outcome: "CANCELLED", describe: (n) => `${n} cancelled` },
  { outcome: "EXPIRED", describe: (n) => `${n} expired` },
  {
    outcome: "UNRECOGNIZED",
    describe: (n) => `${n} with a status this runtime does not recognize`,
  },
];

function zonedFields(
  date: Date,
  timeZone: string,
): { year: number; month: number; day: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const read = (type: string) =>
    Number(parts.find((part) => part.type === type)?.value ?? Number.NaN);
  return { year: read("year"), month: read("month"), day: read("day") };
}

/** The zone's offset from UTC at one instant, in milliseconds. */
function zoneOffsetMs(date: Date, timeZone: string): number {
  const fields = zonedFields(date, timeZone);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(date);
  const read = (type: string) =>
    Number(parts.find((part) => part.type === type)?.value ?? Number.NaN);
  const asUtc = Date.UTC(
    fields.year,
    fields.month - 1,
    fields.day,
    read("hour") % 24,
    read("minute"),
    read("second"),
  );
  return asUtc - Math.floor(date.getTime() / 1000) * 1000;
}

/** The UTC instant of local midnight, corrected once across a DST change. */
function localMidnight(
  year: number,
  month: number,
  day: number,
  timeZone: string,
): number {
  const wallClock = Date.UTC(year, month - 1, day, 0, 0, 0, 0);
  const guess = zoneOffsetMs(new Date(wallClock), timeZone);
  let candidate = wallClock - guess;
  const corrected = zoneOffsetMs(new Date(candidate), timeZone);
  if (corrected !== guess) candidate = wallClock - corrected;
  return candidate;
}

/**
 * The half-open UTC window `[start, end)` of the local calendar day containing
 * `now` in `timeZone`. Resolving the day from the request's IANA zone is what
 * keeps a late-evening operator from being shifted into the next UTC day.
 */
export function actionHistoryDayWindow(
  now: Date,
  timeZone: string,
): { start: number; end: number } {
  const today = zonedFields(now, timeZone);
  const start = localMidnight(today.year, today.month, today.day, timeZone);
  const tomorrow = new Date(
    Date.UTC(today.year, today.month - 1, today.day + 1),
  );
  const end = localMidnight(
    tomorrow.getUTCFullYear(),
    tomorrow.getUTCMonth() + 1,
    tomorrow.getUTCDate(),
    timeZone,
  );
  return { start, end };
}

function localDateLabel(now: Date, timeZone: string): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone,
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(now);
}

function decision(
  state: AssistantResponseState,
  reason: string,
): CapabilityDecision {
  return parseCapabilityDecision({
    kind: state === "READY" ? "DELEGATE" : "CLARIFY",
    capability_id: "action.history",
    target: "actions.query",
    reason,
    requires_approval: false,
    action_state: "NONE",
    action_id: null,
    response_state: state,
  });
}

function clarify(
  reason: string,
  text: string,
): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  return {
    state: "CLARIFY",
    decision: decision("CLARIFY", reason),
    blocks: [{ kind: "NOTICE", state: "CLARIFY", text }],
  };
}

function actionItem(
  record: ActionHistoryRecord,
  outcome: ActionHistoryOutcome,
  band: string,
): AssistantBlock {
  const observedAt = actionEventAt(record);
  return {
    kind: "ITEM",
    item: {
      id: record.id,
      type: "ACTION",
      title: record.action_type,
      description: `${record.provider}, ${OUTCOME_LABELS[outcome]}`,
      status: outcome,
      due_at: null,
      band,
      sources: [
        {
          provider: "navox",
          source_type: "ACTION",
          external_resource_id: null,
          evidence_id: record.id,
          connection_id: null,
          observed_at: observedAt,
        },
      ],
    },
  };
}

/**
 * Builds the bounded Activity answer. A day-scoped request without a usable
 * IANA timezone is refused rather than silently answered in another day.
 */
interface ClassifiedAction {
  record: ActionHistoryRecord;
  outcome: ActionHistoryOutcome;
}

function newestActivityFirst(
  classified: readonly ClassifiedAction[],
): ClassifiedAction[] {
  return [...classified].sort(
    (left, right) =>
      Date.parse(actionEventAt(right.record)) -
      Date.parse(actionEventAt(left.record)),
  );
}

function summaryOf(classified: readonly ClassifiedAction[]): string {
  const counts = new Map<ActionHistoryOutcome, number>();
  for (const entry of classified) {
    counts.set(entry.outcome, (counts.get(entry.outcome) ?? 0) + 1);
  }
  return SUMMARY_ORDER.filter((entry) => (counts.get(entry.outcome) ?? 0) > 0)
    .map((entry) => entry.describe(counts.get(entry.outcome) ?? 0))
    .join("; ");
}

/**
 * The non-authoritative qualifiers. The ledger is read at its fixed bound and
 * returns no total, so a full page is reported as possibly partial rather than
 * presented as a complete history. A record that has not reached a terminal
 * status after a day keeps its own ledger status; the age note is informational
 * and never a status of its own.
 */
function qualifierFor(
  records: readonly ActionHistoryRecord[],
  classified: readonly ClassifiedAction[],
  now: Date,
): string {
  const qualifiers: string[] = [];
  if (records.length === ACTION_HISTORY_LIMIT)
    qualifiers.push(
      `Only the ${ACTION_HISTORY_LIMIT} most recent records were checked, so this list may be partial.`,
    );
  if (classified.length > LIMITS.maxDetails)
    qualifiers.push(
      `Showing the ${LIMITS.maxDetails} most recent of ${classified.length} here.`,
    );
  const unresolved = classified.filter(
    (entry) =>
      (entry.outcome === "PENDING_APPROVAL" ||
        entry.outcome === "IN_PROGRESS") &&
      now.getTime() - Date.parse(actionEventAt(entry.record)) >
        ACTION_HISTORY_AGE_NOTE_MS,
  ).length;
  if (unresolved > 0)
    qualifiers.push(
      `${unresolved} ${
        unresolved === 1 ? "record has" : "records have"
      } been unresolved for over 24 hours; that age note is informational and does not change the ledger status.`,
    );
  return qualifiers.length > 0 ? ` ${qualifiers.join(" ")}` : "";
}

function renderAnswer(
  scope: ActionHistoryScope,
  classified: readonly ClassifiedAction[],
  text: string,
): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  const blocks: AssistantBlock[] = [{ kind: "ANSWER", text }];
  const band = scope.kind === "DAY" ? "TODAY" : "RECENT";
  for (const entry of classified.slice(0, LIMITS.maxDetails))
    blocks.push(actionItem(entry.record, entry.outcome, band));
  return {
    state: "READY",
    decision: decision(
      "READY",
      scope.kind === "DAY" ? "action.history.day" : "action.history.recent",
    ),
    blocks,
  };
}

export function answerActionHistory(input: {
  records: readonly ActionHistoryRecord[];
  scope: ActionHistoryScope;
  timezone: string | null;
  now: Date;
}): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  const { records, scope, now } = input;
  if (scope.kind === "DAY") {
    if (!isIanaTimezone(input.timezone)) {
      return clarify(
        "action.history.timezone_required",
        "I can only resolve 'today' with a valid IANA time zone from this session. Please set a time zone and ask again.",
      );
    }
    const timezone: string = input.timezone;
    const window = actionHistoryDayWindow(now, timezone);
    const classified = newestActivityFirst(
      records
        .filter((record) => {
          const at = Date.parse(actionEventAt(record));
          return at >= window.start && at < window.end;
        })
        .map((record) => ({ record, outcome: classifyAction(record) })),
    );
    const qualifier = qualifierFor(records, classified, now);
    const label = localDateLabel(now, timezone);
    const dayLabel = `today (${label}, ${timezone})`;
    const text =
      classified.length === 0
        ? `No actions were recorded ${dayLabel}.${qualifier} Nothing was changed.`
        : `NavoX recorded ${classified.length} action ${
            classified.length === 1 ? "record" : "records"
          } ${dayLabel}: ${summaryOf(
            classified,
          )}. Only actions the ledger marks as independently verified are described as done.${qualifier}`;
    return renderAnswer(scope, classified, text);
  }
  const classified = newestActivityFirst(
    records.map((record) => ({
      record,
      outcome: classifyAction(record),
    })),
  );
  const qualifier = qualifierFor(records, classified, now);
  const text =
    classified.length === 0
      ? "No action records were found in your workspace. Nothing was changed."
      : `Here are your most recent action records (up to ${ACTION_HISTORY_LIMIT}): ${summaryOf(
          classified,
        )}. Only actions the ledger marks as independently verified are described as done.${qualifier}`;
  return renderAnswer(scope, classified, text);
}
