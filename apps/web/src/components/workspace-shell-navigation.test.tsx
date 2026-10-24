// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountAccess } from "../lib/account-access";
import { observeAccountSession } from "../lib/account-session";
import { WorkspaceShell } from "./workspace-shell";

const router = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("./student-voice-launcher", () => ({
  StudentVoiceLauncher: () => null,
}));

const access: AccountAccess = {
  account: {
    id: "10000000-0000-4000-8000-000000000001",
    email: "student@example.test",
    display_name: "Synthetic student",
    workspace: {
      id: "20000000-0000-4000-8000-000000000001",
      name: "Personal",
      workspace_type: "personal",
    },
  },
  connections: [],
  connectionsError: "",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { resolve, promise };
}

let host: HTMLDivElement;
let root: Root;
let observer: ReturnType<typeof observeAccountSession> | undefined;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  for (const fn of Object.values(router)) fn.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  observer?.dispose();
  observer = undefined;
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function render(onSignOut?: () => Promise<void> | void) {
  act(() => {
    root.render(
      <WorkspaceShell current="Today" onSignOut={onSignOut}>
        Your day
      </WorkspaceShell>,
    );
  });
}

function signOut() {
  const button = Array.from(host.querySelectorAll("button")).find(
    (element) => element.textContent?.trim() === "Sign out",
  );
  if (!button) throw new Error("Missing sign-out control");
  act(() => button.click());
  return button;
}

async function watch(order: string[]) {
  const restoreAccess = vi.fn(async () => access);
  observer = observeAccountSession(
    {
      checking: () => order.push("checking"),
      restored: () => order.push("restored"),
      unavailable: () => order.push("unavailable"),
      revoking: () => order.push("revoked"),
    },
    { restoreAccess },
  );
  await act(async () => {});
  expect(restoreAccess).toHaveBeenCalledTimes(1);
  return restoreAccess;
}

describe("persistent-shell client navigation", () => {
  it.each([{ metaKey: true }, { ctrlKey: true }])(
    "uses the client router for workspace search shortcut %j",
    (modifier) => {
      render();
      const shortcut = new KeyboardEvent("keydown", {
        key: "k",
        bubbles: true,
        cancelable: true,
        ...modifier,
      });
      act(() => document.dispatchEvent(shortcut));
      expect(shortcut.defaultPrevented).toBe(true);
      expect(router.push).toHaveBeenCalledExactlyOnceWith("/navox/search");
      expect(router.replace).not.toHaveBeenCalled();
      expect(host.textContent).toContain("Your day");
    },
  );

  it("does not intercept plain typing and removes its listener on unmount", () => {
    render();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "k" }));
    expect(router.push).not.toHaveBeenCalled();
    act(() => root.unmount());
    root = createRoot(host);
    document.dispatchEvent(
      new KeyboardEvent("keydown", { key: "k", ctrlKey: true }),
    );
    expect(router.push).not.toHaveBeenCalled();
  });
});

describe("fallback logout revocation", () => {
  it("revokes before fetch, holds session restoration during I/O, then navigates with the router", async () => {
    const order: string[] = [];
    const restoreAccess = await watch(order);
    const response = deferred<Response>();
    const fetch = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      order.push("fetch");
      expect(order.indexOf("revoked")).toBeLessThan(order.indexOf("fetch"));
      expect(init).toMatchObject({ method: "POST", credentials: "include" });
      expect(init?.signal).toBeDefined();
      return response.promise;
    });
    vi.stubGlobal("fetch", fetch);
    const deadline = vi.spyOn(AbortSignal, "timeout");
    render();
    const control = signOut();
    expect(control.disabled).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(deadline).toHaveBeenCalledExactlyOnceWith(10_000);
    observer?.refresh();
    await act(async () => {});
    expect(restoreAccess).toHaveBeenCalledTimes(1);
    await act(async () =>
      response.resolve(new Response(null, { status: 204 })),
    );
    expect(router.replace).toHaveBeenCalledExactlyOnceWith("/");
    expect(router.refresh).toHaveBeenCalledTimes(1);
    expect(router.push).not.toHaveBeenCalled();
    expect(restoreAccess).toHaveBeenCalledTimes(2);
    expect(order.filter((value) => value === "revoked")).toHaveLength(2);
  });

  it("completes the revocation token after a failed logout so a fresh check can restore the account", async () => {
    const order: string[] = [];
    const restoreAccess = await watch(order);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 500 })),
    );
    render();
    signOut();
    await act(async () => {});
    expect(host.textContent).toContain(
      "We couldn’t sign you out. Please try again.",
    );
    expect(restoreAccess).toHaveBeenCalledTimes(2);
    expect(order.filter((value) => value === "revoked")).toHaveLength(2);
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("aborts a stalled logout after its bounded signal and releases the exact mutation hold", async () => {
    const order: string[] = [];
    const restoreAccess = await watch(order);
    const timeout = new AbortController();
    vi.spyOn(AbortSignal, "timeout").mockReturnValue(timeout.signal);
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new Error("Logout timed out.")),
            );
          }),
      ),
    );
    render();
    signOut();
    expect(order).toContain("revoked");
    await act(async () => timeout.abort());
    expect(host.textContent).toContain(
      "We couldn’t sign you out. Please try again.",
    );
    expect(restoreAccess).toHaveBeenCalledTimes(2);
    expect(router.replace).not.toHaveBeenCalled();
  });

  it("delegates supplied sign-out handlers without issuing a second fetch or mutation notification", async () => {
    const order: string[] = [];
    await watch(order);
    const handler = vi.fn(async () => {});
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    render(handler);
    signOut();
    await act(async () => {});
    expect(handler).toHaveBeenCalledTimes(1);
    expect(fetch).not.toHaveBeenCalled();
    expect(order).not.toContain("revoked");
    expect(router.replace).not.toHaveBeenCalled();
  });
});
