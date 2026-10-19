// @vitest-environment jsdom

import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Account, AccountAccess } from "../lib/account-access";

const access = vi.hoisted(() => ({ restore: vi.fn() }));
vi.mock("../lib/account-access", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/account-access")>()),
  restoreAccountAccess: access.restore,
}));
vi.mock("../lib/product-features", () => ({
  productFeatures: { studentWhiteboard: true },
}));
vi.mock("next/dynamic", async () => {
  const { StudentWhiteboard } = await import("./student-whiteboard");
  return { default: () => StudentWhiteboard };
});
vi.mock("./workspace-shell", () => ({
  WorkspaceShell: ({
    accountName,
    onSignOut,
    children,
  }: {
    accountName: string;
    onSignOut: () => void;
    children: ReactNode;
  }) =>
    createElement(
      "main",
      null,
      createElement("p", null, accountName),
      createElement(
        "button",
        { type: "button", onClick: onSignOut },
        "Sign out",
      ),
      children,
    ),
}));
vi.mock("./navox-cinematic", () => ({
  NavoXCinematic: ({ children }: { children: ReactNode }) =>
    createElement("div", null, children),
}));

import { AccountWorkspace } from "./account-workspace";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const ownerA: Account = {
  id: "synthetic-owner-a",
  email: "owner-a@example.test",
  display_name: "Synthetic student A",
  workspace: {
    id: "synthetic-workspace-a",
    name: "Practice workspace A",
    workspace_type: "personal",
  },
};
const ownerB: Account = {
  ...ownerA,
  id: "synthetic-owner-b",
  email: "owner-b@example.test",
  display_name: "Synthetic student B",
  workspace: { ...ownerA.workspace, id: "synthetic-workspace-b" },
};

const restored = (account: Account): AccountAccess => ({
  account,
  connections: [],
  connectionsError: "",
});

class SessionChannel extends EventTarget {
  static open = new Set<SessionChannel>();

  constructor(readonly name: string) {
    super();
    SessionChannel.open.add(this);
  }

  // These tests explicitly drive the receiving observer. A local sign-out's
  // notification does not stand in for another verified account response.
  postMessage() {}

  close() {
    SessionChannel.open.delete(this);
  }

  static changed() {
    for (const channel of SessionChannel.open)
      channel.dispatchEvent(new MessageEvent("message", { data: "changed" }));
  }
}

let host: HTMLDivElement;
let root: Root;
let firstCheck: ReturnType<typeof deferred<AccountAccess>>;
let fetchCalls: ReturnType<typeof vi.fn>;

function button(name: string) {
  const result = Array.from(host.querySelectorAll("button")).find(
    (item) => item.textContent?.trim() === name,
  );
  if (!result) throw new Error(`Missing button: ${name}`);
  return result;
}

function click(element: HTMLElement) {
  act(() => element.click());
}

function board() {
  return host.querySelector('[aria-label="Whiteboard tutor"]');
}

function chooseCalculus() {
  const picker = host.querySelector<HTMLSelectElement>("#whiteboard-lesson");
  if (!picker) throw new Error("Missing lesson picker");
  act(() => {
    picker.value = "calculus-parts";
    picker.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function firstStep() {
  const step = host.querySelector<HTMLButtonElement>(
    'nav[aria-label="Lesson timeline"] button',
  );
  if (!step) throw new Error("Missing first step");
  return step;
}

function annotations() {
  const svg = host.querySelector<SVGSVGElement>(
    'svg[aria-label="Your pen annotations"]',
  );
  if (!svg) throw new Error("Missing annotation layer");
  return svg;
}

function markPrivateWork() {
  chooseCalculus();
  click(firstStep());
  click(button("u = x"));
  expect(button("u = x").getAttribute("aria-pressed")).toBe("true");
  click(button("Pen"));
  const svg = annotations();
  Object.defineProperty(svg, "setPointerCapture", {
    configurable: true,
    value: vi.fn(),
  });
  vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    width: 1120,
    height: 1000,
    right: 1120,
    bottom: 1000,
    toJSON: () => ({}),
  });
  const pointer = (type: string) => {
    const event = new MouseEvent(type, {
      bubbles: true,
      clientX: 50,
      clientY: 60,
      button: 0,
    });
    Object.defineProperties(event, {
      pointerId: { value: 7 },
      pointerType: { value: "pen" },
    });
    act(() => svg.dispatchEvent(event));
  };
  pointer("pointerdown");
  pointer("pointerup");
  expect(svg.querySelectorAll("path")).toHaveLength(1);
  click(button("Play lesson"));
  expect(vi.getTimerCount()).toBe(1);
}

function expectFreshBoard() {
  expect(board()).not.toBeNull();
  expect(host.textContent).toContain("One bit, one clock edge");
  expect(host.querySelectorAll("g[data-action-id]")).toHaveLength(0);
  expect(annotations().querySelectorAll("path")).toHaveLength(0);
  chooseCalculus();
  click(firstStep());
  expect(button("u = x").getAttribute("aria-pressed")).toBe("false");
  expect(host.textContent).not.toContain("That’s it.");
  expect(button("Play lesson").disabled).toBe(true);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", SessionChannel);
  SessionChannel.open.clear();
  fetchCalls = vi.fn(async (url: string, init?: RequestInit) => {
    if (
      url.endsWith("/auth/logout") &&
      init?.method === "POST" &&
      init.credentials === "include"
    )
      return new Response(null, { status: 204 });
    throw new Error(`Unexpected network operation: ${url}`);
  });
  vi.stubGlobal("fetch", fetchCalls);
  access.restore.mockReset();
  firstCheck = deferred<AccountAccess>();
  access.restore.mockReturnValueOnce(firstCheck.promise);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  act(() =>
    root.render(createElement(AccountWorkspace, { view: "whiteboard" })),
  );
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  expect(SessionChannel.open.size).toBe(0);
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("account-owned whiteboard lifecycle", () => {
  it("unmounts private work while checking, then opens a fresh board for the verified next owner", async () => {
    expect(board()).toBeNull();
    expect(host.textContent).toContain("Getting NavoX ready");
    await act(async () => firstCheck.resolve(restored(ownerA)));
    expect(host.textContent).toContain("Synthetic student A");
    markPrivateWork();
    const nextCheck = deferred<AccountAccess>();
    access.restore.mockReturnValueOnce(nextCheck.promise);
    act(() => SessionChannel.changed());
    expect(host.textContent).toContain("Checking your sign-in");
    expect(board()).toBeNull();
    expect(
      host.querySelector('[aria-label="Your pen annotations"]'),
    ).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(60_000));
    expect(board()).toBeNull();
    await act(async () => nextCheck.resolve(restored(ownerB)));
    expect(host.textContent).toContain("Synthetic student B");
    expect(host.textContent).not.toContain("Synthetic student A");
    expectFreshBoard();
    expect(fetchCalls).not.toHaveBeenCalled();
  });

  it("removes the board and pending timer on sign-out without retaining work after a new session", async () => {
    await act(async () => firstCheck.resolve(restored(ownerA)));
    markPrivateWork();
    await act(async () => button("Sign out").click());
    expect(board()).toBeNull();
    expect(host.textContent).toContain("Make yourself at home");
    expect(
      host.querySelector('[aria-label="Your pen annotations"]'),
    ).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    expect(fetchCalls).toHaveBeenCalledTimes(1);
    const newSession = deferred<AccountAccess>();
    access.restore.mockReturnValueOnce(newSession.promise);
    act(() => SessionChannel.changed());
    expect(board()).toBeNull();
    await act(async () => newSession.resolve(restored(ownerA)));
    expectFreshBoard();
    expect(fetchCalls).toHaveBeenCalledTimes(1);
  });

  it("keeps the board absent after a failed session recheck", async () => {
    await act(async () => firstCheck.resolve(restored(ownerA)));
    markPrivateWork();
    const failedCheck = deferred<AccountAccess>();
    access.restore.mockReturnValueOnce(failedCheck.promise);
    act(() => SessionChannel.changed());
    expect(board()).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    await act(async () =>
      failedCheck.reject(new Error("Synthetic check failed")),
    );
    expect(host.textContent).toContain("Your sign-in could not be checked");
    expect(host.textContent).toContain("Make yourself at home");
    expect(board()).toBeNull();
    act(() => vi.advanceTimersByTime(60_000));
    expect(board()).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    expect(fetchCalls).not.toHaveBeenCalled();
  });
});
