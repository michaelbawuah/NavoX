// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountAccess } from "./account-access";

const access = vi.hoisted(() => ({ restore: vi.fn() }));
vi.mock("./account-access", () => ({ restoreAccountAccess: access.restore }));

import {
  ACCOUNT_SESSION_CHANGED_EVENT,
  ACCOUNT_SESSION_CHANGING_EVENT,
  type AccountSessionChange,
  notifyAccountSessionChanged,
  notifyAccountSessionChanging,
  observeAccountSession,
} from "./account-session";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function owner(id: string): AccountAccess {
  return {
    account: {
      id,
      email: `${id}@example.test`,
      display_name: `Synthetic ${id}`,
      workspace: {
        id: `workspace-${id}`,
        name: "Personal",
        workspace_type: "personal",
      },
    },
    connections: [],
    connectionsError: "",
  };
}

const accountA = owner("owner-a");
const accountB = owner("owner-b");
const signedOut: AccountAccess = {
  account: null,
  connections: [],
  connectionsError: "",
};

class SessionChannel extends EventTarget {
  static open = new Set<SessionChannel>();
  static posted: unknown[] = [];
  constructor(readonly name: string) {
    super();
    SessionChannel.open.add(this);
  }
  postMessage(data: unknown) {
    SessionChannel.posted.push(data);
    for (const target of SessionChannel.open) {
      if (target === this) continue;
      queueMicrotask(() => {
        if (SessionChannel.open.has(target))
          target.dispatchEvent(new MessageEvent("message", { data }));
      });
    }
  }
  close() {
    SessionChannel.open.delete(this);
  }
  static receive(data: unknown) {
    for (const target of SessionChannel.open)
      target.dispatchEvent(new MessageEvent("message", { data }));
  }
}

let observers: ReturnType<typeof observeAccountSession>[];
let changes: AccountSessionChange[];
let visibility: DocumentVisibilityState;

function callbacks() {
  return {
    checking: vi.fn(),
    restored: vi.fn(),
    unavailable: vi.fn(),
    revoking: vi.fn(),
  };
}

function observe(
  callback = callbacks(),
  options?: Parameters<typeof observeAccountSession>[1],
) {
  const observer = observeAccountSession(callback, options);
  observers.push(observer);
  return { ...callback, observer };
}

function changing() {
  const change = notifyAccountSessionChanging();
  changes.push(change);
  return change;
}

function visible(value: DocumentVisibilityState) {
  visibility = value;
  document.dispatchEvent(new Event("visibilitychange"));
}

function remote(
  sequence: number,
  activeChanges: string[],
  phase = "session-changed",
  sender = "remote",
) {
  SessionChannel.receive({
    schema: "navox-account-session.v1",
    sender,
    sequence,
    phase,
    activeChanges,
  });
}

async function settle() {
  for (let index = 0; index < 4; index += 1) await Promise.resolve();
}

beforeEach(() => {
  observers = [];
  changes = [];
  visibility = "visible";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(
    () => visibility,
  );
  vi.stubGlobal("BroadcastChannel", SessionChannel);
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Real network is forbidden in this test");
    }),
  );
  SessionChannel.posted = [];
  access.restore.mockReset().mockResolvedValue(accountA);
});

afterEach(async () => {
  for (const observer of observers) observer.dispose();
  for (const change of changes) notifyAccountSessionChanged(change);
  await settle();
  expect(SessionChannel.open.size).toBe(0);
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("shared account-session observation", () => {
  it("synchronously checks before invoking initial restoration and waits for verified access", async () => {
    const pending = deferred<AccountAccess>();
    const callback = callbacks();
    access.restore.mockImplementation(() => {
      expect(callback.checking).toHaveBeenCalledOnce();
      expect(callback.restored).not.toHaveBeenCalled();
      return pending.promise;
    });
    const observed = observe(callback, {
      apiBaseUrl: "https://navox.example/api/v1",
    });
    expect(observed.checking).toHaveBeenCalledOnce();
    expect(access.restore).toHaveBeenCalledWith("https://navox.example/api/v1");
    expect(observed.revoking).not.toHaveBeenCalled();
    pending.resolve(accountA);
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountA);
  });

  it("supports a lightweight injected restoration without loading Google connections", async () => {
    const restoreAccess = vi.fn().mockResolvedValue(accountB);
    const observed = observe(callbacks(), { restoreAccess });
    await settle();
    expect(restoreAccess).toHaveBeenCalledOnce();
    expect(access.restore).not.toHaveBeenCalled();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountB);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("ignores an older owner's response after a newer session check", async () => {
    const first = deferred<AccountAccess>();
    const second = deferred<AccountAccess>();
    access.restore
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const observed = observe();
    SessionChannel.receive("changed");
    expect(observed.revoking).toHaveBeenCalledOnce();
    second.resolve(accountB);
    await settle();
    first.resolve(accountA);
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountB);
  });

  it("invalidates in-flight success without starting a new check", async () => {
    const pending = deferred<AccountAccess>();
    access.restore.mockReturnValue(pending.promise);
    const observed = observe();
    observed.observer.invalidate();
    pending.resolve(accountA);
    await settle();
    expect(access.restore).toHaveBeenCalledOnce();
    expect(observed.restored).not.toHaveBeenCalled();
    expect(observed.unavailable).not.toHaveBeenCalled();
  });

  it("ignores stale failures rather than clearing a newer verified owner", async () => {
    const old = deferred<AccountAccess>();
    access.restore
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(accountB);
    const observed = observe();
    observed.observer.refresh();
    await settle();
    old.reject(new Error("Old request private details"));
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountB);
    expect(observed.unavailable).not.toHaveBeenCalled();
  });

  it("reports only the current unavailable check and supports verified signed-out access", async () => {
    access.restore.mockRejectedValueOnce(new Error("private transport detail"));
    const observed = observe();
    await settle();
    expect(observed.unavailable).toHaveBeenCalledExactlyOnceWith();
    access.restore.mockResolvedValueOnce(signedOut);
    observed.observer.refresh();
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(signedOut);
  });

  it("disposes idempotently and cannot receive later successes, failures, or notifications", async () => {
    const pending = deferred<AccountAccess>();
    access.restore.mockReturnValue(pending.promise);
    const observed = observe();
    observed.observer.dispose();
    observed.observer.dispose();
    visible("hidden");
    visible("visible");
    notifyAccountSessionChanged();
    pending.reject(new Error("Late response"));
    await settle();
    observed.observer.refresh();
    expect(observed.checking).toHaveBeenCalledOnce();
    expect(observed.restored).not.toHaveBeenCalled();
    expect(observed.unavailable).not.toHaveBeenCalled();
    expect(SessionChannel.open.size).toBe(0);
  });

  it("uses one shared channel until the last observer disposes", async () => {
    const first = observe();
    const second = observe();
    expect(SessionChannel.open.size).toBe(1);
    first.observer.dispose();
    expect(SessionChannel.open.size).toBe(1);
    const a = first.checking.mock.calls.length;
    notifyAccountSessionChanged();
    expect(first.checking).toHaveBeenCalledTimes(a);
    expect(second.revoking).toHaveBeenCalledOnce();
    second.observer.dispose();
    expect(SessionChannel.open.size).toBe(0);
    await settle();
  });
});

describe("visibility and synchronous mutation holds", () => {
  it("starts hidden with synchronous checking and performs no restoration until visible", async () => {
    visibility = "hidden";
    const observed = observe();
    expect(observed.checking).toHaveBeenCalledOnce();
    expect(access.restore).not.toHaveBeenCalled();
    visible("visible");
    expect(observed.checking).toHaveBeenCalledTimes(2);
    expect(access.restore).toHaveBeenCalledOnce();
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountA);
  });

  it("suspends synchronously on hide, ignores pending access, and rechecks on visibility", async () => {
    const pending = deferred<AccountAccess>();
    access.restore
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(accountB);
    const observed = observe();
    visible("hidden");
    expect(observed.checking).toHaveBeenCalledTimes(2);
    pending.resolve(accountA);
    await settle();
    expect(observed.restored).not.toHaveBeenCalled();
    expect(observed.revoking).not.toHaveBeenCalled();
    visible("visible");
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountB);
  });

  it("revokes and holds every same-document consumer before mutation I/O begins", async () => {
    const old = deferred<AccountAccess>();
    access.restore.mockReturnValue(old.promise);
    const first = observe();
    const second = observe();
    const change = changing();
    expect(first.revoking).toHaveBeenCalledOnce();
    expect(second.revoking).toHaveBeenCalledOnce();
    expect(first.checking).toHaveBeenCalledTimes(2);
    expect(second.checking).toHaveBeenCalledTimes(2);
    old.resolve(accountA);
    visible("hidden");
    visible("visible");
    first.observer.refresh();
    SessionChannel.receive("changed");
    await settle();
    expect(access.restore).toHaveBeenCalledTimes(2);
    expect(first.restored).not.toHaveBeenCalled();
    expect(second.restored).not.toHaveBeenCalled();
    access.restore.mockResolvedValue(accountB);
    notifyAccountSessionChanged(change);
    expect(first.checking).toHaveBeenCalledTimes(7);
    await settle();
    expect(first.restored).toHaveBeenCalledExactlyOnceWith(accountB);
    expect(second.restored).toHaveBeenCalledExactlyOnceWith(accountB);
  });

  it("refreshes this document even without BroadcastChannel and terminally revokes on change", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const observed = observe();
    await settle();
    access.restore.mockResolvedValue(accountB);
    const change = changing();
    expect(observed.revoking).toHaveBeenCalledOnce();
    notifyAccountSessionChanged(change);
    expect(observed.revoking).toHaveBeenCalledTimes(2);
    await settle();
    expect(observed.restored).toHaveBeenLastCalledWith(accountB);
    expect(access.restore).toHaveBeenCalledTimes(2);
  });

  it("deduplicates its own broadcast after synchronous same-document refresh", async () => {
    const observed = observe();
    await settle();
    notifyAccountSessionChanged();
    expect(observed.revoking).toHaveBeenCalledOnce();
    expect(access.restore).toHaveBeenCalledTimes(2);
    await settle();
    expect(observed.revoking).toHaveBeenCalledOnce();
    expect(access.restore).toHaveBeenCalledTimes(2);
    expect(JSON.stringify(SessionChannel.posted)).not.toMatch(
      /owner-a|example.test|workspace-/u,
    );
  });

  it("requires every concurrent mutation's own completion token", async () => {
    const observed = observe();
    await settle();
    observed.restored.mockClear();
    const first = changing();
    const second = changing();
    notifyAccountSessionChanged(first);
    notifyAccountSessionChanged(first);
    notifyAccountSessionChanged();
    observed.observer.refresh();
    await settle();
    expect(access.restore).toHaveBeenCalledOnce();
    expect(observed.restored).not.toHaveBeenCalled();
    notifyAccountSessionChanged(second);
    await settle();
    expect(access.restore).toHaveBeenCalledTimes(2);
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountA);
  });

  it("a newly mounted observer inherits an ongoing same-document mutation hold", async () => {
    const change = changing();
    const observed = observe();
    expect(observed.checking).toHaveBeenCalledOnce();
    expect(access.restore).not.toHaveBeenCalled();
    notifyAccountSessionChanged(change);
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountA);
  });

  it("changed while hidden keeps checking and waits for a visible revalidation", async () => {
    observe();
    await settle();
    visible("hidden");
    const change = changing();
    notifyAccountSessionChanged(change);
    await settle();
    expect(access.restore).toHaveBeenCalledOnce();
    visible("visible");
    await settle();
    expect(access.restore).toHaveBeenCalledTimes(2);
  });
});

describe("bounded checking watchdog", () => {
  it("ordinary long-hidden suspension preserves same-owner resumption without terminal failure", async () => {
    vi.useFakeTimers();
    const observed = observe();
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountA);
    visible("hidden");
    await vi.advanceTimersByTimeAsync(90_000);
    expect(access.restore).toHaveBeenCalledOnce();
    expect(observed.unavailable).not.toHaveBeenCalled();
    expect(observed.revoking).not.toHaveBeenCalled();
    visible("visible");
    await settle();
    expect(observed.restored).toHaveBeenCalledTimes(2);
    expect(observed.restored).toHaveBeenLastCalledWith(accountA);
    expect(observed.unavailable).not.toHaveBeenCalled();
    expect(observed.revoking).not.toHaveBeenCalled();
  });

  it("an initial hidden mount attempts no proof and has no terminal checking timeout", async () => {
    vi.useFakeTimers();
    visibility = "hidden";
    const observed = observe();
    await vi.advanceTimersByTimeAsync(90_000);
    expect(observed.checking).toHaveBeenCalledOnce();
    expect(access.restore).not.toHaveBeenCalled();
    expect(observed.unavailable).not.toHaveBeenCalled();
    expect(observed.revoking).not.toHaveBeenCalled();
    visible("visible");
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountA);
  });

  it("hiding cancels the pending proof deadline without releasing a later stale response", async () => {
    vi.useFakeTimers();
    const pending = deferred<AccountAccess>();
    access.restore
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(accountB);
    const observed = observe();
    await vi.advanceTimersByTimeAsync(20_000);
    visible("hidden");
    await vi.advanceTimersByTimeAsync(90_000);
    pending.resolve(accountA);
    await settle();
    expect(observed.restored).not.toHaveBeenCalled();
    expect(observed.unavailable).not.toHaveBeenCalled();
    expect(observed.revoking).not.toHaveBeenCalled();
    visible("visible");
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountB);
  });

  it("reports a lost remote hold after thirty seconds without releasing it or polling", async () => {
    vi.useFakeTimers();
    const observed = observe();
    await settle();
    observed.restored.mockClear();
    observed.revoking.mockClear();
    remote(1, ["lost-remote-change"], "session-changing");
    expect(observed.revoking).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(29_999);
    expect(observed.unavailable).not.toHaveBeenCalled();
    // Activity must not indefinitely postpone the original checking deadline.
    observed.observer.refresh();
    visible("hidden");
    visible("visible");
    await vi.advanceTimersByTimeAsync(1);
    expect(observed.unavailable).toHaveBeenCalledExactlyOnceWith();
    expect(observed.revoking).toHaveBeenCalledTimes(2);
    expect(access.restore).toHaveBeenCalledOnce();
    observed.observer.refresh();
    notifyAccountSessionChanged();
    await settle();
    expect(access.restore).toHaveBeenCalledOnce();
    expect(observed.restored).not.toHaveBeenCalled();
  });

  it("fences a timed-out restore so its late success cannot restore authority", async () => {
    vi.useFakeTimers();
    const pending = deferred<AccountAccess>();
    access.restore.mockReturnValueOnce(pending.promise);
    const observed = observe();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(observed.unavailable).toHaveBeenCalledOnce();
    expect(observed.revoking).toHaveBeenCalledOnce();
    pending.resolve(accountA);
    await settle();
    expect(observed.restored).not.toHaveBeenCalled();
    expect(access.restore).toHaveBeenCalledOnce();
    access.restore.mockResolvedValueOnce(accountB);
    observed.observer.refresh();
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountB);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(observed.unavailable).toHaveBeenCalledOnce();
  });

  it("an older timeout cannot affect a newer successful verification", async () => {
    vi.useFakeTimers();
    const old = deferred<AccountAccess>();
    access.restore
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(accountB);
    const observed = observe();
    await vi.advanceTimersByTimeAsync(29_000);
    observed.observer.refresh();
    await settle();
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountB);
    await vi.advanceTimersByTimeAsync(90_000);
    old.reject(new Error("Stale transport failure"));
    await settle();
    expect(observed.unavailable).not.toHaveBeenCalled();
    expect(observed.revoking).not.toHaveBeenCalled();
  });

  it.each(["dispose", "invalidate"] as const)(
    "clears a pending watchdog on %s",
    async (method) => {
      vi.useFakeTimers();
      const pending = deferred<AccountAccess>();
      access.restore.mockReturnValueOnce(pending.promise);
      const observed = observe();
      observed.observer[method]();
      await vi.advanceTimersByTimeAsync(90_000);
      pending.resolve(accountA);
      await settle();
      expect(observed.unavailable).not.toHaveBeenCalled();
      expect(observed.revoking).not.toHaveBeenCalled();
      expect(observed.restored).not.toHaveBeenCalled();
    },
  );

  it("clears the watchdog after an authoritative unavailable response", async () => {
    vi.useFakeTimers();
    access.restore.mockRejectedValueOnce(
      new Error("Synthetic offline failure"),
    );
    const observed = observe();
    await settle();
    expect(observed.unavailable).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(90_000);
    expect(observed.unavailable).toHaveBeenCalledOnce();
    expect(observed.revoking).not.toHaveBeenCalled();
  });

  it("invalidate then refresh gives a new proof its own deadline instead of the old one", async () => {
    vi.useFakeTimers();
    const old = deferred<AccountAccess>();
    const current = deferred<AccountAccess>();
    access.restore
      .mockReturnValueOnce(old.promise)
      .mockReturnValueOnce(current.promise);
    const observed = observe();
    await vi.advanceTimersByTimeAsync(20_000);
    observed.observer.invalidate();
    observed.observer.refresh();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(observed.unavailable).not.toHaveBeenCalled();
    current.resolve(accountB);
    await settle();
    old.resolve(accountA);
    await settle();
    await vi.advanceTimersByTimeAsync(90_000);
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountB);
    expect(observed.unavailable).not.toHaveBeenCalled();
    expect(observed.revoking).not.toHaveBeenCalled();
  });
});

describe("bounded cross-document mutation snapshots", () => {
  it("holds until remote completion and ignores reordered or duplicate snapshots", async () => {
    const observed = observe();
    await settle();
    observed.restored.mockClear();
    remote(2, ["change-a", "change-b"], "session-changing");
    remote(3, ["change-b"]);
    remote(1, ["change-a"], "session-changing");
    remote(3, []);
    observed.observer.refresh();
    await settle();
    expect(access.restore).toHaveBeenCalledOnce();
    expect(observed.restored).not.toHaveBeenCalled();
    remote(4, []);
    await settle();
    expect(access.restore).toHaveBeenCalledTimes(2);
    expect(observed.restored).toHaveBeenCalledExactlyOnceWith(accountA);
  });

  it("one tab cannot release another tab's mutation hold", async () => {
    const observed = observe();
    await settle();
    remote(1, ["one"], "session-changing", "tab-one");
    remote(1, ["two"], "session-changing", "tab-two");
    remote(2, [], "session-changed", "tab-one");
    await settle();
    expect(access.restore).toHaveBeenCalledOnce();
    remote(2, [], "session-changed", "tab-two");
    await settle();
    expect(access.restore).toHaveBeenCalledTimes(2);
    expect(observed.revoking).toHaveBeenCalledTimes(4);
  });

  it("retains legacy changing/changed broadcasts while suspending visibility checks", async () => {
    const observed = observe();
    await settle();
    SessionChannel.receive("session-changing");
    visible("hidden");
    visible("visible");
    await settle();
    expect(access.restore).toHaveBeenCalledOnce();
    SessionChannel.receive("session-changed");
    await settle();
    expect(access.restore).toHaveBeenCalledTimes(2);
    expect(observed.revoking).toHaveBeenCalledTimes(2);
  });

  it("malformed or unrelated channel messages cannot release an existing legacy hold", async () => {
    const observed = observe();
    await settle();
    SessionChannel.receive("session-changing");
    SessionChannel.receive({ unexpected: "payload" });
    SessionChannel.receive("unrelated-message");
    SessionChannel.receive({
      schema: "navox-account-session.v1",
      phase: "session-changed",
    });
    observed.observer.refresh();
    await settle();
    expect(access.restore).toHaveBeenCalledOnce();
    SessionChannel.receive("session-changed");
    await settle();
    expect(access.restore).toHaveBeenCalledTimes(2);
  });

  it("raw same-document hook events also hold visibility checks until changed", async () => {
    const observed = observe();
    await settle();
    window.dispatchEvent(new Event(ACCOUNT_SESSION_CHANGING_EVENT));
    observed.observer.refresh();
    visible("hidden");
    visible("visible");
    await settle();
    expect(access.restore).toHaveBeenCalledOnce();
    window.dispatchEvent(new Event(ACCOUNT_SESSION_CHANGED_EVENT));
    await settle();
    expect(access.restore).toHaveBeenCalledTimes(2);
    expect(observed.revoking).toHaveBeenCalledTimes(2);
  });

  it("does not silently expire a remote mutation hold and recovers after a full observer reload", async () => {
    const observed = observe();
    await settle();
    remote(1, ["still-running"], "session-changing");
    observed.observer.refresh();
    await settle();
    expect(access.restore).toHaveBeenCalledOnce();
    observed.observer.dispose();
    const reloaded = observe();
    await settle();
    expect(access.restore).toHaveBeenCalledTimes(2);
    expect(reloaded.restored).toHaveBeenCalledExactlyOnceWith(accountA);
  });

  it("fails closed with bounded state after an excessive number of origin contexts", async () => {
    const observed = observe();
    await settle();
    for (let index = 0; index < 65; index += 1)
      remote(1, [], "session-changed", `tab-${index}`);
    observed.observer.refresh();
    await settle();
    expect(observed.restored).toHaveBeenCalledOnce();
    const checks = access.restore.mock.calls.length;
    remote(2, [], "session-changed", "tab-0");
    observed.observer.refresh();
    await settle();
    expect(access.restore).toHaveBeenCalledTimes(checks);
  });

  it("still performs local checks if channel construction is unavailable", async () => {
    vi.stubGlobal(
      "BroadcastChannel",
      class {
        constructor() {
          throw new Error("Disabled");
        }
      },
    );
    const observed = observe();
    await settle();
    const change = changing();
    notifyAccountSessionChanged(change);
    await settle();
    expect(observed.restored).toHaveBeenCalledTimes(2);
    expect(observed.revoking).toHaveBeenCalledTimes(2);
  });
});
