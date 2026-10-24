// @vitest-environment jsdom

import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Account, AccountAccess } from "../lib/account-access";
import { observeAccountSession } from "../lib/account-session";

vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("../lib/product-features", () => ({ productFeatures: {} }));
vi.mock("./navox-cinematic", () => ({
  NavoXCinematic: ({ children }: { children: ReactNode }) =>
    createElement("div", null, children),
}));
vi.mock("./today-workspace", () => ({
  TodayWorkspace: ({
    account,
    onSignOut,
  }: {
    account: Account;
    onSignOut: () => Promise<void>;
  }) =>
    createElement(
      "main",
      null,
      createElement("p", null, account.display_name),
      createElement(
        "button",
        { type: "button", onClick: onSignOut },
        "Sign out",
      ),
    ),
}));
vi.mock("./student-notifications", () => ({
  StudentNotifications: () => null,
}));
vi.mock("./student-time-lookup", () => ({ StudentTimeLookup: () => null }));

import { AccountWorkspace } from "./account-workspace";
import { StudentClassesWorkspace } from "./student-classes";

const account: Account = {
  id: "22222222-2222-4222-8222-222222222222",
  email: "boundary-student@example.test",
  display_name: "Synthetic student",
  workspace: {
    id: "11111111-1111-4111-8111-111111111111",
    name: "Synthetic personal workspace",
    workspace_type: "personal",
  },
};
const profile = {
  school_name: "Synthetic school",
  lms: "none",
  display_name: "Synthetic student",
  timezone: "UTC",
  term_start: "2026-08-20",
  term_end: "2026-12-20",
  revision: 1,
};
const timetable = {
  server_now: "2026-10-19T12:00:00Z",
  timezone: "UTC",
  date: "2026-10-19",
  days: 7,
  courses: [],
  fixed_blocks: [],
  breaks: [],
  exceptions: [],
  occurrences: [],
  fixed_occurrences: [],
  conflicts: [],
  warnings: [],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

let host: HTMLDivElement;
let root: Root;
let currentAccount: Account | null;
let observer: ReturnType<typeof observeAccountSession>;
let restore: ReturnType<typeof vi.fn<() => Promise<AccountAccess>>>;
let revoking: ReturnType<typeof vi.fn<() => void>>;
let order: string[];
let pending: ReturnType<typeof deferred<Response>>[];
let mutate: (path: string, options: RequestInit) => Promise<Response>;
let network: ReturnType<typeof vi.fn>;

async function settle() {
  for (let index = 0; index < 20; index += 1) await Promise.resolve();
}

function access(): AccountAccess {
  return { account: currentAccount, connections: [], connectionsError: "" };
}

function mutation() {
  const operation = deferred<Response>();
  pending.push(operation);
  return operation;
}

async function mount(element: ReactNode) {
  await act(async () => {
    root.render(element);
    await settle();
  });
}

function button(name: string) {
  const found = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
    (item) => item.textContent?.trim() === name,
  );
  if (!found) throw new Error(`Missing UI button ${name}`);
  return found;
}

function click(element: HTMLElement) {
  act(() => element.click());
}

function input(selector: string, value: string) {
  const field = host.querySelector<HTMLInputElement>(selector);
  if (!field) throw new Error(`Missing field ${selector}`);
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  if (!setter) throw new Error("Missing native input setter");
  act(() => {
    setter.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function submitLogin() {
  click(button("Sign in"));
  input('input[type="email"]', account.email);
  input('input[type="password"]', "synthetic-password");
  const form = host.querySelector<HTMLFormElement>("form");
  if (!form) throw new Error("Missing actual AccountWorkspace form");
  act(() =>
    form.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    ),
  );
}

function prepareReset() {
  const summary = [...host.querySelectorAll("summary")].find(
    (item) => item.textContent === "Forget my student data",
  );
  if (!summary) throw new Error("Missing actual reset disclosure");
  click(summary);
  const checkbox = summary
    .closest("details")
    ?.querySelector<HTMLInputElement>('input[type="checkbox"]');
  if (!checkbox) throw new Error("Missing reset confirmation checkbox");
  expect(button("Forget student data").disabled).toBe(true);
  click(checkbox);
  expect(checkbox.checked).toBe(true);
  expect(button("Forget student data").disabled).toBe(false);
}

async function assertHeldWhilePending(previousChecks: number) {
  observer.refresh();
  document.dispatchEvent(new Event("visibilitychange"));
  await act(async () => settle());
  expect(restore).toHaveBeenCalledTimes(previousChecks);
}

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  currentAccount = null;
  order = [];
  pending = [];
  revoking = vi.fn<() => void>(() => {
    order.push("revoking");
  });
  restore = vi.fn(async () => {
    order.push("restore");
    return access();
  });
  observer = observeAccountSession(
    { checking: vi.fn(), restored: vi.fn(), unavailable: vi.fn(), revoking },
    { restoreAccess: restore },
  );
  mutate = async (path) => {
    throw new Error(`Unexpected mutation: ${path}`);
  };
  network = vi.fn(async (url: string, options: RequestInit = {}) => {
    const path = new URL(url).pathname;
    if ((options.method ?? "GET") !== "GET") return mutate(path, options);
    if (path.endsWith("/auth/me"))
      return currentAccount
        ? Response.json(currentAccount)
        : new Response(null, { status: 401 });
    if (path.endsWith("/connections/google")) return Response.json([]);
    if (path.endsWith("/student/profile")) return Response.json(profile);
    if (path.endsWith("/student/courses"))
      return Response.json({ courses: [] });
    if (path.endsWith("/student/timetable")) return Response.json(timetable);
    throw new Error(`Unexpected read: ${path}`);
  });
  vi.stubGlobal("fetch", network);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => {
    for (const operation of pending)
      operation.resolve(new Response(null, { status: 503 }));
    await settle();
    root.unmount();
    await settle();
  });
  observer.dispose();
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const outcomes = ["success", "rejection", "transport"] as const;

describe("actual account UI revokes before auth cookie mutations", () => {
  it.each(outcomes)(
    "login revokes before network and releases its exact hold after %s",
    async (outcome) => {
      await mount(createElement(AccountWorkspace));
      const operation = mutation();
      let posted = false;
      mutate = (path, options) => {
        expect(path).toBe("/api/v1/auth/login");
        expect(options.credentials).toBe("include");
        expect(JSON.parse(String(options.body))).toEqual({
          email: account.email,
          password: "synthetic-password",
        });
        expect(revoking).toHaveBeenCalledOnce();
        order.push("network");
        posted = true;
        return operation.promise;
      };
      const checks = restore.mock.calls.length;
      order = [];
      submitLogin();
      expect(posted).toBe(true);
      expect(order.slice(0, 2)).toEqual(["revoking", "network"]);
      await assertHeldWhilePending(checks);
      await act(async () => {
        if (outcome === "success") {
          currentAccount = account;
          operation.resolve(Response.json(account));
        } else if (outcome === "rejection") {
          operation.resolve(
            Response.json(
              { detail: "Synthetic login rejected" },
              { status: 401 },
            ),
          );
        } else operation.reject(new TypeError("Synthetic offline failure"));
        await settle();
      });
      expect(restore).toHaveBeenCalledTimes(checks + 1);
      expect(revoking).toHaveBeenCalledTimes(2);
      if (outcome === "success")
        expect(host.textContent).toContain("Synthetic student");
      else expect(host.querySelector('input[type="email"]')).not.toBeNull();
    },
  );

  it.each(outcomes)(
    "logout revokes before network and releases its exact hold after %s",
    async (outcome) => {
      currentAccount = account;
      await mount(createElement(AccountWorkspace));
      const operation = mutation();
      let posted = false;
      mutate = (path, options) => {
        expect(path).toBe("/api/v1/auth/logout");
        expect(options.credentials).toBe("include");
        expect(options.signal).toBeInstanceOf(AbortSignal);
        expect(revoking).toHaveBeenCalledOnce();
        order.push("network");
        posted = true;
        return operation.promise;
      };
      const checks = restore.mock.calls.length;
      order = [];
      click(button("Sign out"));
      expect(posted).toBe(true);
      expect(order.slice(0, 2)).toEqual(["revoking", "network"]);
      await assertHeldWhilePending(checks);
      await act(async () => {
        if (outcome === "success") {
          currentAccount = null;
          operation.resolve(new Response(null, { status: 204 }));
        } else if (outcome === "rejection") {
          operation.resolve(new Response(null, { status: 503 }));
        } else operation.reject(new TypeError("Synthetic offline failure"));
        await settle();
      });
      expect(restore).toHaveBeenCalledTimes(checks + 1);
      expect(revoking).toHaveBeenCalledTimes(2);
      if (outcome === "success")
        expect(host.querySelector('input[type="email"]')).not.toBeNull();
      else expect(host.textContent).toContain("Synthetic student");
    },
  );
});

describe("actual confirmed student reset revokes before data mutation", () => {
  it.each(outcomes)(
    "preserves confirmed revision and releases its hold after %s",
    async (outcome) => {
      currentAccount = account;
      await mount(
        createElement(StudentClassesWorkspace, {
          settingsOnly: true,
          accountId: `${account.workspace.id}:${account.id}`,
        }),
      );
      prepareReset();
      const operation = mutation();
      let posted = false;
      mutate = (path, options) => {
        expect(path).toBe("/api/v1/student/data/reset");
        expect(options.credentials).toBe("include");
        expect(options.cache).toBe("no-store");
        const payload = JSON.parse(String(options.body));
        expect(payload.confirmed).toBe(true);
        expect(payload.revision).toBe(1);
        expect(payload.request_id).toMatch(
          /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/u,
        );
        expect(revoking).toHaveBeenCalledOnce();
        order.push("network");
        posted = true;
        return operation.promise;
      };
      const checks = restore.mock.calls.length;
      order = [];
      click(button("Forget student data"));
      expect(posted).toBe(true);
      expect(order.slice(0, 2)).toEqual(["revoking", "network"]);
      await assertHeldWhilePending(checks);
      await act(async () => {
        if (outcome === "success")
          operation.resolve(Response.json({ cleared: true }));
        else if (outcome === "rejection") {
          operation.resolve(
            Response.json(
              { detail: "Synthetic reset rejected" },
              { status: 409 },
            ),
          );
        } else operation.reject(new TypeError("Synthetic offline failure"));
        await settle();
      });
      expect(restore).toHaveBeenCalledTimes(checks + 1);
      expect(revoking).toHaveBeenCalledTimes(2);
    },
  );

  it("retries a rejected confirmed reset with the same request UUID and profile revision", async () => {
    currentAccount = account;
    await mount(
      createElement(StudentClassesWorkspace, {
        settingsOnly: true,
        accountId: `${account.workspace.id}:${account.id}`,
      }),
    );
    prepareReset();
    const payloads: unknown[] = [];
    mutate = async (path, options) => {
      expect(path).toBe("/api/v1/student/data/reset");
      payloads.push(JSON.parse(String(options.body)));
      expect(revoking.mock.calls.length).toBe(payloads.length * 2 - 1);
      return payloads.length === 1
        ? Response.json(
            { detail: "Synthetic first rejection" },
            { status: 409 },
          )
        : Response.json({ cleared: true });
    };
    await act(async () => {
      button("Forget student data").click();
      await settle();
    });
    expect(button("Forget student data").disabled).toBe(false);
    await act(async () => {
      button("Forget student data").click();
      await settle();
    });
    expect(payloads).toHaveLength(2);
    expect(payloads[0]).toEqual(payloads[1]);
    expect(payloads[0]).toMatchObject({ confirmed: true, revision: 1 });
    expect(revoking).toHaveBeenCalledTimes(4);
    // This checks the UI's reviewed request and session boundary, not database erasure.
    expect(restore).toHaveBeenCalledTimes(3);
  });
});
