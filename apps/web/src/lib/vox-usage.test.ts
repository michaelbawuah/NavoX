import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { VoxCallState, VoxOwner } from "./vox-call";
import { usageNotice, VoxUsageMonitor } from "./vox-usage";

const owner: VoxOwner = {
  userId: "11111111-1111-4111-8111-111111111111",
  workspaceId: "22222222-2222-4222-8222-222222222222",
};
const other = { ...owner, userId: "11111111-1111-4111-8111-111111111112" };
const warning = "You have used at least 80% of your daily voice allowance.";
const status = (notices: string[] = [], blocked = false) => ({
  notices,
  blocked,
  policy_revision: 2,
  daily: {},
  monthly: {},
});
function state(
  scope = owner,
  phase: VoxCallState["phase"] = "active",
): VoxCallState {
  return {
    owner: scope,
    ownerVerified: true,
    callId: "33333333-3333-4333-8333-333333333333",
    phase,
    activity: "quiet",
    microphoneMuted: false,
    speakerEnabled: true,
    speakerBlocked: false,
    captionsEnabled: true,
    minimized: false,
    startedAt: 0,
    elapsedMs: 0,
    endReason: null,
  };
}
async function settle() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const monitors: VoxUsageMonitor[] = [];
function rig(
  fetcher = vi.fn<typeof fetch>(async () => Response.json(status([warning]))),
) {
  const onChange = vi.fn();
  const monitor = new VoxUsageMonitor({
    apiBaseUrl: "https://navox.example/api/v1",
    fetch: fetcher,
    onChange,
  });
  monitors.push(monitor);
  return { monitor, fetcher, onChange };
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  for (const monitor of monitors.splice(0)) monitor.dispose();
  vi.useRealTimers();
});

describe("private Vox allowance notices", () => {
  it("reads only server status at call start with the exact account header and deduplicates the 80% notice", async () => {
    const env = rig();
    env.monitor.update(state(owner, "idle"));
    expect(env.fetcher).not.toHaveBeenCalled();
    env.monitor.update(state());
    await settle();
    expect(env.fetcher).toHaveBeenCalledExactlyOnceWith(
      "https://navox.example/api/v1/vox/usage/status",
      {
        method: "GET",
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        headers: { "X-NavoX-Account": `${owner.workspaceId}:${owner.userId}` },
        signal: expect.any(AbortSignal),
      },
    );
    expect(env.onChange).toHaveBeenCalledExactlyOnceWith({
      message: warning,
      blocked: false,
      policyRevision: 2,
    });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(env.fetcher).toHaveBeenCalledTimes(2);
    expect(env.onChange).toHaveBeenCalledTimes(1);
    expect(
      env.fetcher.mock.calls.every(
        ([, options]) =>
          options?.method === "GET" && options.body === undefined,
      ),
    ).toBe(true);
  });

  it("uses a helpful hard-allowance message and never calculates costs or remaining usage in the browser", async () => {
    const env = rig(
      vi.fn<typeof fetch>(async () =>
        Response.json(status([warning, "Another allowance"], true)),
      ),
    );
    env.monitor.update(state());
    await settle();
    expect(env.onChange).toHaveBeenCalledWith({
      message:
        "Your Vox allowance has been reached. Try again after your allowance resets.",
      blocked: true,
      policyRevision: 2,
    });
    env.monitor.update(state(owner, "ended"));
    await settle();
    const calls = env.fetcher.mock.calls.length;
    await vi.advanceTimersByTimeAsync(120_000);
    expect(env.fetcher).toHaveBeenCalledTimes(calls);
  });

  it("reads the final hard allowance after a pending start check when the call ends quickly", async () => {
    const pending = deferred<Response>();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(Response.json(status([warning], true)));
    const env = rig(fetcher);
    env.monitor.update(state());
    env.monitor.update(state(owner, "ended"));
    expect(fetcher).toHaveBeenCalledTimes(1);
    pending.resolve(Response.json(status()));
    await settle();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(env.onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ blocked: true }),
    );
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("aborts an old owner's request and never shows its late notice under a new owner", async () => {
    const old = deferred<Response>();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(Response.json(status()));
    const env = rig(fetcher);
    env.monitor.update(state());
    const signal = fetcher.mock.calls[0][1]?.signal;
    env.monitor.update(state(other));
    await settle();
    expect(signal?.aborted).toBe(true);
    expect(fetcher.mock.calls[1][1]?.headers).toEqual({
      "X-NavoX-Account": `${other.workspaceId}:${other.userId}`,
    });
    old.resolve(Response.json(status(["OLD OWNER PRIVATE NOTICE"])));
    await settle();
    expect(env.onChange).not.toHaveBeenCalled();
  });

  it("clears a verified notice and aborts polling immediately while account verification is suspended", async () => {
    const env = rig();
    env.monitor.update(state());
    await settle();
    env.monitor.update({ ...state(), ownerVerified: false });
    expect(env.onChange).toHaveBeenLastCalledWith(null);
    const calls = env.fetcher.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(env.fetcher).toHaveBeenCalledTimes(calls);
  });

  it("ignores an abort-ignoring response after unmount and leaves no active timer", async () => {
    const pending = deferred<Response>();
    const env = rig(vi.fn<typeof fetch>().mockReturnValue(pending.promise));
    env.monitor.update(state());
    const signal = env.fetcher.mock.calls[0][1]?.signal;
    env.monitor.dispose();
    expect(signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    pending.resolve(Response.json(status([warning], true)));
    await settle();
    expect(env.onChange).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not invent an allowance message from an unavailable or malformed response", async () => {
    const env = rig(
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(new Response(null, { status: 503 }))
        .mockResolvedValueOnce(
          Response.json({
            notices: [warning],
            blocked: "true",
            policy_revision: 2,
          }),
        ),
    );
    env.monitor.update(state());
    await settle();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(env.onChange).not.toHaveBeenCalled();
    expect(() =>
      usageNotice({ ...status(), notices: ["x".repeat(241)] }),
    ).toThrow();
  });
});
