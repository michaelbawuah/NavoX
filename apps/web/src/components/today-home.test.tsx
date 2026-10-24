// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { productFeatures } from "../lib/product-features";
import { studyRequest } from "../lib/student-planning";
import { studentRequest } from "../lib/student-schedule";
import { TodayHome } from "./today-home";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));
vi.mock("./vox-call-provider", () => ({ useVoxCall: () => null }));
vi.mock("../lib/student-schedule", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/student-schedule")>()),
  studentRequest: vi.fn(),
}));
vi.mock("../lib/student-planning", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/student-planning")>()),
  studyRequest: vi.fn(),
}));
const account = {
  id: "qa",
  display_name: "Jordan",
  email: "qa@example.test",
  timezone: "America/New_York",
  workspace: { id: "personal", name: "Personal", workspace_type: "personal" },
};
const empty = {
  server_now: "2026-10-23T18:00:00Z",
  timezone: "America/New_York",
  date: "2026-10-23",
  days: 1,
  courses: [],
  fixed_blocks: [],
  breaks: [],
  exceptions: [],
  occurrences: [],
  fixed_occurrences: [],
  conflicts: [],
  warnings: [],
};
const props = {
  account,
  connections: [],
  message: "",
  onConnectionsChanged: vi.fn(async () => {}),
  onSignOut: vi.fn(async () => {}),
};
let host: HTMLDivElement, root: Root;
const flags = { ...productFeatures };
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  localStorage.clear();
  productFeatures.studentPlanning = true;
  productFeatures.studentGoals = false;
  productFeatures.studentWhiteboard = false;
  vi.mocked(studentRequest).mockResolvedValue(empty);
  vi.mocked(studyRequest).mockImplementation(async (path) =>
    path === "/tasks" ? [] : { revision: 0, blocks: [], needs_review: false },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        completed_recently: [],
        needs_attention: [],
        coming_up: [],
        waiting_on: [],
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
  Object.assign(productFeatures, flags);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
async function render(next = props) {
  await act(async () => root.render(<TodayHome {...next} />));
}
describe("Today home", () => {
  it("shows one five-step setup checklist and no empty dashboard cards", async () => {
    await render();
    expect(host.textContent).toContain("0 of 5 done");
    expect(host.querySelectorAll('[aria-label="Set up NavoX"]')).toHaveLength(
      1,
    );
    for (const removed of [
      "Upcoming renewals",
      "Your daily focus",
      "Refresh",
      "Due soon",
      "Your day",
      "Needs a reply",
    ])
      expect(host.textContent).not.toContain(removed);
    expect(
      vi
        .mocked(fetch)
        .mock.calls.every(([, init]) => !init?.method || init.method === "GET"),
    ).toBe(true);
  });
  it("does not tick a step simply because its link was opened", async () => {
    await render();
    const link = host.querySelector('a[href="/classes"]');
    if (!link) throw Error("Missing Classes link");
    link.addEventListener("click", (e) => e.preventDefault());
    act(() =>
      link.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true }),
      ),
    );
    expect(host.textContent).toContain("0 of 5 done");
  });
  it("never declares an empty account done after hiding setup", async () => {
    await render();
    const hide = host.querySelector<HTMLButtonElement>(
      '[aria-label="Close setup checklist"]',
    );
    if (!hide) throw Error("Missing hide action");
    act(() => hide.click());
    expect(host.textContent).not.toContain("Done for today");
    expect(host.textContent).toContain("Show setup checklist");
  });
  it("does not count calendar read permission as email access", async () => {
    await act(async () =>
      root.render(
        <TodayHome
          {...props}
          connections={[
            {
              id: "google",
              provider: "google",
              external_email: null,
              status: "active",
              granted_scopes: [
                "https://www.googleapis.com/auth/calendar.readonly",
              ],
              last_checked_at: null,
              last_error: null,
            },
          ]}
        />,
      ),
    );
    const rows = [...host.querySelectorAll('[aria-label="Set up NavoX"] li')];
    expect(rows).toHaveLength(5);
    expect(rows[1].getAttribute("data-done")).toBe("true");
    expect(rows[2].getAttribute("data-done")).toBe("false");
    expect(host.textContent).toContain("1 of 5 done");
  });
  it("does not present a failed schedule lookup as an empty completed day", async () => {
    vi.mocked(studentRequest).mockRejectedValue(Error("internal details"));
    await render();
    expect(host.textContent).toContain("Some of your day couldn’t load.");
    expect(host.textContent).not.toContain("Done for today");
    expect(host.textContent).not.toContain("Let’s set up NavoX");
    expect(host.textContent).not.toContain("internal details");
    expect(host.textContent).not.toContain(
      "No more classes on your timetable today",
    );
  });
  it("shows a real saved shift in the correct day and hides empty side sections", async () => {
    vi.mocked(studentRequest).mockResolvedValue({
      ...empty,
      fixed_occurrences: [
        {
          id: "shift",
          fixed_block_id: "fixed",
          title: "Library shift",
          start_at: "2026-10-23T20:00:00Z",
          end_at: "2026-10-23T22:00:00Z",
        },
      ],
    });
    await render();
    expect(host.textContent).toContain("Library shift");
    expect(host.querySelector('[aria-label="Next up"]')).not.toBeNull();
    expect(host.textContent).not.toContain("Let’s set up NavoX");
    expect(host.textContent).toContain("Your day");
    expect(host.textContent).not.toContain("Due soon");
  });
  it("does not call a day complete when connection status is unavailable", async () => {
    localStorage.setItem("navox:setup:personal:qa:dismissed", "yes");
    await act(async () =>
      root.render(<TodayHome {...props} connectionsUnavailable />),
    );
    expect(host.textContent).toContain("Some of your day couldn’t load.");
    expect(host.textContent).not.toContain("Done for today");
  });
  it("ignores a late response after changing accounts", async () => {
    let resolve!: (value: unknown) => void;
    vi.mocked(studentRequest).mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    await render();
    await render({
      ...props,
      account: { ...account, id: "other", display_name: "Taylor" },
    });
    await act(async () =>
      resolve({
        ...empty,
        fixed_occurrences: [
          {
            id: "private",
            title: "Old account shift",
            start_at: "2026-10-23T20:00:00Z",
            end_at: "2026-10-23T22:00:00Z",
          },
        ],
      }),
    );
    expect(host.textContent).toContain("Taylor");
    expect(host.textContent).not.toContain("Old account shift");
  });
});
