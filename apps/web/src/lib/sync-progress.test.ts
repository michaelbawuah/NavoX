import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isSyncTerminal,
  monitorSync,
  type SyncProgress,
  syncErrorHelp,
  syncProgressMessage,
} from "./sync-progress";

const workflowId = "intelligence:connection:gmail:request";
function progress(
  status: SyncProgress["status"],
  extra: Partial<SyncProgress> = {},
): SyncProgress {
  return {
    workflow_id: workflowId,
    status,
    commitment_count: null,
    error: null,
    ...extra,
  };
}

describe("source sync monitoring", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it.each([
    ["provider_request_failed", "timeout", "timeout"],
    ["provider_request_failed", "quota_exhausted", "quota_exhausted"],
    ["provider_request_failed", "private provider text", undefined],
    ["provider_request_failed", ["timeout"], undefined],
    ["provider_request_failed", "constructor", undefined],
    ["google_rate_limited", "timeout", undefined],
  ])(
    "validates the AI category for %s / %s",
    async (code, providerCode, expected) => {
      const onTerminal = vi.fn();
      const stop = monitorSync({
        workflowId,
        read: vi.fn().mockResolvedValue({
          ...progress("failed"),
          error: {
            code,
            provider_code: providerCode,
            message: "private source text",
          },
        }),
        onProgress: vi.fn(),
        onTerminal,
        onPause: vi.fn(),
      });
      await vi.advanceTimersByTimeAsync(0);
      expect(onTerminal.mock.calls[0][0].error).toEqual({
        code,
        ...(expected ? { provider_code: expected } : {}),
      });
      stop();
    },
  );

  it("reports running and retrying, then completes once even with zero commitments", async () => {
    const read = vi
      .fn()
      .mockResolvedValueOnce(progress("queued"))
      .mockResolvedValueOnce(progress("running"))
      .mockResolvedValueOnce(progress("retrying"))
      .mockResolvedValue(progress("completed", { commitment_count: 0 }));
    const onProgress = vi.fn();
    const onTerminal = vi.fn();
    const onPause = vi.fn();
    const stop = monitorSync({
      workflowId,
      read,
      onProgress,
      onTerminal,
      onPause,
    });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(onProgress.mock.calls.map(([value]) => value.status)).toEqual([
      "queued",
      "running",
      "retrying",
      "completed",
    ]);
    expect(onTerminal).toHaveBeenCalledExactlyOnceWith(
      progress("completed", { commitment_count: 0 }),
    );
    expect(read).toHaveBeenCalledTimes(4);
    expect(onPause).not.toHaveBeenCalled();
    stop();
  });

  it("pauses after repeated status failures and resumes the same job on request", async () => {
    const read = vi.fn().mockRejectedValue(new Error("unreachable"));
    const onProgress = vi.fn();
    const onTerminal = vi.fn();
    const onPause = vi.fn();
    const stop = monitorSync({
      workflowId,
      read,
      onProgress,
      onTerminal,
      onPause,
    });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(read).toHaveBeenCalledTimes(3);
    expect(onPause).toHaveBeenCalledTimes(1);
    expect(onTerminal).not.toHaveBeenCalled();
    expect(onProgress.mock.lastCall?.[0].status).toBe("unavailable");
    stop();
    read.mockResolvedValue(progress("completed"));
    const resumed = monitorSync({
      workflowId,
      read,
      onProgress,
      onTerminal,
      onPause,
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(read.mock.calls.every(([id]) => id === workflowId)).toBe(true);
    expect(onTerminal).toHaveBeenCalledTimes(1);
    resumed();
  });

  it("does not overlap slow requests and ignores a late response after cleanup", async () => {
    let resolve: (value: SyncProgress) => void = () => {};
    const read = vi.fn(
      (_id: string, _signal: AbortSignal) =>
        new Promise<SyncProgress>((done) => {
          resolve = done;
        }),
    );
    const onProgress = vi.fn();
    const onTerminal = vi.fn();
    const stop = monitorSync({
      workflowId,
      read,
      onProgress,
      onTerminal,
      onPause: vi.fn(),
    });
    await vi.advanceTimersByTimeAsync(9_000);
    expect(read).toHaveBeenCalledTimes(1);
    stop();
    expect(read.mock.calls[0][1].aborted).toBe(true);
    resolve(progress("completed"));
    await vi.advanceTimersByTimeAsync(30_000);
    expect(onProgress).not.toHaveBeenCalled();
    expect(onTerminal).not.toHaveBeenCalled();
  });

  it("times out stalled status requests without declaring the sync failed", async () => {
    const read = vi.fn(
      (_id: string, signal: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new Error("timeout")), {
            once: true,
          });
        }),
    );
    const onProgress = vi.fn();
    const onTerminal = vi.fn();
    const stop = monitorSync({
      workflowId,
      read,
      onProgress,
      onTerminal,
      onPause: vi.fn(),
    });
    await vi.advanceTimersByTimeAsync(15_000);
    expect(onProgress).toHaveBeenLastCalledWith(progress("unavailable"));
    expect(onTerminal).not.toHaveBeenCalled();
    stop();
  });

  it("keeps simultaneous Gmail and Calendar results independent", async () => {
    const calendarId = "intelligence:connection:calendar:request";
    const read = vi.fn(async (id: string) =>
      id === workflowId
        ? progress("completed", { commitment_count: 2 })
        : progress("failed", {
            workflow_id: calendarId,
            error: { code: "google_api_disabled" },
          }),
    );
    const gmailDone = vi.fn();
    const calendarDone = vi.fn();
    const gmailStop = monitorSync({
      workflowId,
      read,
      onProgress: vi.fn(),
      onTerminal: gmailDone,
      onPause: vi.fn(),
    });
    const calendarStop = monitorSync({
      workflowId: calendarId,
      read,
      onProgress: vi.fn(),
      onTerminal: calendarDone,
      onPause: vi.fn(),
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(gmailDone.mock.calls[0][0].status).toBe("completed");
    expect(calendarDone.mock.calls[0][0].error.code).toBe(
      "google_api_disabled",
    );
    gmailStop();
    calendarStop();
  });

  it("never accepts another workflow's completion or unknown response states", async () => {
    const read = vi
      .fn()
      .mockResolvedValueOnce(
        progress("completed", { workflow_id: "other-workflow" }),
      )
      .mockResolvedValueOnce({
        workflow_id: workflowId,
        status: "imaginary_success",
      })
      .mockResolvedValue(null);
    const onProgress = vi.fn();
    const onTerminal = vi.fn();
    const stop = monitorSync({
      workflowId,
      read,
      onProgress,
      onTerminal,
      onPause: vi.fn(),
    });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(
      onProgress.mock.calls.every(([value]) => value.status === "unavailable"),
    ).toBe(true);
    expect(onTerminal).not.toHaveBeenCalled();
    stop();
  });

  it("retains a numeric cooldown but ignores invalid provider fields", async () => {
    const read = vi
      .fn()
      .mockResolvedValueOnce(
        progress("retrying", {
          error: { code: "google_rate_limited", retry_after_seconds: 60 },
        }),
      )
      .mockResolvedValueOnce({
        ...progress("retrying"),
        error: {
          code: "google_rate_limited",
          retry_after_seconds: "private text",
        },
      })
      .mockResolvedValue(
        progress("failed", {
          error: { code: "google_quota_exceeded", retry_after_seconds: -10 },
        }),
      );
    const onProgress = vi.fn();
    const stop = monitorSync({
      workflowId,
      read,
      onProgress,
      onTerminal: vi.fn(),
      onPause: vi.fn(),
    });
    await vi.advanceTimersByTimeAsync(6_000);
    expect(onProgress.mock.calls[0][0].error.retry_after_seconds).toBe(60);
    expect(onProgress.mock.calls[1][0].error).toEqual({
      code: "google_rate_limited",
    });
    expect(onProgress.mock.calls[2][0].error).toEqual({
      code: "google_quota_exceeded",
    });
    stop();
  });
});

describe("safe sync messages", () => {
  it("distinguishes AI timeouts, connection failures, and quotas without reconnecting Google", () => {
    expect(
      syncErrorHelp("gmail", "provider_request_failed", "timeout").message,
    ).toContain("timed out");
    expect(
      syncErrorHelp("gmail", "provider_request_failed", "transport_error")
        .message,
    ).toContain("connection");
    const quota = syncErrorHelp(
      "gmail",
      "provider_request_failed",
      "quota_exhausted",
    );
    expect(quota.message).toContain("AI service");
    expect(quota.message).toContain("quota");
    expect(quota.reconnect).toBe(false);
    const legacy = syncErrorHelp("gmail", "provider_request_failed");
    for (const unknown of [
      "private provider text",
      "constructor",
      "__proto__",
    ]) {
      expect(
        syncErrorHelp("gmail", "provider_request_failed", unknown),
      ).toEqual(legacy);
    }
  });
  it("distinguishes unavailable status from terminal failure", () => {
    expect(isSyncTerminal("unavailable")).toBe(false);
    expect(isSyncTerminal("retrying")).toBe(false);
    expect(isSyncTerminal("failed")).toBe(true);
    expect(syncProgressMessage(progress("unavailable"))).toContain(
      "may still be running",
    );
    expect(
      syncProgressMessage(progress("completed", { commitment_count: 0 })),
    ).toContain("0 commitments processed");
  });

  it("directs API enablement to the correct source without changing permissions", () => {
    expect(syncErrorHelp("gmail", "google_api_disabled").message).toContain(
      "Gmail API",
    );
    expect(syncErrorHelp("calendar", "google_api_disabled").message).toContain(
      "Google Calendar API",
    );
    expect(syncErrorHelp("gmail", "google_api_disabled").reconnect).toBe(false);
    expect(syncErrorHelp("gmail", "google_scope_missing").reconnect).toBe(true);
  });

  it("never displays an unknown provider error string", () => {
    const help = syncErrorHelp("gmail", "private-token-and-email-body");
    expect(help.message).not.toContain("private-token-and-email-body");
    expect(help.message).toContain("could not finish");
  });

  it("keeps policy denial distinct from a missing permission", () => {
    expect(
      syncErrorHelp("gmail", "google_permission_denied").message,
    ).toContain("Workspace access policies");
    expect(syncErrorHelp("gmail", "google_scope_missing").message).toContain(
      "Reconnect read access",
    );
  });

  it("distinguishes daily limits from generic quota and temporary rate limits", () => {
    const daily = syncErrorHelp("gmail", "google_daily_limit_exceeded");
    const quota = syncErrorHelp("calendar", "google_quota_exceeded");
    expect(daily.message).toContain("Gmail API quotas");
    expect(daily.message).toContain(
      "limit resets or the configuration is corrected",
    );
    expect(quota.message).toContain(
      "Google Calendar API quota metric and limit",
    );
    expect(quota.message).not.toContain("Wait a little");
    expect(`${daily.message} ${quota.message}`).not.toMatch(
      /pay|billing|raise/i,
    );
    expect(daily.reconnect).toBe(false);
    expect(quota.reconnect).toBe(false);
    expect(syncErrorHelp("gmail", "google_rate_limited").message).toContain(
      "Wait a little",
    );
  });

  it("describes a scheduled cooldown only while the job is retrying", () => {
    const error = { code: "google_rate_limited", retry_after_seconds: 60 };
    expect(syncProgressMessage(progress("retrying", { error }))).toContain(
      "cooldown before the next attempt",
    );
    expect(syncProgressMessage(progress("failed", { error }))).toBe(
      "Sync failed.",
    );
    expect(
      syncProgressMessage(
        progress("retrying", {
          error: { code: "google_rate_limited" },
        }),
      ),
    ).not.toContain("cooldown");
  });
});
