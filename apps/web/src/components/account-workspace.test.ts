import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type AccountAccess,
  restoreAccountAccess,
} from "../lib/account-access";
import { observeAccountSession } from "./account-workspace";

vi.mock("../lib/account-access", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/account-access")>()),
  restoreAccountAccess: vi.fn(),
}));

class SessionChannel extends EventTarget {
  static channels: SessionChannel[] = [];
  closed = false;
  constructor(public name: string) {
    super();
    SessionChannel.channels.push(this);
  }
  postMessage(data: unknown) {
    for (const channel of SessionChannel.channels) {
      if (channel !== this && !channel.closed && channel.name === this.name)
        channel.dispatchEvent(new MessageEvent("message", { data }));
    }
  }
  close() {
    this.closed = true;
  }
}

function access(id: string | null): AccountAccess {
  return {
    account: id
      ? {
          id,
          display_name: id,
          email: `${id}@example.test`,
          workspace: {
            id: `${id}-workspace`,
            name: id,
            workspace_type: "personal",
          },
        }
      : null,
    connections: [],
    connectionsError: "",
  };
}

function setup() {
  const surface = Object.assign(new EventTarget(), {
    visibilityState: "visible",
  });
  vi.stubGlobal("document", surface);
  vi.stubGlobal("window", new EventTarget());
  vi.stubGlobal("BroadcastChannel", SessionChannel);
  const callbacks = {
    checking: vi.fn(),
    restored: vi.fn(),
    unavailable: vi.fn(),
  };
  return { surface, callbacks };
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  SessionChannel.channels = [];
});

describe("account session revalidation", () => {
  it("masks the previous workspace on tab return until the current cookie account is verified", async () => {
    const { surface, callbacks } = setup();
    let resolveSwitch: (value: AccountAccess) => void = () => {};
    vi.mocked(restoreAccountAccess)
      .mockResolvedValueOnce(access("account-a"))
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSwitch = resolve;
          }),
      );
    const observer = observeAccountSession(callbacks);
    await vi.waitFor(() =>
      expect(callbacks.restored).toHaveBeenCalledWith(access("account-a")),
    );

    surface.visibilityState = "hidden";
    surface.dispatchEvent(new Event("visibilitychange"));
    surface.visibilityState = "visible";
    surface.dispatchEvent(new Event("visibilitychange"));
    expect(callbacks.checking).toHaveBeenCalledTimes(3);
    expect(callbacks.restored).toHaveBeenCalledTimes(1);
    resolveSwitch(access("account-b"));
    await vi.waitFor(() =>
      expect(callbacks.restored).toHaveBeenLastCalledWith(access("account-b")),
    );
    observer.dispose();
  });

  it("reacts to another tab's sign-out and ignores an earlier account lookup arriving late", async () => {
    const { callbacks } = setup();
    let resolveOld: (value: AccountAccess) => void = () => {};
    vi.mocked(restoreAccountAccess)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve;
          }),
      )
      .mockResolvedValueOnce(access(null));
    const observer = observeAccountSession(callbacks);
    const otherTab = new SessionChannel("navox-account-session");
    otherTab.postMessage("session-changed");
    await vi.waitFor(() =>
      expect(callbacks.restored).toHaveBeenCalledWith(access(null)),
    );
    resolveOld(access("account-a"));
    await Promise.resolve();
    expect(callbacks.restored).toHaveBeenCalledTimes(1);
    observer.dispose();
    otherTab.postMessage("session-changed");
    expect(restoreAccountAccess).toHaveBeenCalledTimes(2);
    expect(SessionChannel.channels[0].closed).toBe(true);
  });

  it("fails closed when the current session cannot be verified", async () => {
    const { callbacks } = setup();
    vi.mocked(restoreAccountAccess).mockRejectedValueOnce(
      new TypeError("offline"),
    );
    const observer = observeAccountSession(callbacks);
    await vi.waitFor(() =>
      expect(callbacks.unavailable).toHaveBeenCalledTimes(1),
    );
    expect(callbacks.checking).toHaveBeenCalledTimes(1);
    expect(callbacks.restored).not.toHaveBeenCalled();
    observer.dispose();
  });
});
