// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountAccess } from "../lib/account-access";
import {
  VOX_END_CALL_TIMEOUT_MS,
  type VoxConnection,
  type VoxTransportRequest,
} from "../lib/vox-call";
import { VoxCallControls, VoxHomeLauncher } from "./vox-call-controls";
import {
  useVoxCall,
  type VoxCallContextValue,
  VoxCallProvider,
} from "./vox-call-provider";

const account = {
  id: "10000000-0000-4000-8000-000000000001",
  email: "synthetic-vox-ui@example.test",
  display_name: "Synthetic student",
  workspace: {
    id: "20000000-0000-4000-8000-000000000001",
    name: "Synthetic workspace",
    workspace_type: "personal",
  },
};
const proof: AccountAccess = { account, connections: [], connectionsError: "" };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

let latest: VoxCallContextValue | null;
let host: HTMLDivElement;
let root: Root;
let visibility: DocumentVisibilityState;

function Route({ page, home = false }: { page: string; home?: boolean }) {
  latest = useVoxCall();
  return (
    <>
      {home && <VoxHomeLauncher />}
      <p>{page}</p>
    </>
  );
}

function rig(available = true) {
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
    available,
    localWakeAvailable: false,
    connect: vi.fn(async (request: VoxTransportRequest) => {
      requests.push(request);
      const connection = join(request);
      connections.push(connection);
      return connection;
    }),
    endCall: vi.fn(async () => {}),
  };
  const restoreAccess = vi.fn(async () => proof);
  const configuration = {
    transport,
    restoreAccess,
    createCallId: () =>
      `30000000-0000-4000-8000-${String(++serial).padStart(12, "0")}`,
    now: () => now,
  };
  async function mount(
    page = "Today",
    variant: "home" | "floating" = "floating",
    home = false,
  ) {
    await act(async () =>
      root.render(
        <VoxCallProvider {...configuration}>
          <VoxCallControls variant={variant} />
          <Route key={page} page={page} home={home && page === "Today"} />
        </VoxCallProvider>,
      ),
    );
  }
  return {
    transport,
    requests,
    connections,
    restoreAccess,
    configuration,
    mount,
    join,
    setNow: (value: number) => {
      now = value;
    },
  };
}

function button(name: string) {
  const match = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
    (element) =>
      element.getAttribute("aria-label") === name ||
      element.textContent?.trim() === name,
  );
  if (!match) throw new Error(`Missing call button ${name}`);
  return match;
}

async function click(name: string) {
  await act(async () => button(name).click());
}

function dialog() {
  const element = host.querySelector<HTMLDialogElement>("dialog");
  if (!element) throw new Error("No call dialog");
  return element;
}

beforeEach(() => {
  latest = null;
  visibility = "visible";
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Real network is forbidden");
    }),
  );
  vi.spyOn(document, "visibilityState", "get").mockImplementation(
    () => visibility,
  );
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

function homeButton() {
  const element = host.querySelector<HTMLButtonElement>(
    '[data-vox-launcher="home"]',
  );
  if (!element) throw new Error("Missing Home Vox entry");
  return element;
}

async function clickHome() {
  await act(async () => {
    homeButton().focus();
    homeButton().click();
  });
}

describe("Home entry shares the app-scoped Vox call", () => {
  it("never creates a call or another dialog on mount and waits for verified sign-in", async () => {
    const test = rig();
    const check = deferred<AccountAccess>();
    test.restoreAccess.mockImplementationOnce(() => check.promise);
    await test.mount("Today", "floating", true);
    expect(homeButton().disabled).toBe(true);
    expect(homeButton().textContent).toContain("Checking your sign-in");
    expect(test.transport.connect).not.toHaveBeenCalled();
    expect(host.querySelectorAll("dialog")).toHaveLength(0);
    await act(async () => check.resolve(proof));
    expect(homeButton().disabled).toBe(false);
    expect(homeButton().getAttribute("aria-haspopup")).toBe("dialog");
    expect(homeButton().getAttribute("title")).toBe("Talk with NavoX");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("starts once on repeated Home taps and opens only the root dialog", async () => {
    const test = rig();
    const pending = deferred<ReturnType<typeof test.join>>();
    test.transport.connect.mockImplementationOnce(async (request) => {
      test.requests.push(request);
      return pending.promise;
    });
    await test.mount("Today", "floating", true);
    await act(async () => {
      homeButton().focus();
      homeButton().click();
      homeButton().click();
    });
    expect(test.transport.connect).toHaveBeenCalledOnce();
    expect(homeButton().textContent).toContain("Connecting");
    expect(host.querySelectorAll("dialog")).toHaveLength(1);
    expect(document.activeElement).toBe(button("Minimize call"));
    await act(async () => pending.resolve(test.join(test.requests[0])));
    expect(homeButton().textContent).toContain("Ready when you are");
    expect(host.querySelectorAll("dialog")).toHaveLength(1);
  });

  it("returns keyboard focus to Home and reopens the same muted call", async () => {
    const test = rig();
    await test.mount("Today", "floating", true);
    await clickHome();
    const controller = latest?.controller;
    const callId = latest?.state.callId;
    await click("Mute microphone");
    await click("Turn speaker off");
    await act(async () =>
      dialog().dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Escape",
          bubbles: true,
          cancelable: true,
        }),
      ),
    );
    expect(host.querySelectorAll("dialog")).toHaveLength(0);
    expect(document.activeElement).toBe(homeButton());
    expect(homeButton().textContent).toContain("Microphone muted");
    expect(homeButton().textContent).toContain("Speaker off");
    await clickHome();
    expect(latest?.controller).toBe(controller);
    expect(latest?.state.callId).toBe(callId);
    expect(latest?.state.microphoneMuted).toBe(true);
    expect(latest?.state.speakerEnabled).toBe(false);
    expect(test.transport.connect).toHaveBeenCalledOnce();
    expect(host.querySelectorAll("dialog")).toHaveLength(1);
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.requests[0].mayPlayAudio()).toBe(false);
  });

  it("keeps the same call when the Home route leaves and returns", async () => {
    const test = rig();
    await test.mount("Today", "floating", true);
    await clickHome();
    const callId = latest?.state.callId;
    const controller = latest?.controller;
    await click("Minimize call");
    await test.mount("Classes", "floating", true);
    expect(host.querySelector('[data-vox-launcher="home"]')).toBeNull();
    expect(latest?.controller).toBe(controller);
    expect(latest?.state.callId).toBe(callId);
    expect(test.connections[0].close).not.toHaveBeenCalled();
    await click("Return to Vox call");
    expect(host.querySelectorAll("dialog")).toHaveLength(1);
    await click("Minimize call");
    await test.mount("Today", "floating", true);
    await clickHome();
    expect(latest?.controller).toBe(controller);
    expect(latest?.state.callId).toBe(callId);
    expect(test.transport.connect).toHaveBeenCalledOnce();
    expect(host.querySelectorAll("dialog")).toHaveLength(1);
  });

  it("returns Done to the Home initiator even after starting another call in the same dialog", async () => {
    const test = rig();
    await test.mount("Today", "floating", true);
    await clickHome();
    await click("Hang up");
    await click("Start another call");
    expect(test.transport.connect).toHaveBeenCalledTimes(2);
    expect(host.querySelectorAll("dialog")).toHaveLength(1);
    await click("Hang up");
    await click("Done");
    expect(host.querySelectorAll("dialog")).toHaveLength(0);
    expect(document.activeElement).toBe(homeButton());
  });

  it("returns Done to the floating launcher if the Home initiator left while the dialog stayed open", async () => {
    const test = rig();
    await test.mount("Today", "floating", true);
    await clickHome();
    const controller = latest?.controller;
    await test.mount("Classes", "floating", true);
    expect(host.querySelector('[data-vox-launcher="home"]')).toBeNull();
    expect(host.querySelectorAll("dialog")).toHaveLength(1);
    expect(latest?.controller).toBe(controller);
    expect(test.connections[0].close).not.toHaveBeenCalled();
    await click("Hang up");
    await click("Done");
    expect(host.querySelectorAll("dialog")).toHaveLength(0);
    expect(document.activeElement).toBe(button("Talk with NavoX"));
    expect(test.transport.connect).toHaveBeenCalledOnce();
  });

  it("disables Home when calls are unavailable and retains a typed alternative", async () => {
    const test = rig(false);
    await test.mount("Today", "floating", true);
    expect(homeButton().disabled).toBe(true);
    expect(homeButton().textContent).toContain(
      "Vox calls are not available yet",
    );
    const section = homeButton().closest("section");
    expect(section?.querySelector("a")?.getAttribute("href")).toBe("/navox");
    await clickHome();
    expect(test.transport.connect).not.toHaveBeenCalled();
    expect(host.querySelectorAll("dialog")).toHaveLength(0);
  });

  it("masks an active call while sign-in is being checked without granting new audio authority", async () => {
    const test = rig();
    await test.mount("Today", "floating", true);
    await clickHome();
    const controller = latest?.controller;
    if (!controller) throw new Error("Missing shared call controller");
    const callId = latest?.state.callId;
    await act(async () => controller.suspendForSessionCheck());
    expect(homeButton().disabled).toBe(true);
    expect(homeButton().textContent).toContain("Checking your sign-in");
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.requests[0].mayPlayAudio()).toBe(false);
    await act(async () =>
      controller.bindOwner({
        userId: account.id,
        workspaceId: account.workspace.id,
      }),
    );
    expect(homeButton().disabled).toBe(false);
    expect(latest?.state.callId).toBe(callId);
    expect(test.transport.connect).toHaveBeenCalledOnce();
  });

  it("reports failed connection without leaking transport details or claiming audio works", async () => {
    const test = rig();
    test.transport.connect.mockRejectedValueOnce(
      new Error("Private transport details"),
    );
    await test.mount("Today", "floating", true);
    await clickHome();
    expect(homeButton().textContent).toContain("The call could not connect");
    expect(host.textContent).not.toContain("Private transport details");
    expect(host.textContent).not.toContain("Listening");
    expect(
      homeButton().closest("section")?.querySelector("a")?.getAttribute("href"),
    ).toBe("/navox");
    expect(test.transport.connect).toHaveBeenCalledOnce();
  });

  it("requires reload after unconfirmed cleanup instead of offering another Home call", async () => {
    const test = rig();
    test.transport.endCall.mockRejectedValueOnce(
      new Error("Private cleanup failure"),
    );
    await test.mount("Today", "floating", true);
    await clickHome();
    await click("Hang up");
    await click("Done");
    expect(homeButton().getAttribute("aria-label")).toBe("Reload NavoX");
    expect(homeButton().textContent).toContain("Reload needed");
    expect(homeButton().closest("section")?.textContent).toContain(
      "Reload before starting another call",
    );
    expect(homeButton().disabled).toBe(false);
    expect(test.transport.connect).toHaveBeenCalledOnce();
    expect(host.textContent).not.toContain("Private cleanup failure");
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Vox call interface with real provider and controller", () => {
  it("has an accessible first-use launcher and never opens audio on mount", async () => {
    const test = rig();
    await test.mount("Today", "home");
    expect(button("Talk with NavoX").disabled).toBe(false);
    expect(button("Talk with NavoX").getAttribute("aria-haspopup")).toBe(
      "dialog",
    );
    expect(test.transport.connect).not.toHaveBeenCalled();
    expect(host.querySelector("dialog")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("keeps an unavailable transport disabled and offers a real typed-chat destination", async () => {
    const test = rig(false);
    await test.mount();
    expect(button("Talk with NavoX").disabled).toBe(true);
    expect(host.textContent).toContain("Vox calls are not available yet");
    const link = host.querySelector<HTMLAnchorElement>("a");
    expect(link?.textContent).toBe("Use typed chat");
    expect(link?.getAttribute("href")).toBe("/navox");
    await click("Talk with NavoX");
    expect(test.transport.connect).not.toHaveBeenCalled();
    expect(host.querySelector("dialog")).toBeNull();
  });

  it("opens a named modal only after an explicit start and gives keyboard focus to its control", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    expect(test.transport.connect).toHaveBeenCalledOnce();
    const modal = dialog();
    expect(modal.open).toBe(true);
    expect(modal.getAttribute("aria-modal")).toBe("true");
    expect(
      document.getElementById(modal.getAttribute("aria-labelledby") ?? "")
        ?.textContent,
    ).toBe("NavoX Vox");
    expect(document.activeElement).toBe(button("Minimize call"));
    expect(modal.textContent).toContain("Ready when you are");
    expect(test.requests[0].mayTransmitAudio()).toBe(true);
  });

  it("shows connecting honestly and can hang up before a pending join completes", async () => {
    const test = rig();
    const pending = deferred<ReturnType<typeof test.join>>();
    test.transport.connect.mockImplementationOnce(async (request) => {
      test.requests.push(request);
      return pending.promise;
    });
    await test.mount();
    await click("Talk with NavoX");
    expect(dialog().textContent).toContain("Connecting");
    expect(dialog().querySelector('[role="timer"]')).toBeNull();
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    await click("Hang up");
    expect(dialog().textContent).toContain("Call ended");
    const connection = test.join(test.requests[0]);
    await act(async () => pending.resolve(connection));
    expect(connection.close).toHaveBeenCalledOnce();
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
  });

  it("microphone and speaker switches independently change the real transport gates", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    await click("Mute microphone");
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.requests[0].mayPlayAudio()).toBe(true);
    expect(button("Unmute microphone").getAttribute("aria-pressed")).toBe(
      "true",
    );
    await click("Turn speaker off");
    expect(test.requests[0].mayPlayAudio()).toBe(false);
    await click("Unmute microphone");
    expect(test.requests[0].mayTransmitAudio()).toBe(true);
    expect(test.requests[0].mayPlayAudio()).toBe(false);
    expect(test.connections[0].stopPlayback).toHaveBeenCalled();
    await click("Turn speaker on");
    expect(test.requests[0].mayPlayAudio()).toBe(true);
  });

  it("captions stay unavailable until a real stream exists and never fabricate transcript text", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    expect(dialog().textContent).toContain(
      "Live captions are not available yet",
    );
    expect(button("Captions unavailable").disabled).toBe(true);
    expect(button("Captions unavailable").textContent).toContain("Unavailable");
    await click("Captions unavailable");
    expect(latest?.state.captionsEnabled).toBe(true);
    expect(test.requests[0].mayTransmitAudio()).toBe(true);
    expect(test.requests[0].mayPlayAudio()).toBe(true);
    expect(dialog().textContent).not.toMatch(
      /You said|You asked|Transcript|Meeting added/u,
    );
  });

  it("retries browser-blocked sound only on a tap without unmuting or restarting the call", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    await click("Mute microphone");
    const callId = latest?.state.callId;
    await act(async () => {
      test.requests[0].onEvent({ type: "activity", activity: "speaking" });
      test.requests[0].onEvent({ type: "playback-blocked" });
    });
    expect(dialog().textContent).toContain("Your browser paused sound.");
    expect(dialog().querySelector('[role="status"]')?.textContent).toBe(
      "Enable sound to hear NavoX",
    );
    const controller = latest?.controller;
    if (!controller) throw new Error("Missing actual call controller");
    const microphoneChange = vi.spyOn(controller, "setMicrophoneMuted");
    await click("Enable sound");
    expect(latest?.state.callId).toBe(callId);
    expect(latest?.state.microphoneMuted).toBe(true);
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(microphoneChange).not.toHaveBeenCalled();
    expect(test.connections[0].setMicrophoneEnabled).toHaveBeenLastCalledWith(
      false,
    );
    expect(test.connections[0].setSpeakerEnabled).toHaveBeenLastCalledWith(
      true,
    );
    expect(test.transport.connect).toHaveBeenCalledOnce();
    expect(dialog().textContent).toContain("Your browser paused sound.");
    await act(async () => test.requests[0].onEvent({ type: "playback-ready" }));
    expect(dialog().textContent).not.toContain("Your browser paused sound");
    expect(
      [...dialog().querySelectorAll("button")].some(
        (item) => item.textContent === "Enable sound",
      ),
    ).toBe(false);
    expect(latest?.state.microphoneMuted).toBe(true);
  });

  it("does not advertise browser-blocked playback while the speaker is deliberately off", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    await click("Turn speaker off");
    await act(async () =>
      test.requests[0].onEvent({ type: "playback-blocked" }),
    );
    expect(dialog().textContent).not.toContain("Your browser paused sound");
    expect(dialog().querySelector('[role="status"]')?.textContent).not.toBe(
      "Enable sound to hear NavoX",
    );
    expect(test.requests[0].mayPlayAudio()).toBe(false);
  });

  it("minimizes, restores, and navigates while preserving the same call and independent preferences", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    await click("Mute microphone");
    const controller = latest?.controller;
    const callId = latest?.state.callId;
    await click("Minimize call");
    expect(host.querySelector("dialog")).toBeNull();
    expect(document.activeElement).toBe(button("Return to Vox call"));
    await test.mount("Classes");
    expect(latest?.controller).toBe(controller);
    expect(latest?.state.callId).toBe(callId);
    await click("Return to Vox call");
    expect(dialog()).toBeTruthy();
    expect(latest?.state.microphoneMuted).toBe(true);
    expect(test.transport.connect).toHaveBeenCalledOnce();
    expect(test.connections[0].close).not.toHaveBeenCalled();
  });

  it("Escape minimizes rather than hanging up, and Tab remains within the modal", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    const first = button("Minimize call");
    const last = dialog().querySelector<HTMLAnchorElement>("a");
    if (!last) throw new Error("Missing modal typed-chat alternative");
    first.focus();
    act(() =>
      first.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Tab",
          shiftKey: true,
          bubbles: true,
          cancelable: true,
        }),
      ),
    );
    expect(document.activeElement).toBe(last);
    act(() =>
      last.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Tab",
          bubbles: true,
          cancelable: true,
        }),
      ),
    );
    expect(document.activeElement).toBe(first);
    act(() =>
      dialog().dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Escape",
          bubbles: true,
          cancelable: true,
        }),
      ),
    );
    await act(async () => Promise.resolve());
    expect(host.querySelector("dialog")).toBeNull();
    expect(test.connections[0].close).not.toHaveBeenCalled();
    expect(test.requests[0].mayTransmitAudio()).toBe(true);
    expect(document.activeElement).toBe(button("Return to Vox call"));
  });

  it("typed-chat navigation minimizes while leaving call ownership above the route", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    const link = dialog().querySelector<HTMLAnchorElement>("a");
    if (!link) throw new Error("Missing typed chat link");
    const event = new MouseEvent("click", { bubbles: true, cancelable: true });
    event.preventDefault(); // Keep jsdom on this synthetic document.
    await act(async () => link.dispatchEvent(event));
    expect(latest?.state.minimized).toBe(true);
    expect(test.connections[0].close).not.toHaveBeenCalled();
    expect(link.getAttribute("href")).toBe("/navox");
  });

  it("shows only reported activity and updates a non-live call timer", async () => {
    vi.useFakeTimers();
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    for (const [activity, label] of [
      ["listening", "Listening"],
      ["thinking", "Thinking"],
      ["speaking", "Speaking"],
    ] as const) {
      await act(async () =>
        test.requests[0].onEvent({ type: "activity", activity }),
      );
      expect(dialog().querySelector('[role="status"]')?.textContent).toBe(
        label,
      );
    }
    test.setNow(65_000);
    await act(async () => vi.advanceTimersByTime(1_000));
    expect(dialog().querySelector('[role="timer"]')?.textContent).toBe("1:05");
    expect(
      dialog().querySelector('[role="timer"]')?.getAttribute("aria-live"),
    ).toBe("off");
  });

  it("standing by pauses transmission and explicit resume reuses the existing connection", async () => {
    vi.useFakeTimers();
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    test.setNow(120_000);
    await act(async () => vi.advanceTimersByTime(1_000));
    expect(dialog().textContent).toContain("Standing by");
    expect(dialog().textContent).toContain("Audio transmission is paused");
    expect(dialog().textContent).not.toContain("Hey NavoX");
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    await click("Resume conversation");
    expect(test.requests[0].mayTransmitAudio()).toBe(true);
    expect(test.transport.connect).toHaveBeenCalledOnce();
  });

  it("offers Hey NavoX only after local listening is confirmed and describes wake input as queued", async () => {
    vi.useFakeTimers();
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    test.setNow(120_000);
    await act(async () => vi.advanceTimersByTime(1_000));
    await act(async () =>
      test.requests[0].onEvent({ type: "wake-status", status: "starting" }),
    );
    expect(dialog().textContent).toContain("Preparing local wake listening");
    expect(dialog().textContent).not.toContain("Hey NavoX");
    await act(async () =>
      test.requests[0].onEvent({ type: "wake-status", status: "listening" }),
    );
    expect(dialog().textContent).toContain("Say “Hey NavoX”");
    expect(dialog().textContent).toContain("Keep NavoX open");
    await act(async () =>
      test.requests[0].onEvent({ type: "wake-status", status: "unavailable" }),
    );
    expect(dialog().textContent).not.toContain("Hey NavoX");
    expect(dialog().textContent).toContain("Local wake is unavailable");
    await click("Resume conversation");
    await act(async () =>
      test.requests[0].onEvent({ type: "wake-input", status: "queued" }),
    );
    expect(dialog().textContent).toContain(
      "Your question is queued in this conversation",
    );
    await act(async () =>
      test.requests[0].onEvent({ type: "wake-input", status: "failed" }),
    );
    expect(dialog().textContent).toContain("please say it again");
  });

  it("shows one quiet server allowance notice in the call and while minimized", async () => {
    vi.useFakeTimers();
    const notice = "You have used at least 80% of your daily voice allowance.";
    const fetcher = vi.fn<typeof fetch>(async () =>
      Response.json({ notices: [notice], blocked: false, policy_revision: 3 }),
    );
    vi.stubGlobal("fetch", fetcher);
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    expect(dialog().querySelector("[data-vox-usage]")?.textContent).toBe(
      notice,
    );
    expect(host.querySelectorAll("[data-vox-usage]")).toHaveLength(1);
    const originalNotice = dialog().querySelector("[data-vox-usage]");
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(dialog().querySelector("[data-vox-usage]")).toBe(originalNotice);
    await click("Minimize call");
    expect(host.querySelectorAll("[data-vox-usage]")).toHaveLength(1);
    expect(host.querySelector("[data-vox-usage]")?.textContent).toBe(notice);
    fetcher.mockResolvedValue(
      Response.json({ notices: [notice], blocked: true, policy_revision: 3 }),
    );
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(host.querySelector("[data-vox-usage]")?.textContent).toContain(
      "allowance has been reached",
    );
    expect(host.querySelector("[data-vox-usage]")?.textContent).toContain(
      "after your allowance resets",
    );
    expect(
      fetcher.mock.calls.every(
        ([, init]) => init?.method === "GET" && init?.body === undefined,
      ),
    ).toBe(true);
  });

  it("reconnecting is visible while the old lease cannot send or play audio", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    const pending = deferred<ReturnType<typeof test.join>>();
    test.transport.connect.mockImplementationOnce(async (request) => {
      test.requests.push(request);
      return pending.promise;
    });
    await act(async () => test.requests[0].onEvent({ type: "disconnected" }));
    expect(dialog().textContent).toContain("Reconnecting");
    expect(dialog().textContent).toContain("Audio is paused");
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.requests[0].mayPlayAudio()).toBe(false);
    await act(async () => pending.resolve(test.join(test.requests[1])));
    expect(dialog().textContent).toContain("Ready when you are");
  });

  it("hangs up with a real terminal state, closes details, and starts again only on another tap", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    const oldId = latest?.state.callId;
    await click("Hang up");
    expect(dialog().textContent).toContain("Call ended");
    expect(test.connections[0].close).toHaveBeenCalledOnce();
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(dialog().textContent).not.toMatch(
      /Recap|Summary|Actions completed/u,
    );
    await click("Done");
    expect(host.querySelector("dialog")).toBeNull();
    expect(document.activeElement).toBe(button("Talk with NavoX"));
    expect(test.transport.connect).toHaveBeenCalledOnce();
    await click("Talk with NavoX");
    expect(test.transport.connect).toHaveBeenCalledTimes(2);
    expect(latest?.state.callId).not.toBe(oldId);
  });

  it("requires a reload after failed terminal cleanup, including after details are dismissed", async () => {
    const test = rig();
    test.transport.endCall.mockRejectedValueOnce(
      new Error("Private terminal cleanup failure"),
    );
    await test.mount();
    await click("Talk with NavoX");
    await click("Hang up");
    expect(latest?.state.endReason).toBe("cleanup-incomplete");
    expect(dialog().textContent).toContain("cleanup could not be confirmed");
    expect(dialog().textContent).toContain("Reload NavoX");
    expect(dialog().textContent).not.toMatch(
      /Start another call|try again|Private terminal/u,
    );
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.requests[0].mayPlayAudio()).toBe(false);
    await click("Done");
    expect(host.querySelector("dialog")).toBeNull();
    expect(button("Reload NavoX").disabled).toBe(false);
    expect(document.activeElement).toBe(button("Reload NavoX"));
    expect(host.textContent).toContain("Call cleanup could not be confirmed");
    expect(host.querySelector('a[href="/navox"]')?.textContent).toBe(
      "Use typed chat",
    );
    expect(host.textContent).not.toMatch(/Talk with NavoX|try again/u);
    expect(test.transport.connect).toHaveBeenCalledOnce();
  });

  it("surfaces timed-out cleanup even after ended details were already closed and ignores late success", async () => {
    vi.useFakeTimers();
    const test = rig();
    const pending = deferred<void>();
    test.transport.endCall.mockReturnValueOnce(pending.promise);
    await test.mount();
    await click("Talk with NavoX");
    await click("Hang up");
    expect(latest?.state.endReason).toBe("hang-up");
    await click("Done");
    expect(host.querySelector("dialog")).toBeNull();
    await act(async () => vi.advanceTimersByTime(VOX_END_CALL_TIMEOUT_MS - 1));
    expect(latest?.state.endReason).toBe("hang-up");
    await act(async () => vi.advanceTimersByTime(1));
    expect(latest?.state.endReason).toBe("cleanup-incomplete");
    expect(dialog().textContent).toContain("Reload NavoX");
    expect(host.textContent).not.toMatch(/Start another call|try again/u);
    await act(async () => pending.resolve());
    expect(latest?.state.endReason).toBe("cleanup-incomplete");
    expect(dialog().textContent).toContain("Reload NavoX");
    expect(test.transport.connect).toHaveBeenCalledOnce();
  });

  it("requires reload when physical connection cleanup times out and keeps its audio gates closed", async () => {
    vi.useFakeTimers();
    const test = rig();
    const pending = deferred<void>();
    await test.mount();
    await click("Talk with NavoX");
    test.connections[0].close.mockReturnValueOnce(pending.promise);
    await click("Hang up");
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.requests[0].mayPlayAudio()).toBe(false);
    await act(async () => vi.advanceTimersByTime(VOX_END_CALL_TIMEOUT_MS));
    expect(latest?.state.endReason).toBe("cleanup-incomplete");
    expect(dialog().textContent).toContain("Reload NavoX");
    expect(host.textContent).not.toMatch(/Start another call|try again/u);
    await act(async () => pending.resolve());
    expect(latest?.state.endReason).toBe("cleanup-incomplete");
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.requests[0].mayPlayAudio()).toBe(false);
    expect(test.transport.connect).toHaveBeenCalledOnce();
  });

  it("still exposes cleanup recovery when an owner change cleared the previous call identifier", async () => {
    const test = rig();
    test.transport.endCall.mockRejectedValueOnce(new Error("Cleanup failed"));
    await test.mount();
    await click("Talk with NavoX");
    test.restoreAccess.mockResolvedValueOnce({
      ...proof,
      account: {
        ...account,
        id: "10000000-0000-4000-8000-000000000002",
        workspace: {
          ...account.workspace,
          id: "20000000-0000-4000-8000-000000000002",
        },
      },
    });
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(latest?.state.ownerVerified).toBe(true);
    expect(latest?.state.callId).toBeNull();
    expect(latest?.state.endReason).toBe("cleanup-incomplete");
    expect(dialog().textContent).toContain("Reload NavoX");
    expect(dialog().querySelector('[role="timer"]')).toBeNull();
    expect(host.textContent).not.toMatch(/Start another call|try again/u);
    await click("Done");
    expect(host.querySelector("dialog")).toBeNull();
    expect(button("Reload NavoX").disabled).toBe(false);
    expect(test.transport.connect).toHaveBeenCalledOnce();
  });

  it("masks the entire old call while owner proof is pending, then restores only a fresh matching owner", async () => {
    vi.useFakeTimers();
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    test.setNow(65_000);
    await act(async () => vi.advanceTimersByTime(1_000));
    const id = latest?.state.callId;
    const pending = deferred<AccountAccess>();
    test.restoreAccess.mockReturnValueOnce(pending.promise);
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(host.textContent).toContain("Checking your sign-in");
    expect(host.querySelector("dialog")).toBeNull();
    expect(host.textContent).not.toMatch(
      /1:05|Microphone|Speaker|Ready when you are/u,
    );
    expect(host.querySelector("button")?.disabled).toBe(true);
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    await act(async () => pending.resolve(proof));
    expect(latest?.state.callId).toBe(id);
    expect(dialog()).toBeTruthy();
    expect(test.transport.connect).toHaveBeenCalledOnce();
  });

  it("hides a playback-recovery prompt while fresh owner proof is pending", async () => {
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    await act(async () =>
      test.requests[0].onEvent({ type: "playback-blocked" }),
    );
    expect(dialog().textContent).toContain("Your browser paused sound");
    const pending = deferred<AccountAccess>();
    test.restoreAccess.mockReturnValueOnce(pending.promise);
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(host.textContent).toContain("Checking your sign-in");
    expect(host.textContent).not.toMatch(
      /Your browser paused sound|Enable sound/u,
    );
    expect(host.querySelector("dialog")).toBeNull();
    expect(test.requests[0].mayPlayAudio()).toBe(false);
    await act(async () => pending.resolve(proof));
  });

  it("initial unverified identity never exposes a start control or opens audio", async () => {
    const test = rig();
    const pending = deferred<AccountAccess>();
    test.restoreAccess.mockReturnValueOnce(pending.promise);
    await test.mount();
    expect(host.querySelector('[aria-label="Vox"]')).toBeNull();
    expect(host.querySelector("button")).toBeNull();
    expect(host.querySelector("dialog")).toBeNull();
    expect(test.transport.connect).not.toHaveBeenCalled();
    await act(async () => pending.resolve(proof));
    expect(button("Talk with NavoX").disabled).toBe(false);
    expect(test.transport.connect).not.toHaveBeenCalled();
  });

  it("does not render an entry or perpetual checking state for a confirmed signed-out account", async () => {
    const test = rig();
    test.restoreAccess.mockResolvedValueOnce({ ...proof, account: null });
    await test.mount();
    expect(host.querySelector('[aria-label="Vox"]')).toBeNull();
    expect(host.querySelector("button,dialog,time")).toBeNull();
    expect(host.textContent).not.toContain("Checking your sign-in");
    expect(test.transport.connect).not.toHaveBeenCalled();
  });

  it("removes the entire old call and its timer after confirmed owner loss", async () => {
    vi.useFakeTimers();
    const test = rig();
    await test.mount();
    await click("Talk with NavoX");
    test.setNow(65_000);
    await act(async () => vi.advanceTimersByTime(1_000));
    expect(dialog().querySelector('[role="timer"]')?.textContent).toBe("1:05");
    test.restoreAccess.mockResolvedValueOnce({ ...proof, account: null });
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(host.querySelector('[aria-label="Vox"]')).toBeNull();
    expect(host.querySelector("button,dialog,time")).toBeNull();
    expect(host.textContent).not.toMatch(
      /1:05|Checking your sign-in|Microphone|Speaker/u,
    );
    expect(test.requests[0].mayTransmitAudio()).toBe(false);
    expect(test.connections[0].close).toHaveBeenCalledOnce();
  });

  it("renders nothing outside the app-scoped provider", async () => {
    await act(async () => root.render(<VoxCallControls />));
    expect(host.innerHTML).toBe("");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("a failed connection is an ended state with an honest explicit retry", async () => {
    const test = rig();
    test.transport.connect.mockRejectedValueOnce(
      new Error("Private adapter error"),
    );
    await test.mount();
    await click("Talk with NavoX");
    expect(dialog().textContent).toContain(
      "The connection could not be completed",
    );
    expect(dialog().textContent).not.toContain("Private adapter error");
    expect(test.transport.connect).toHaveBeenCalledOnce();
    await click("Start another call");
    expect(test.transport.connect).toHaveBeenCalledTimes(2);
  });
});
