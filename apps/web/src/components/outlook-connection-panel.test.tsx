// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OutlookConnectionPanel } from "./outlook-connection-panel";

let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
it("shows setup pending without a connect or send action when Microsoft is unconfigured", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ configured: false, connections: [], notice: "" }),
    ),
  );
  await act(async () =>
    root.render(<OutlookConnectionPanel accountKey="workspace:student" />),
  );
  expect(host.textContent).toContain("setup is pending");
  expect(host.querySelector("button")).toBeNull();
  expect(
    vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === "GET"),
  ).toBe(true);
});
it("clears another account's response after its owner changes", async () => {
  let finish!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, init) => {
      const headers = init?.headers as Record<string, string>;
      if (headers["X-NavoX-Account"] === "workspace:first")
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      return Response.json({ configured: false, connections: [], notice: "" });
    }),
  );
  await act(async () =>
    root.render(<OutlookConnectionPanel accountKey="workspace:first" />),
  );
  await act(async () =>
    root.render(<OutlookConnectionPanel accountKey="workspace:second" />),
  );
  await act(async () =>
    finish(
      Response.json({
        configured: true,
        connections: [
          {
            id: "11111111-1111-4111-8111-111111111111",
            email: "private@example.com",
            status: "CONNECTED",
            read_enabled: true,
            send_enabled: true,
          },
        ],
        notice: "",
      }),
    ),
  );
  expect(host.textContent).not.toContain("private@example.com");
  expect(host.textContent).toContain("setup is pending");
});
