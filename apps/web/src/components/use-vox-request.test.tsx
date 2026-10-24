// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useVoxRequest } from "./use-vox-request";

const mock = vi.hoisted(() => ({ call: null as unknown }));
vi.mock("./vox-call-provider", () => ({ useVoxCall: () => mock.call }));
let root: Root,
  host: HTMLDivElement,
  state: Record<string, unknown>,
  listener: ((value: unknown) => void) | undefined;
let start: ReturnType<typeof vi.fn>, resume: ReturnType<typeof vi.fn>;
function Harness() {
  const request = useVoxRequest();
  return (
    <>
      <button
        type="button"
        disabled={request.busy}
        onClick={() => void request.ask("Plan my afternoon")}
      >
        Ask
      </button>
      <p>{request.notice}</p>
    </>
  );
}
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  state = {
    phase: "active",
    callId: "call-1",
    owner: { workspaceId: "workspace", userId: "student" },
    ownerVerified: true,
  };
  start = vi.fn(async () => {
    state.phase = "active";
  });
  resume = vi.fn(() => {
    state.phase = "active";
  });
  mock.call = {
    available: true,
    state,
    controller: {
      current: () => state,
      start,
      resume,
      setMinimized: vi.fn(),
      subscribe: (fn: (s: unknown) => void) => {
        listener = fn;
        return () => {
          listener = undefined;
        };
      },
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, options: RequestInit) =>
      Response.json({
        request_id: JSON.parse(String(options.body)).request_id,
        status: "accepted",
        delivery: "queued",
      }),
    ),
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
async function mount() {
  await act(async () => root.render(<Harness />));
}
async function ask() {
  await act(async () => host.querySelector("button")?.click());
}
it("starts a call and sends the explicit request with owner binding", async () => {
  state.phase = "idle";
  await mount();
  await ask();
  expect(start).toHaveBeenCalledOnce();
  const [url, options] = vi.mocked(fetch).mock.calls[0];
  expect(url).toMatch(/vox\/calls\/call-1\/messages$/);
  expect(options?.headers).toMatchObject({
    "X-NavoX-Account": "workspace:student",
  });
  expect(JSON.parse(String(options?.body)).text).toBe("Plan my afternoon");
  expect(host.textContent).toContain("Your request is with Vox.");
});
it("reuses the request ID after an uncertain delivery instead of duplicating it", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(Error("Network lost"));
  await mount();
  await ask();
  await ask();
  const bodies = vi
    .mocked(fetch)
    .mock.calls.map(([, options]) => JSON.parse(String(options?.body)));
  expect(bodies[0].request_id).toBe(bodies[1].request_id);
});
it("waits during reconnect and resumes standby before submitting", async () => {
  state.phase = "reconnecting";
  await mount();
  await ask();
  expect(fetch).not.toHaveBeenCalled();
  state.phase = "standby";
  await ask();
  expect(resume).toHaveBeenCalledOnce();
  expect(fetch).toHaveBeenCalledOnce();
});
it("aborts a queued request if the call owner changes", async () => {
  let signal: AbortSignal | null | undefined;
  vi.mocked(fetch).mockImplementationOnce(async (_url, options) => {
    signal = options?.signal;
    return new Promise((_resolve, reject) =>
      signal?.addEventListener("abort", () => reject(Error("aborted"))),
    );
  });
  await mount();
  await ask();
  expect(signal?.aborted).toBe(false);
  await act(async () =>
    listener?.({ ...state, owner: { workspaceId: "other", userId: "other" } }),
  );
  expect(signal?.aborted).toBe(true);
  expect(host.textContent).not.toContain("Your request is with Vox.");
});
