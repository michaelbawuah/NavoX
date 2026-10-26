// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountSessionCallbacks } from "../lib/account-session";
import type {
  AppleCalendarApi,
  AppleCalendarView,
} from "../lib/apple-calendar-api";
import { notifyCalendarAccessChanged } from "../lib/student-calendar-privacy";
import { AppleCalendarConnection } from "./apple-calendar-connection";

const account = {
  id: "11111111-1111-4111-8111-111111111111",
  workspace: {
    id: "22222222-2222-4222-8222-222222222222",
    name: "Personal",
    workspace_type: "personal",
  },
  email: "student@example.test",
  display_name: "Student",
};
const accountId = `${account.workspace.id}:${account.id}`;
const observers: AccountSessionCallbacks[] = [];
vi.mock("../lib/account-session", () => ({
  observeAccountSession(callbacks: AccountSessionCallbacks) {
    observers.push(callbacks);
    callbacks.checking();
    callbacks.restored({ account, connections: [], connectionsError: "" });
    return { dispose: vi.fn(), refresh: vi.fn() };
  },
}));
const view: AppleCalendarView = {
  generation: 2,
  revision: 3,
  consent_version: "apple-calendar-availability.v1",
  configured: true,
  grants: [],
  changes_applied: false,
};
const connected: AppleCalendarView = {
  ...view,
  revision: 4,
  grants: [
    {
      id: "33333333-3333-4333-8333-333333333333",
      connection_id: "44444444-4444-4444-8444-444444444444",
      provider: "icloud",
      status: "connected",
      account_label: "student@example.test",
      revision: 1,
    },
  ],
};
const secret = "aaaa-bbbb-cccc-dddd";
function api(): AppleCalendarApi {
  return {
    account: vi.fn(async () => accountId),
    view: vi.fn(async () => structuredClone(view)),
    connect: vi.fn(async () => structuredClone(connected)),
    disconnect: vi.fn(async () => ({ ...structuredClone(view), revision: 5 })),
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
let host: HTMLDivElement;
let root: Root;
async function settle() {
  for (let index = 0; index < 16; index += 1) await Promise.resolve();
}
function input(type: string) {
  const found = host.querySelector<HTMLInputElement>(`input[type="${type}"]`);
  if (!found) throw new Error(`Missing ${type} input`);
  return found;
}
function button(text: string) {
  const found = [...host.querySelectorAll("button")].find(
    (element) => element.textContent?.trim() === text,
  );
  if (!found) throw new Error(`Missing ${text} button`);
  return found;
}
async function enter(type: string, value: string) {
  await act(async () => {
    const element = input(type);
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set?.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    await settle();
  });
}
async function click(element: HTMLElement) {
  await act(async () => {
    element.click();
    await settle();
  });
}
async function mount(client: AppleCalendarApi) {
  await act(async () => {
    root.render(<AppleCalendarConnection accountId={accountId} api={client} />);
    await settle();
  });
}
async function fill() {
  await enter("email", "student@example.test");
  await enter("password", secret);
  await click(input("checkbox"));
}
beforeEach(() => {
  observers.length = 0;
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Unexpected network");
    }),
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Apple calendar connection screen", () => {
  it("loads saved metadata and requires explicit busy consent before connecting", async () => {
    const client = api();
    await mount(client);
    expect(client.view).toHaveBeenCalledTimes(1);
    expect(client.connect).not.toHaveBeenCalled();
    expect(input("checkbox").checked).toBe(false);
    expect(button("Connect Apple calendar").disabled).toBe(true);
    expect(input("password").autocomplete).toBe("off");
    expect(host.textContent).toContain(
      "Never enter your main Apple Account password",
    );
    expect(
      host.querySelector('a[href="https://support.apple.com/en-us/102654"]'),
    ).not.toBeNull();
  });

  it("clears the secret and consent immediately while a reviewed connect is pending", async () => {
    const client = api();
    const pending = deferred<AppleCalendarView>();
    client.connect = vi.fn(() => pending.promise);
    await mount(client);
    await fill();
    await click(button("Connect Apple calendar"));
    expect(input("password").value).toBe("");
    expect(input("checkbox").checked).toBe(false);
    expect(client.connect).toHaveBeenCalledWith(
      expect.objectContaining({
        apple_id: "student@example.test",
        app_password: secret,
        confirmed: true,
        generation: 2,
        revision: 3,
      }),
      expect.any(AbortSignal),
    );
    await act(async () => {
      pending.resolve(connected);
      await settle();
    });
    expect(host.textContent).toContain("Apple calendar access is connected");
    expect(host.textContent).not.toContain(secret);
    expect(input("email").value).toBe("");
  });

  it("clears drafts on privacy notices and ignores a late connect after account revocation", async () => {
    const client = api();
    const pending = deferred<AppleCalendarView>();
    client.connect = vi.fn(() => pending.promise);
    await mount(client);
    await fill();
    await act(async () => {
      notifyCalendarAccessChanged();
      await settle();
    });
    expect(input("email").value).toBe("");
    expect(input("password").value).toBe("");
    await fill();
    await click(button("Connect Apple calendar"));
    await act(async () => {
      observers[0].revoking?.();
      await settle();
    });
    await act(async () => {
      pending.resolve(connected);
      await settle();
    });
    expect(host.textContent).not.toContain(
      "Apple calendar access is connected",
    );
    expect(host.textContent).not.toContain("Busy-time access is on");
    expect(input("email").value).toBe("");
    expect(input("password").value).toBe("");
  });

  it("fences an old response across a new sign-in for the same owner", async () => {
    const client = api();
    const pending = deferred<AppleCalendarView>();
    client.connect = vi.fn(() => pending.promise);
    await mount(client);
    await fill();
    await click(button("Connect Apple calendar"));
    await act(async () => {
      observers[0].checking();
      observers[0].restored({ account, connections: [], connectionsError: "" });
      await settle();
      pending.resolve(connected);
      await settle();
    });
    expect(host.textContent).not.toContain("Busy-time access is on");
    expect(host.textContent).not.toContain(
      "Apple calendar access is connected",
    );
  });

  it("sanitizes failed connect errors and keeps the password cleared", async () => {
    const client = api();
    client.connect = vi.fn(async () => {
      throw new Error(`private ${secret}`);
    });
    await mount(client);
    await fill();
    await click(button("Connect Apple calendar"));
    expect(host.querySelector('[role="alert"]')?.textContent).toContain(
      "Calendar access is unavailable",
    );
    expect(host.textContent).not.toContain(secret);
    expect(input("password").value).toBe("");
  });

  it("discards credential drafts when the selected owner changes", async () => {
    const client = api();
    await mount(client);
    await fill();
    await act(async () => {
      root.render(
        <AppleCalendarConnection
          accountId="66666666-6666-4666-8666-666666666666:77777777-7777-4777-8777-777777777777"
          api={client}
        />,
      );
      await settle();
    });
    expect(input("email").value).toBe("");
    expect(input("password").value).toBe("");
    expect(input("checkbox").checked).toBe(false);
    expect(client.connect).not.toHaveBeenCalled();
    expect(host.textContent).toContain("The account changed");
  });

  it("disconnects only the selected Apple grant and does not send credentials", async () => {
    const client = api();
    client.view = vi.fn(async () => structuredClone(connected));
    await mount(client);
    await click(button("Disconnect Apple calendar"));
    expect(client.disconnect).toHaveBeenCalledWith(
      connected.grants[0].id,
      expect.objectContaining({ generation: 2, revision: 4 }),
      expect.any(AbortSignal),
    );
    expect(host.textContent).toContain(
      "saved app-specific credential was removed",
    );
    expect(host.textContent).not.toContain("Busy-time access is on");
  });
});
