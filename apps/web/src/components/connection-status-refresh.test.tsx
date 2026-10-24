// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { lmsFeedRequest } from "../lib/student-lms-feeds";
import { studentRequest } from "../lib/student-schedule";
import { IntelligenceControls } from "./intelligence-controls";
import { StudentLmsFeeds } from "./student-lms-feeds";
import { ThreadWatchPanel } from "./thread-watch-panel";

vi.mock("../lib/student-lms-feeds", async (original) => ({
  ...(await original<typeof import("../lib/student-lms-feeds")>()),
  lmsFeedRequest: vi.fn(),
}));
vi.mock("../lib/student-schedule", async (original) => ({
  ...(await original<typeof import("../lib/student-schedule")>()),
  studentRequest: vi.fn(),
}));

const watch = {
  id: "watch-owned",
  action_id: "action-owned",
  connection_id: "mailbox-owned",
  commitment_id: null,
  compose_commitment_id: null,
  recipient_email: "recipient@example.test",
  sender_email: "qa@example.test",
  subject: "Original reply watch",
  remind_at: "2026-10-26T15:00:00Z",
  status: "watching",
  reason: null,
  last_checked_at: null,
  replied_at: null,
  next_check_at: null,
  provider_url: null,
  created_at: "2026-10-24T15:00:00Z",
  updated_at: "2026-10-24T15:00:00Z",
};
const feed = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "New calendar status",
  domain: "school.example.test",
  platform: "canvas",
  timezone: "America/New_York",
  all_day_due_time: "23:59",
  state: "active",
  revision: 1,
  last_synced_at: null,
  next_sync_at: null,
  last_error: null,
  course_matches: {},
};
let host: HTMLDivElement, root: Root;
const request = vi.fn();
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  vi.stubGlobal("fetch", request);
  request.mockResolvedValue(Response.json({ items: [watch] }));
  vi.mocked(lmsFeedRequest).mockImplementation(async (path) =>
    path === "/items" ? { items: [], next_cursor: null } : [],
  );
  vi.mocked(studentRequest).mockImplementation(async (path) =>
    path.startsWith("/courses") ? [] : { timezone: "America/New_York" },
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});
async function focus() {
  await act(async () => window.dispatchEvent(new Event("focus")));
}
function addCalendar() {
  const button = [...host.querySelectorAll("button")].find((node) =>
    node.textContent?.includes("Add a calendar"),
  );
  if (!button) throw Error("Add calendar action missing");
  button.click();
}

describe("quiet saved-status updates", () => {
  it("refreshes Gmail watch status without starting a provider check or showing Refresh", async () => {
    await act(async () =>
      root.render(<ThreadWatchPanel timezone="America/New_York" />),
    );
    expect(host.textContent).toContain("Original reply watch");
    expect(host.textContent).not.toContain("Refresh status");
    request.mockResolvedValue(
      Response.json({ items: [{ ...watch, subject: "Updated reply watch" }] }),
    );
    await focus();
    expect(host.textContent).toContain("Updated reply watch");
    expect(
      request.mock.calls.every(([, options]) => options.method === "GET"),
    ).toBe(true);
    expect(host.textContent).toContain("11:00 AM");
  });

  it("preserves an open reminder editor and ignores a read that finishes after it opens", async () => {
    await act(async () =>
      root.render(<ThreadWatchPanel timezone="America/New_York" />),
    );
    let finish: (value: Response) => void = () => {};
    request.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    await focus();
    const editor = host.querySelector("details");
    if (!editor) throw Error("Reminder editor missing");
    editor.open = true;
    await act(async () =>
      finish(
        Response.json({
          items: [{ ...watch, subject: "Stale incoming update" }],
        }),
      ),
    );
    expect(host.textContent).toContain("Original reply watch");
    expect(host.textContent).not.toContain("Stale incoming update");
    const reads = request.mock.calls.length;
    await focus();
    expect(request).toHaveBeenCalledTimes(reads);
  });

  it("stops saved-status polling in a hidden tab", async () => {
    await act(async () => root.render(<ThreadWatchPanel />));
    const reads = request.mock.calls.length;
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(request).toHaveBeenCalledTimes(reads);
  });

  it("updates idle school-calendar lists without polling an active add flow", async () => {
    await act(async () =>
      root.render(
        <StudentLmsFeeds accountId="qa:owner" planningEnabled={false} />,
      ),
    );
    expect(host.textContent).not.toContain("Refresh saved status");
    const reads = vi.mocked(lmsFeedRequest).mock.calls.length;
    await focus();
    expect(vi.mocked(lmsFeedRequest).mock.calls.length).toBe(reads + 2);
    await act(async () => addCalendar());
    const editingReads = vi.mocked(lmsFeedRequest).mock.calls.length;
    await focus();
    expect(vi.mocked(lmsFeedRequest)).toHaveBeenCalledTimes(editingReads);
  });

  it("does not apply an old calendar-list read after the student begins connecting", async () => {
    await act(async () =>
      root.render(
        <StudentLmsFeeds accountId="qa:owner" planningEnabled={false} />,
      ),
    );
    let finish: (value: unknown) => void = () => {};
    vi.mocked(lmsFeedRequest).mockImplementation(async (path) =>
      path === "/items"
        ? { items: [], next_cursor: null }
        : new Promise((resolve) => {
            finish = resolve;
          }),
    );
    await focus();
    await act(async () => addCalendar());
    await act(async () => finish([feed]));
    expect(host.textContent).not.toContain("New calendar status");
    expect(host.querySelector('input[type="password"]')).not.toBeNull();
  });

  it("updates source status through reads without sync, source consent, or a manual Refresh control", async () => {
    request.mockImplementation(async () =>
      Response.json({
        active_commitments: 0,
        evidence_count: 0,
        observation_count: 0,
      }),
    );
    const onRefresh = vi.fn(async () => {});
    await act(async () =>
      root.render(
        <IntelligenceControls
          connections={[]}
          paused={false}
          onRefresh={onRefresh}
        />,
      ),
    );
    expect(host.textContent).not.toContain("Refresh Today");
    expect(host.textContent).not.toContain("0 active commitments");
    const reads = request.mock.calls.length;
    await focus();
    expect(request).toHaveBeenCalledTimes(reads + 1);
    expect(onRefresh).not.toHaveBeenCalled();
    expect(
      request.mock.calls.every(
        ([url, options]) =>
          String(url).endsWith("/intelligence/status") && !options.method,
      ),
    ).toBe(true);
  });
});
