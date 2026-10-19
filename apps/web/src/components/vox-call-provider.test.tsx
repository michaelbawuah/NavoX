// @vitest-environment jsdom

import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountAccess } from "../lib/account-access";
import {
  notifyAccountSessionChanged,
  notifyAccountSessionChanging,
} from "../lib/account-session";
import type { VoxConnection, VoxTransportRequest } from "../lib/vox-call";
import {
  restoreVoxAccountAccess,
  useVoxCall,
  type VoxCallContextValue,
  VoxCallProvider,
} from "./vox-call-provider";

const ownerA = {
  id: "10000000-0000-4000-8000-000000000001",
  email: "synthetic-a@example.test",
  display_name: "Synthetic A",
  workspace: {
    id: "20000000-0000-4000-8000-000000000001",
    name: "Synthetic workspace",
    workspace_type: "personal",
  },
};
const ownerB = { ...ownerA, id: "10000000-0000-4000-8000-000000000002" };
const access = (account = ownerA): AccountAccess => ({
  account,
  connections: [],
  connectionsError: "Google is deliberately not part of a Vox identity check.",
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

class SessionChannel extends EventTarget {
  static channels = new Set<SessionChannel>();
  constructor(readonly name: string) {
    super();
    SessionChannel.channels.add(this);
  }
  postMessage(data: unknown) {
    for (const channel of SessionChannel.channels)
      if (channel !== this && channel.name === this.name)
        channel.dispatchEvent(new MessageEvent("message", { data }));
  }
  close() {
    SessionChannel.channels.delete(this);
  }
}

let latest: VoxCallContextValue | null = null;
let host: HTMLDivElement;
let root: Root;
let visibility: DocumentVisibilityState;
let online: boolean;

function Probe({ page = "today" }: { page?: string }) {
  latest = useVoxCall();
  return <p>{page}</p>;
}

function rig() {
  let serial = 0;
  let now = 0;
  const requests: VoxTransportRequest[] = [];
  const connections: ReturnType<typeof join>[] = [];
  function join(request: VoxTransportRequest) {
    return {
      owner: request.owner,
      callId: request.callId,
      setMicrophoneEnabled: vi.fn(),
      setSpeakerEnabled: vi.fn(),
      setWakeEnabled: vi.fn(),
      stopPlayback: vi.fn(),
      close: vi.fn<() => void | Promise<void>>(),
    } satisfies VoxConnection;
  }
  const transport = {
    available: true,
    localWakeAvailable: true,
    connect: vi.fn(async (request: VoxTransportRequest) => {
      requests.push(request);
      const connection = join(request);
      connections.push(connection);
      return connection;
    }),
  };
  const restoreAccess = vi.fn(async () => access());
  const configuration = {
    transport,
    restoreAccess,
    createCallId: () =>
      `30000000-0000-4000-8000-${String(++serial).padStart(12, "0")}`,
    now: () => now,
  };
  async function mount(page = "today", strict = false) {
    const tree = (
      <VoxCallProvider {...configuration}>
        <Probe key={page} page={page} />
      </VoxCallProvider>
    );
    await act(async () =>
      root.render(strict ? <StrictMode>{tree}</StrictMode> : tree),
    );
  }
  async function start() {
    const controller = latest?.controller;
    if (!controller) throw new Error("No mounted provider");
    await act(async () => controller.start());
    return controller;
  }
  return {
    configuration,
    transport,
    requests,
    connections,
    restoreAccess,
    mount,
    start,
    join,
    setNow: (value: number) => {
      now = value;
    },
  };
}

beforeEach(() => {
  latest = null;
  visibility = "visible";
  online = true;
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", SessionChannel);
  vi.spyOn(document, "visibilityState", "get").mockImplementation(
    () => visibility,
  );
  vi.spyOn(navigator, "onLine", "get").mockImplementation(() => online);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  SessionChannel.channels.clear();
});

describe("app-scoped Vox ownership", () => {
  it("has no context outside the opt-in owner and does no work during server rendering", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const html = renderToStaticMarkup(
      <VoxCallProvider>
        <Probe />
      </VoxCallProvider>,
    );
    expect(html).toContain("today");
    expect(latest).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
    renderToStaticMarkup(<Probe page="without provider" />);
    expect(latest).toBeNull();
  });

  it("checks identity but never starts a call on mount; the default transport stays unavailable", async () => {
    const restoreAccess = vi.fn(async () => access());
    await act(async () =>
      root.render(
        <VoxCallProvider restoreAccess={restoreAccess}>
          <Probe />
        </VoxCallProvider>,
      ),
    );
    expect(latest?.state.ownerVerified).toBe(true);
    expect(latest?.available).toBe(false);
    await act(async () => latest?.controller.start());
    expect(latest?.state.phase).toBe("unavailable");
    expect(JSON.stringify(latest?.state)).not.toContain(ownerA.email);
  });

  it("does not authorize a pending account check", async () => {
    const test = rig();
    const pending = deferred<AccountAccess>();
    test.restoreAccess.mockReturnValueOnce(pending.promise);
    await test.mount();
    await test.start();
    expect(test.transport.connect).not.toHaveBeenCalled();
    await act(async () => pending.resolve(access()));
    expect(latest?.state.ownerVerified).toBe(true);
    expect(test.transport.connect).not.toHaveBeenCalled();
  });

  it("keeps the same call when a route child unmounts and another page mounts", async () => {
    const test = rig();
    await test.mount();
    const controller = await test.start();
    const id = controller.current().callId;
    await act(async () => controller.setMinimized(true));
    await test.mount("classes");
    expect(latest?.controller).toBe(controller);
    expect(latest?.state.callId).toBe(id);
    expect(latest?.state.minimized).toBe(true);
    expect(test.connections[0].close).not.toHaveBeenCalled();
    expect(test.restoreAccess).toHaveBeenCalledTimes(1);
  });

  it("suspends frame and wake leases immediately while hidden, then restores the same verified call", async () => {
    const test = rig();
    await test.mount();
    const controller = await test.start();
    const pending = deferred<AccountAccess>();
    test.restoreAccess.mockReturnValueOnce(pending.promise);
    await act(async () => {
      visibility = "hidden";
      document.dispatchEvent(new Event("visibilitychange"));
      expect(test.requests[0].mayTransmitAudio()).toBe(false);
      expect(test.requests[0].mayPlayAudio()).toBe(false);
      expect(test.requests[0].mayListenForWake()).toBe(false);
    });
    expect(controller.current().phase).toBe("active");
    await act(async () => {
      visibility = "visible";
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(controller.current().ownerVerified).toBe(false);
    await act(async () => pending.resolve(access()));
    expect(controller.current().ownerVerified).toBe(true);
    expect(test.requests[0].mayTransmitAudio()).toBe(true);
    expect(test.connections[0].close).not.toHaveBeenCalled();
    expect(test.transport.connect).toHaveBeenCalledTimes(1);
  });

  it("ends a confirmed mutation before I/O and never revives that call after a failed same-owner reset", async () => {
    const test = rig();
    await test.mount();
    const controller = await test.start();
    let change!: ReturnType<typeof notifyAccountSessionChanging>;
    await act(async () => {
      change = notifyAccountSessionChanging();
      expect(test.requests[0].mayTransmitAudio()).toBe(false);
      expect(controller.current().callId).toBeNull();
      expect(test.connections[0].close).toHaveBeenCalledTimes(1);
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(test.restoreAccess).toHaveBeenCalledTimes(1);
    await act(async () => notifyAccountSessionChanged(change));
    expect(controller.current().ownerVerified).toBe(true);
    expect(controller.current().phase).toBe("ended");
    expect(test.transport.connect).toHaveBeenCalledTimes(1);
  });

  it("preserves a healthy call through a long hidden pause without treating it as a failed lookup", async () => {
    vi.useFakeTimers();
    const test = rig();
    await test.mount();
    const controller = await test.start();
    const id = controller.current().callId;
    await act(async () => {
      visibility = "hidden";
      document.dispatchEvent(new Event("visibilitychange"));
      test.setNow(90_000);
      vi.advanceTimersByTime(90_000);
    });
    expect(controller.current().callId).toBe(id);
    expect(controller.current().ownerVerified).toBe(false);
    expect(test.connections[0].close).not.toHaveBeenCalled();
    expect(test.restoreAccess).toHaveBeenCalledTimes(1);
    await act(async () => {
      visibility = "visible";
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(controller.current().callId).toBe(id);
    expect(controller.current().ownerVerified).toBe(true);
    expect(test.requests[0].mayTransmitAudio()).toBe(true);
    expect(test.connections[0].close).not.toHaveBeenCalled();
  });

  it("rejects an old identity reply that arrives after another account is verified", async () => {
    const test = rig();
    const old = deferred<AccountAccess>();
    test.restoreAccess
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(access(ownerB));
    await test.mount();
    await act(async () => {
      const change = notifyAccountSessionChanging();
      notifyAccountSessionChanged(change);
    });
    expect(latest?.state.owner?.userId).toBe(ownerB.id);
    await act(async () => old.resolve(access()));
    expect(latest?.state.owner?.userId).toBe(ownerB.id);
  });

  it("clears a live call when a fresh check identifies another workspace", async () => {
    const test = rig();
    await test.mount();
    await test.start();
    const changed = {
      ...ownerB,
      workspace: {
        ...ownerB.workspace,
        id: "20000000-0000-4000-8000-000000000002",
      },
    };
    test.restoreAccess.mockResolvedValueOnce(access(changed));
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(latest?.state.owner).toEqual({
      userId: changed.id,
      workspaceId: changed.workspace.id,
    });
    expect(latest?.state.callId).toBeNull();
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.connections[0].close).toHaveBeenCalledTimes(1);
    expect(test.transport.connect).toHaveBeenCalledTimes(1);
  });

  it("revokes the old controller when its host replaces transport configuration", async () => {
    const test = rig();
    await test.mount();
    const old = await test.start();
    const replacement = {
      ...test.transport,
      connect: vi.fn((request: VoxTransportRequest) =>
        test.transport.connect(request),
      ),
    };
    const pending = deferred<AccountAccess>();
    test.restoreAccess.mockReturnValueOnce(pending.promise);
    await act(async () =>
      root.render(
        <VoxCallProvider {...test.configuration} transport={replacement}>
          <Probe />
        </VoxCallProvider>,
      ),
    );
    expect(latest?.controller).not.toBe(old);
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.connections[0].close).toHaveBeenCalledTimes(1);
    expect(latest?.state.ownerVerified).toBe(false);
    await act(async () => pending.resolve(access()));
    expect(latest?.state.callId).toBeNull();
    expect(replacement.connect).not.toHaveBeenCalled();
  });

  it("terminates an unavailable or malformed fresh account rather than retaining the old owner", async () => {
    const test = rig();
    await test.mount();
    await test.start();
    test.restoreAccess.mockResolvedValueOnce({
      ...access(),
      account: { ...ownerA, workspace: null },
    } as unknown as AccountAccess);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(latest?.state.owner).toBeNull();
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.connections[0].close).toHaveBeenCalledTimes(1);
  });

  it("suspends offline and waits for a successful fresh online check", async () => {
    const test = rig();
    await test.mount();
    await test.start();
    await act(async () => {
      online = false;
      window.dispatchEvent(new Event("offline"));
    });
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    test.restoreAccess.mockRejectedValueOnce(
      new Error("synthetic offline failure"),
    );
    await act(async () => {
      online = true;
      window.dispatchEvent(new Event("online"));
    });
    expect(latest?.state.owner).toBeNull();
    expect(test.connections[0].close).toHaveBeenCalledTimes(1);
  });

  it("ends on document exit and releases a late join without inheriting it on pageshow", async () => {
    const test = rig();
    const joining = deferred<ReturnType<typeof test.join>>();
    test.transport.connect.mockImplementationOnce(async (request) => {
      test.requests.push(request);
      return joining.promise;
    });
    await test.mount();
    const controller = latest?.controller;
    const started = controller?.start();
    await act(async () => {
      await Promise.resolve();
    });
    const request = test.requests[0];
    await act(async () => window.dispatchEvent(new Event("pagehide")));
    expect(request.mayTransmitAudio()).toBe(false);
    const connection = test.join(request);
    await act(async () => {
      joining.resolve(connection);
      await started;
    });
    expect(connection.close).toHaveBeenCalledTimes(1);
    await act(async () => window.dispatchEvent(new Event("pageshow")));
    expect(controller?.current().ownerVerified).toBe(true);
    expect(controller?.current().callId).toBeNull();
  });

  it("ticks an ongoing call and stops every lease on unmount", async () => {
    vi.useFakeTimers();
    const test = rig();
    await test.mount();
    const controller = await test.start();
    test.setNow(120_000);
    await act(async () => vi.advanceTimersByTime(1_000));
    expect(controller.current().phase).toBe("standby");
    expect(controller.current().elapsedMs).toBe(120_000);
    await act(async () => root.render(<p>Owner removed</p>));
    expect(test.requests[0].mayListenForWake()).toBe(false);
    expect(test.connections[0].close).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("survives Strict Mode replay and ignores the first disposed owner's pending check", async () => {
    const test = rig();
    const old = deferred<AccountAccess>();
    test.restoreAccess
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(access(ownerB));
    await test.mount("today", true);
    expect(test.restoreAccess).toHaveBeenCalledTimes(2);
    expect(latest?.state.owner?.userId).toBe(ownerB.id);
    await act(async () => old.resolve(access()));
    expect(latest?.state.owner?.userId).toBe(ownerB.id);
    await test.start();
    expect(test.transport.connect).toHaveBeenCalledTimes(1);
  });
});

describe("Vox account-only identity checks", () => {
  it("makes one bounded no-store auth request and never lists Google connections", async () => {
    const request = vi.fn().mockResolvedValue(Response.json(ownerA));
    vi.stubGlobal("fetch", request);
    const result = await restoreVoxAccountAccess();
    expect(result.account?.id).toBe(ownerA.id);
    expect(result.connections).toEqual([]);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request.mock.calls[0][0]).toMatch(/\/auth\/me$/);
    expect(request.mock.calls[0][1]).toMatchObject({
      credentials: "include",
      cache: "no-store",
      signal: expect.any(AbortSignal),
    });
  });
  it.each([401, 403])(
    "clears an unauthorized identity (%s)",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response(null, { status })),
      );
      expect((await restoreVoxAccountAccess()).account).toBeNull();
    },
  );
  it.each([
    null,
    {},
    { id: ownerA.id, workspace: null },
    { id: "bad", workspace: ownerA.workspace },
  ])("rejects a malformed identity", async (value) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(value)));
    await expect(restoreVoxAccountAccess()).rejects.toThrow(
      "sign-in could not be checked",
    );
  });
});
