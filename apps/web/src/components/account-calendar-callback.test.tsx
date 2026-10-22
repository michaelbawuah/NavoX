// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountAccess } from "../lib/account-access";
import { observeCalendarAccessChanges } from "../lib/student-calendar-privacy";

vi.mock("../lib/account-session", () => ({
  observeAccountSession: (callbacks: {
    restored: (value: AccountAccess) => void;
  }) => {
    callbacks.restored({
      account: {
        id: "11111111-1111-4111-8111-111111111111",
        email: "synthetic-callback@example.test",
        display_name: "Synthetic student",
        workspace: {
          id: "22222222-2222-4222-8222-222222222222",
          name: "Synthetic workspace",
          workspace_type: "personal",
        },
      },
      connections: [],
      connectionsError: "",
    });
    return { dispose: vi.fn(), invalidate: vi.fn() };
  },
  notifyAccountSessionChanged: vi.fn(),
  notifyAccountSessionChanging: vi.fn(),
}));
vi.mock("./today-workspace", () => ({
  TodayWorkspace: ({ message }: { message: string }) =>
    createElement("p", { role: "status" }, message),
}));

import { AccountWorkspace } from "./account-workspace";

let host: HTMLDivElement;
let root: Root;
const posted = vi.fn();

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "BroadcastChannel",
    class extends EventTarget {
      postMessage = posted;
      close() {}
    },
  );
  vi.stubGlobal("fetch", vi.fn());
  posted.mockClear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});

describe("calendar authorization return", () => {
  it.each([
    [
      "calendar_scheduling_enabled",
      "Google calendar scheduling enabled. Every event still needs your review and approval.",
    ],
    [
      "calendar_availability_enabled",
      "Google calendar busy-time access enabled. Open Planner to check for openings.",
    ],
    [
      "calendar_scheduling_failed",
      "Google calendar scheduling did not complete. Review Calendar scheduling in Connections.",
    ],
  ])(
    "handles %s and retires earlier calendar displays",
    async (outcome, message) => {
      const clear = vi.fn();
      const stop = observeCalendarAccessChanges(clear);
      window.history.replaceState({}, "", `/?google_connection=${outcome}`);
      await act(async () => root.render(createElement(AccountWorkspace)));
      expect(host.querySelector('[role="status"]')?.textContent).toBe(message);
      expect(clear).toHaveBeenCalledTimes(1);
      expect(posted).toHaveBeenCalledExactlyOnceWith({
        schema: "navox-calendar-access.v1",
        type: "changed",
      });
      expect(window.location.search).toBe("");
      expect(fetch).not.toHaveBeenCalled();
      stop();
    },
  );

  it("does not turn an unknown outcome into approved calendar access", async () => {
    const clear = vi.fn();
    const stop = observeCalendarAccessChanges(clear);
    window.history.replaceState({}, "", "/?google_connection=unknown_success");
    await act(async () => root.render(createElement(AccountWorkspace)));
    expect(host.textContent).toContain("Google connection did not complete.");
    expect(clear).not.toHaveBeenCalled();
    expect(posted).not.toHaveBeenCalled();
    expect(window.location.search).toBe("");
    expect(fetch).not.toHaveBeenCalled();
    stop();
  });
});
