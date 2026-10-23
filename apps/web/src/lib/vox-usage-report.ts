import type { Account, AccountAccess } from "./account-access";

export const USAGE_REPORT_PAGE_SIZE = 25;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const money = /^(0|[1-9]\d{0,29})(?:\.(\d{1,18}))?(?:[eE]([+-]?\d{1,2}))?$/;
const unavailable = "Daily Vox usage could not be loaded. Try again.";

export interface UsageReportStudent {
  userId: string;
  timezone: string;
  localDay: string;
  speakingMs: number;
  idleMs: number;
  connectedMs: number;
  tokens: number;
  reservedTokens: number;
  estimatedCost: string | null;
  knownCost: string;
  unknownComponents: number;
  costBasis: "estimate" | "unknown" | "reconciled";
  reconciledCost: string | null;
}
export interface UsageReportPage {
  students: UsageReportStudent[];
  nextAfterUserId: string | null;
}
export interface UsageReportState extends UsageReportPage {
  phase: "hidden" | "loading" | "ready" | "error";
  day: string | null;
  afterUserId: string | null;
}
export const hiddenUsageReport = (): UsageReportState => ({
  phase: "hidden",
  day: null,
  afterUserId: null,
  students: [],
  nextAfterUserId: null,
});

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(unavailable);
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, keys: string[]) {
  if (
    Object.keys(value).length !== keys.length ||
    keys.some((key) => !(key in value))
  )
    throw new Error(unavailable);
}
function counter(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0)
    throw new Error(unavailable);
  return value;
}
function cost(value: unknown): string {
  if (typeof value !== "string") throw new Error(unavailable);
  const match = money.exec(value);
  if (!match || Math.abs(Number(match[3] ?? 0)) > 30)
    throw new Error(unavailable);
  const digits = match[1] + (match[2] ?? "");
  const point = match[1].length + Number(match[3] ?? 0);
  if (point <= 0) return `0.${"0".repeat(-point)}${digits}`;
  if (point >= digits.length) return digits + "0".repeat(point - digits.length);
  return `${digits.slice(0, point)}.${digits.slice(point)}`;
}

/** Validation only: default "today" always comes from the server, never this clock. */
export function validUsageReportDate(value: string): boolean {
  if (
    !/^(?:19[7-9]\d|[2-9]\d{3})-\d{2}-\d{2}$/.test(value) ||
    value.startsWith("9999")
  )
    return false;
  const instant = new Date(`${value}T00:00:00Z`);
  return (
    Number.isFinite(instant.valueOf()) &&
    instant.toISOString().slice(0, 10) === value
  );
}

export function readUsageReportPage(value: unknown): UsageReportPage {
  const page = record(value);
  exact(page, ["students", "next_after_user_id"]);
  if (
    !Array.isArray(page.students) ||
    page.students.length > USAGE_REPORT_PAGE_SIZE
  )
    throw new Error(unavailable);
  const students = page.students.map((raw): UsageReportStudent => {
    const row = record(raw);
    exact(row, ["user_id", "timezone", "local_day", "totals"]);
    if (
      typeof row.user_id !== "string" ||
      !uuid.test(row.user_id) ||
      typeof row.timezone !== "string" ||
      !/^[A-Za-z0-9_+./-]{1,64}$/.test(row.timezone) ||
      typeof row.local_day !== "string" ||
      !validUsageReportDate(row.local_day)
    )
      throw new Error(unavailable);
    const totals = record(row.totals);
    const requiredTotals = [
      "speaking_ms",
      "idle_ms",
      "connected_ms",
      "tokens",
      "reserved_tokens",
      "estimated_cost_usd",
      "known_estimated_cost_usd",
      "unknown_cost_components",
      "reconciled_cost_usd",
      "cost_basis",
      "transport_billing_notice",
    ];
    // Read only known counters; future reservation metadata is not actual usage.
    if (requiredTotals.some((key) => !(key in totals)))
      throw new Error(unavailable);
    const speakingMs = counter(totals.speaking_ms),
      idleMs = counter(totals.idle_ms);
    const connectedMs = counter(totals.connected_ms);
    const tokens = counter(totals.tokens),
      reservedTokens = counter(totals.reserved_tokens);
    const unknownComponents = counter(totals.unknown_cost_components);
    const estimatedCost =
      totals.estimated_cost_usd === null
        ? null
        : cost(totals.estimated_cost_usd);
    const reconciledCost =
      totals.reconciled_cost_usd === null
        ? null
        : cost(totals.reconciled_cost_usd);
    const knownCost = cost(totals.known_estimated_cost_usd);
    const costBasis = totals.cost_basis;
    if (
      speakingMs + idleMs !== connectedMs ||
      !["estimate", "unknown", "reconciled"].includes(String(costBasis)) ||
      typeof totals.transport_billing_notice !== "string" ||
      totals.transport_billing_notice.length > 240 ||
      (costBasis === "unknown" && estimatedCost !== null) ||
      (costBasis === "estimate" &&
        (estimatedCost === null || unknownComponents > 0)) ||
      (costBasis === "reconciled" && reconciledCost === null)
    )
      throw new Error(unavailable);
    return {
      userId: row.user_id.toLowerCase(),
      timezone: row.timezone,
      localDay: row.local_day,
      speakingMs,
      idleMs,
      connectedMs,
      tokens,
      reservedTokens,
      estimatedCost,
      knownCost,
      unknownComponents,
      reconciledCost,
      costBasis: costBasis as UsageReportStudent["costBasis"],
    };
  });
  for (let index = 1; index < students.length; index++)
    if (students[index].userId <= students[index - 1].userId)
      throw new Error(unavailable);
  const next = page.next_after_user_id;
  if (
    next !== null &&
    (typeof next !== "string" ||
      !uuid.test(next) ||
      students.length !== USAGE_REPORT_PAGE_SIZE ||
      next.toLowerCase() !== students.at(-1)?.userId)
  )
    throw new Error(unavailable);
  return {
    students,
    nextAfterUserId: typeof next === "string" ? next.toLowerCase() : null,
  };
}

/** Reuse the shared session observer without fetching connected student records. */
export async function restoreUsageReportAccess(
  api: string,
): Promise<AccountAccess> {
  const response = await fetch(`${api.replace(/\/$/, "")}/auth/me`, {
    credentials: "include",
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(10_000),
  });
  if (response.status === 401 || response.status === 403)
    return { account: null, connections: [], connectionsError: "" };
  if (!response.ok) throw new Error(unavailable);
  const value = record(await response.json()),
    workspace = record(value.workspace);
  if (
    typeof value.id !== "string" ||
    !uuid.test(value.id) ||
    typeof workspace.id !== "string" ||
    !uuid.test(workspace.id) ||
    typeof value.email !== "string" ||
    !(value.display_name === null || typeof value.display_name === "string") ||
    typeof workspace.name !== "string" ||
    typeof workspace.workspace_type !== "string"
  )
    throw new Error(unavailable);
  const account: Account = {
    id: value.id,
    email: value.email,
    display_name: value.display_name,
    workspace: {
      id: workspace.id,
      name: workspace.name,
      workspace_type: workspace.workspace_type,
    },
  };
  return { account, connections: [], connectionsError: "" };
}

/** In-memory, read-only pages bound to the freshly restored cookie account. */
export class VoxUsageReportController {
  private owner: string | null = null;
  private generation = 0;
  private request: AbortController | null = null;
  private authorized = false;
  private disposed = false;
  private state = hiddenUsageReport();
  constructor(
    private readonly options: {
      apiBaseUrl: string;
      fetch?: typeof globalThis.fetch;
      onChange(state: UsageReportState): void;
    },
  ) {}
  private publish(state: UsageReportState) {
    this.state = state;
    this.options.onChange(state);
  }
  clear(): void {
    ++this.generation;
    this.request?.abort();
    this.request = null;
    this.owner = null;
    this.authorized = false;
    this.publish(hiddenUsageReport());
  }
  activate(owner: string): void {
    this.clear();
    if (
      this.disposed ||
      owner.split(":").length !== 2 ||
      !owner.split(":").every((id) => uuid.test(id))
    )
      return;
    this.owner = owner;
    void this.load(null, null);
  }
  refresh(day: string | null): boolean {
    if (day !== null && !validUsageReportDate(day)) return false;
    void this.load(day, null);
    return true;
  }
  next(): void {
    if (this.state.phase === "ready" && this.state.nextAfterUserId)
      void this.load(this.state.day, this.state.nextAfterUserId);
  }
  first(): void {
    if (this.state.phase === "ready") void this.load(this.state.day, null);
  }
  private async load(
    day: string | null,
    afterUserId: string | null,
  ): Promise<void> {
    const owner = this.owner;
    if (this.disposed || !owner) return;
    this.request?.abort();
    const request = new AbortController(),
      generation = ++this.generation;
    this.request = request;
    const current = () =>
      !this.disposed &&
      this.owner === owner &&
      this.generation === generation &&
      this.request === request;
    const state: UsageReportState = {
      phase: this.authorized ? "loading" : "hidden",
      day,
      afterUserId,
      students: [],
      nextAfterUserId: null,
    };
    this.publish(state);
    const timeout = setTimeout(() => {
      if (!current()) return;
      request.abort();
      ++this.generation;
      this.request = null;
      this.publish({ ...state, phase: this.authorized ? "error" : "hidden" });
    }, 10_000);
    let provedAccess = this.authorized;
    try {
      const query = new URLSearchParams({
        limit: String(USAGE_REPORT_PAGE_SIZE),
      });
      if (day !== null) query.set("day", day);
      if (afterUserId !== null) query.set("after_user_id", afterUserId);
      const response = await (this.options.fetch ?? globalThis.fetch)(
        `${this.options.apiBaseUrl.replace(/\/$/, "")}/vox/usage/daily?${query}`,
        {
          method: "GET",
          credentials: "include",
          cache: "no-store",
          redirect: "error",
          headers: { "X-NavoX-Account": owner },
          signal: request.signal,
        },
      );
      if (!current() || request.signal.aborted) return;
      if ([401, 403, 409].includes(response.status)) {
        this.clear();
        return;
      }
      if (!response.ok) throw new Error(unavailable);
      provedAccess = true;
      const body: unknown = await response.json();
      if (!current() || request.signal.aborted) return;
      const page = readUsageReportPage(body);
      if (
        (day !== null && page.students.some((row) => row.localDay !== day)) ||
        (afterUserId !== null &&
          page.students.some((row) => row.userId <= afterUserId))
      )
        throw new Error(unavailable);
      this.authorized = true;
      this.publish({ ...state, ...page, phase: "ready" });
    } catch {
      if (current()) {
        this.authorized = provedAccess;
        this.publish({ ...state, phase: provedAccess ? "error" : "hidden" });
      }
    } finally {
      clearTimeout(timeout);
      if (this.request === request) this.request = null;
    }
  }
  dispose(): void {
    this.disposed = true;
    this.clear();
  }
}
