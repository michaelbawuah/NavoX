// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  type Account,
  type AccountAccess,
  authenticate,
  restoreAccountAccess,
} from "../lib/account-access";

vi.mock("next/dynamic", () => ({ default: () => () => null }));
vi.mock("../lib/product-features", () => ({ productFeatures: {} }));
vi.mock("../lib/account-access", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/account-access")>()),
  authenticate: vi.fn(),
  restoreAccountAccess: vi.fn(),
}));
vi.mock("./today-workspace", () => ({
  TodayWorkspace: ({ account, view }: { account: Account; view: string }) =>
    createElement(
      "main",
      { "aria-label": "Verified workspace" },
      `${account.display_name} · ${view}`,
    ),
}));

import { AccountWorkspace } from "./account-workspace";

const account: Account = {
  id: "22222222-2222-4222-8222-222222222222",
  email: "student@example.test",
  display_name: "Synthetic student",
  workspace: {
    id: "11111111-1111-4111-8111-111111111111",
    name: "Personal workspace",
    workspace_type: "personal",
  },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

function access(owner: Account | null): AccountAccess {
  return { account: owner, connections: [], connectionsError: "" };
}

let host: HTMLDivElement;
let root: Root;
let sessionCheck: ReturnType<typeof deferred<AccountAccess>>;

beforeEach(async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  sessionCheck = deferred<AccountAccess>();
  vi.mocked(restoreAccountAccess).mockReset();
  vi.mocked(authenticate).mockReset();
  vi.mocked(restoreAccountAccess).mockReturnValueOnce(sessionCheck.promise);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(createElement(AccountWorkspace)));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function modeButton(name: string) {
  const button = [
    ...host.querySelectorAll<HTMLButtonElement>(".auth-tabs button"),
  ].find((item) => item.textContent === name);
  if (!button) throw new Error(`Missing account mode: ${name}`);
  return button;
}

function input(selector: string, value: string) {
  const field = host.querySelector<HTMLInputElement>(selector);
  if (!field) throw new Error(`Missing account input: ${selector}`);
  const set = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )?.set;
  if (!set) throw new Error("Missing native input setter");
  act(() => {
    set.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("direct app account entry", () => {
  it("waits for the session check before showing either account access or a workspace", () => {
    expect(host.textContent).toContain("Getting NavoX ready…");
    expect(host.querySelector("form")).toBeNull();
    expect(host.querySelector(".account-entry")).toBeNull();
    expect(host.querySelector('[aria-label="Verified workspace"]')).toBeNull();
  });

  it("shows sign-in immediately for an anonymous visitor and keeps account creation available", async () => {
    await act(async () => sessionCheck.resolve(access(null)));

    const form = host.querySelector("form");
    expect(form).not.toBeNull();
    expect(form?.closest("dialog, [hidden]")).toBeNull();
    expect(host.querySelector("dialog")).toBeNull();
    expect(host.querySelector("h1")?.textContent).toBe("Welcome back");
    expect(modeButton("Sign in").getAttribute("aria-pressed")).toBe("true");
    expect(
      host.querySelector('input[autocomplete="current-password"]'),
    ).not.toBeNull();
    expect(host.querySelector('input[autocomplete="name"]')).toBeNull();
    expect(
      host.querySelector('a[aria-label="NavoX home"]')?.getAttribute("href"),
    ).toBe("https://navox.net");
    expect(
      host.querySelector(".account-entry-back")?.getAttribute("href"),
    ).toBe("https://navox.net");

    act(() => modeButton("Create account").click());
    expect(host.querySelector("h1")?.textContent).toBe("Make yourself at home");
    expect(modeButton("Create account").getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(host.querySelector('input[autocomplete="name"]')).not.toBeNull();
    expect(
      host
        .querySelector('input[autocomplete="new-password"]')
        ?.getAttribute("minlength"),
    ).toBe("12");

    act(() => modeButton("Sign in").click());
    expect(host.querySelector("h1")?.textContent).toBe("Welcome back");
    expect(host.querySelector('input[autocomplete="name"]')).toBeNull();
  });

  it("opens Today directly when the existing account is verified", async () => {
    await act(async () => sessionCheck.resolve(access(account)));

    expect(
      host.querySelector('[aria-label="Verified workspace"]')?.textContent,
    ).toBe("Synthetic student · today");
    expect(host.querySelector("form")).toBeNull();
    expect(host.querySelector(".account-entry")).toBeNull();
  });

  it("submits the direct sign-in form and enters the newly verified workspace", async () => {
    await act(async () => sessionCheck.resolve(access(null)));
    vi.mocked(authenticate).mockResolvedValueOnce(account);
    vi.mocked(restoreAccountAccess).mockResolvedValue(access(account));
    input('input[type="email"]', account.email);
    input('input[type="password"]', "synthetic-password");
    const form = host.querySelector("form");
    if (!form) throw new Error("Missing direct sign-in form");

    await act(async () => {
      form.dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );
    });

    expect(authenticate).toHaveBeenCalledWith(expect.any(String), "login", {
      email: account.email,
      password: "synthetic-password",
    });
    expect(
      host.querySelector('[aria-label="Verified workspace"]')?.textContent,
    ).toBe("Synthetic student · today");
    expect(host.querySelector("form")).toBeNull();
  });
});
