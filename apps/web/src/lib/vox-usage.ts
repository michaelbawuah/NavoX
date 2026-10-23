import type { VoxCallState, VoxOwner } from "./vox-call";

export interface VoxUsageNotice {
  message: string;
  blocked: boolean;
  policyRevision: number;
}

export function usageNotice(value: unknown): VoxUsageNotice | null {
  if (!value || typeof value !== "object")
    throw new Error("Invalid voice allowance status.");
  const body = value as Record<string, unknown>;
  if (
    typeof body.blocked !== "boolean" ||
    !Number.isSafeInteger(body.policy_revision) ||
    (body.policy_revision as number) < 0 ||
    !Array.isArray(body.notices) ||
    body.notices.length > 4 ||
    body.notices.some(
      (notice) =>
        typeof notice !== "string" || !notice.trim() || notice.length > 240,
    )
  )
    throw new Error("Invalid voice allowance status.");
  const message = body.blocked
    ? "Your Vox allowance has been reached. Try again after your allowance resets."
    : (body.notices[0] as string | undefined)?.trim();
  return message
    ? {
        message,
        blocked: body.blocked,
        policyRevision: body.policy_revision as number,
      }
    : null;
}

/** Read server counters only. No audio, transcript, token count or cost is reported here. */
export class VoxUsageMonitor {
  private scope: { key: string; owner: Readonly<VoxOwner> } | null = null;
  private phase: VoxCallState["phase"] | null = null;
  private interval: ReturnType<typeof setInterval> | null = null;
  private request: AbortController | null = null;
  private timeout: ReturnType<typeof setTimeout> | null = null;
  private generation = 0;
  private disposed = false;
  private finalReadPending = false;
  private notice: VoxUsageNotice | null = null;

  constructor(
    private readonly options: {
      apiBaseUrl: string;
      fetch?: typeof globalThis.fetch;
      onChange(notice: VoxUsageNotice | null): void;
    },
  ) {}

  private publish(notice: VoxUsageNotice | null) {
    if (
      notice?.message === this.notice?.message &&
      notice?.blocked === this.notice?.blocked &&
      notice?.policyRevision === this.notice?.policyRevision
    )
      return;
    this.notice = notice;
    this.options.onChange(notice);
  }

  private clearInterval() {
    if (this.interval !== null) clearInterval(this.interval);
    this.interval = null;
  }

  private revoke() {
    ++this.generation;
    this.request?.abort();
    this.request = null;
    if (this.timeout !== null) clearTimeout(this.timeout);
    this.timeout = null;
    this.clearInterval();
    this.scope = null;
    this.phase = null;
    this.finalReadPending = false;
    this.publish(null);
  }

  update(state: VoxCallState): void {
    if (this.disposed) return;
    if (
      !state.ownerVerified ||
      !state.owner ||
      !state.callId ||
      state.phase === "idle" ||
      state.phase === "unavailable"
    ) {
      this.revoke();
      return;
    }
    const key = `${state.owner.workspaceId}:${state.owner.userId}:${state.callId}`;
    const changed = this.scope?.key !== key;
    if (changed) {
      this.revoke();
      this.scope = { key, owner: Object.freeze({ ...state.owner }) };
    }
    const phaseChanged = this.phase !== state.phase;
    this.phase = state.phase;
    const ongoing = state.phase !== "ended";
    if (!ongoing) this.clearInterval();
    else if (this.interval === null)
      this.interval = setInterval(() => {
        void this.refresh();
      }, 30_000);
    if (changed || (phaseChanged && state.phase === "ended")) {
      if (this.request && state.phase === "ended") this.finalReadPending = true;
      else void this.refresh();
    }
  }

  private async refresh(): Promise<void> {
    const scope = this.scope;
    if (this.disposed || !scope || this.request) return;
    const generation = this.generation;
    const controller = new AbortController();
    this.request = controller;
    const timeout = setTimeout(() => {
      controller.abort();
      if (this.request === controller) this.request = null;
      if (this.timeout === timeout) this.timeout = null;
    }, 10_000);
    this.timeout = timeout;
    try {
      const response = await (this.options.fetch ?? globalThis.fetch)(
        `${this.options.apiBaseUrl.replace(/\/$/, "")}/vox/usage/status`,
        {
          method: "GET",
          credentials: "include",
          cache: "no-store",
          redirect: "error",
          headers: {
            "X-NavoX-Account": `${scope.owner.workspaceId}:${scope.owner.userId}`,
          },
          signal: controller.signal,
        },
      );
      if (
        !response.ok ||
        controller.signal.aborted ||
        this.disposed ||
        generation !== this.generation ||
        this.scope?.key !== scope.key
      )
        return;
      const body: unknown = await response.json();
      if (
        controller.signal.aborted ||
        this.disposed ||
        generation !== this.generation ||
        this.scope?.key !== scope.key
      )
        return;
      this.publish(usageNotice(body));
    } catch {
      // An unavailable read never invents remaining allowance or clears a verified notice.
    } finally {
      clearTimeout(timeout);
      if (this.timeout === timeout) this.timeout = null;
      if (this.request === controller) this.request = null;
      if (
        !this.disposed &&
        generation === this.generation &&
        this.finalReadPending
      ) {
        this.finalReadPending = false;
        void this.refresh();
      }
    }
  }

  dispose(): void {
    this.disposed = true;
    this.revoke();
  }
}
