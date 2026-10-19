// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StudyBlock, StudyPlan, StudyTask } from "../lib/student-planning";
import { StudentPlanToday } from "./student-plan-today";

const taskId = "11111111-1111-4111-8111-111111111111";
const task: StudyTask = {
  id: taskId,
  title: "Finish my lab",
  course_id: null,
  due_at: null,
  estimated_minutes: 90,
  remaining_minutes: 45,
  actual_minutes: 30,
  difficulty: 3,
  priority: 3,
  course_weight: 1,
  status: "active",
  revision: 1,
  source_item_id: null,
  source_revision: null,
  source_current_revision: null,
  source_current_title: null,
  source_current_due_at: null,
  source_needs_review: false,
  source_reviewable: false,
  source_review_notice: null,
};
const block: StudyBlock = {
  task_id: taskId,
  title: task.title,
  id: "22222222-2222-4222-8222-222222222222",
  plan_id: "33333333-3333-4333-8333-333333333333",
  revision: 1,
  status: "planned",
  start_at: "2026-10-19T14:00:00Z",
  end_at: "2026-10-19T14:30:00Z",
  minutes: 30,
  actual_minutes: 0,
  outcome_at: null,
  focus_window: false,
};
const plan: StudyPlan = {
  revision: 1,
  blocks: [block],
  needs_review: false,
  review_notice: null,
  has_more_history: false,
};
const clock = {
  server_now: "2026-10-19T13:45:00Z",
  timezone: "America/New_York",
  mode: "now",
  date: "2026-10-19",
  at: "2026-10-19T13:45:00Z",
  occurrences: [],
  next_upcoming: null,
  reason: "no_classes",
  conflicts: [],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
let host: HTMLDivElement;
let root: Root;
const fetchMock = vi.fn<typeof fetch>();

function respond(
  tasks = [task],
  savedPlan: StudyPlan = plan,
  serverClock: unknown = clock,
) {
  fetchMock.mockImplementation(async (input) => {
    const path = String(input);
    if (path.endsWith("/planning/tasks")) return json({ tasks });
    if (path.endsWith("/planning/plan")) return json(savedPlan);
    if (path.endsWith("/lookup?mode=now")) return json(serverClock);
    throw new Error(`Unexpected read: ${path}`);
  });
}
async function render(accountId = "workspace:a") {
  await act(async () => {
    root.render(createElement(StudentPlanToday, { accountId }));
  });
}
function refresh() {
  const button = [...host.querySelectorAll("button")].find(
    (item) => item.textContent === "Refresh study plan",
  );
  if (!button) throw new Error("Refresh button unavailable");
  return button;
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  vi.stubGlobal("fetch", fetchMock);
  respond();
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  fetchMock.mockReset();
});

describe("Home study plan", () => {
  it("starts with an accessible loading state and makes no request during server render", () => {
    const html = renderToStaticMarkup(
      createElement(StudentPlanToday, { accountId: "workspace:a" }),
    );
    expect(html).toContain("Your study plan");
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('role="status"');
    expect(html).toContain("Opening your saved tasks");
    expect(html).not.toContain("No accepted study block");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows saved work and accepted times using the server clock, never the device clock", async () => {
    vi.spyOn(Date, "now").mockReturnValue(Date.parse("2050-01-01T00:00:00Z"));
    await render();
    expect(host.textContent).toContain("Active tasks1");
    expect(host.textContent).toContain("Work remaining45m");
    expect(host.textContent).toContain("Next accepted block");
    expect(host.textContent).toContain("Finish my lab");
    expect(host.textContent).toContain("10:00 AM–10:30 AM · 30m planned");
    expect(host.textContent).toContain("Progress recorded today0m");
    expect(host.textContent).toContain("Planned time is not recorded progress");
    expect(host.textContent).toContain("America/New York · checked at 9:45 AM");
    expect(
      host.querySelector<HTMLAnchorElement>("a[href='/planner']"),
    ).not.toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    for (const [, options] of fetchMock.mock.calls) {
      expect(options).toMatchObject({
        method: "GET",
        credentials: "include",
        cache: "no-store",
      });
    }
  });

  it("labels both local dates when an accepted block crosses midnight", async () => {
    respond([task], {
      ...plan,
      blocks: [
        {
          ...block,
          start_at: "2026-10-20T03:45:00Z",
          end_at: "2026-10-20T04:15:00Z",
        },
      ],
    });
    await render();
    const start = host.querySelector("time[datetime='2026-10-20T03:45:00Z']");
    const end = host.querySelector("time[datetime='2026-10-20T04:15:00Z']");
    expect(start?.textContent).toBe("11:45 PM");
    expect(start?.parentElement?.textContent).toContain("Monday, Oct 19");
    expect(end?.textContent).toBe("Tuesday, Oct 20 · 12:15 AM");
  });

  it("counts only saved actual outcomes recorded on the server's local day", async () => {
    respond([task], {
      ...plan,
      blocks: [
        block,
        {
          ...block,
          status: "done",
          actual_minutes: 25,
          outcome_at: "2026-10-19T13:00:00Z",
        },
        {
          ...block,
          status: "took_longer",
          actual_minutes: 40,
          outcome_at: "2026-10-19T13:10:00Z",
        },
        {
          ...block,
          status: "skipped",
          actual_minutes: 80,
          outcome_at: "2026-10-19T13:15:00Z",
        },
        {
          ...block,
          status: "done",
          actual_minutes: 60,
          outcome_at: "2026-10-19T02:00:00Z",
        },
        {
          ...block,
          status: "done",
          actual_minutes: 60,
          outcome_at: "2026-10-19T18:00:00Z",
        },
        {
          ...block,
          status: "superseded",
          actual_minutes: 60,
          outcome_at: "2026-10-19T13:00:00Z",
        },
      ],
      has_more_history: true,
    });
    await render();
    expect(host.textContent).toContain("Progress recorded today1h 5m");
    expect(host.textContent).toContain("1 block marked done · 1 skipped");
    expect(host.textContent).toContain("Older history may not be included");
    expect(host.textContent).not.toContain("You finished");
  });

  it("does not present stale study times as the next block or expired blocks as completed", async () => {
    respond([task], {
      ...plan,
      needs_review: true,
      blocks: [
        block,
        {
          ...block,
          start_at: "2026-10-18T13:00:00Z",
          end_at: "2026-10-18T13:30:00Z",
        },
      ],
    });
    await render();
    expect(host.textContent).toContain("Your saved plan needs a review");
    expect(host.textContent).not.toContain("Next accepted block");
    expect(host.textContent).not.toContain("Finish my lab");
    expect(host.textContent).toContain(
      "They have not been counted as completed",
    );
    expect(host.textContent).toContain("0 blocks marked done");
  });

  it("offers a real first-task path when no task or plan exists", async () => {
    respond([], { ...plan, revision: 0, blocks: [] });
    await render();
    const link = [...host.querySelectorAll("a")].find((item) =>
      item.textContent?.includes("Add your first task"),
    );
    expect(link?.getAttribute("href")).toBe("/planner");
    expect(host.textContent).toContain(
      "Your plan changes only when you accept it",
    );
    expect(host.textContent).not.toContain("Finish my lab");
    expect(host.textContent).not.toContain("0 blocks marked done");
  });

  it("clears private previous data on a failed refresh and recovers with checked data", async () => {
    await render();
    fetchMock.mockImplementation(async () =>
      json({ detail: "unavailable" }, 503),
    );
    await act(async () => refresh().click());
    expect(host.querySelector("[role='alert']")?.textContent).toContain(
      "could not be checked",
    );
    expect(host.textContent).not.toContain("Finish my lab");
    expect(host.textContent).not.toContain("45m");
    respond([task], { ...plan, blocks: [] });
    await act(async () => refresh().click());
    expect(host.querySelector("[role='alert']")).toBeNull();
    expect(host.textContent).toContain("Your tasks are saved");
    expect(host.textContent).toContain("Work remaining45m");
  });

  it("rejects malformed server clock data instead of making time claims", async () => {
    respond([task], plan, { ...clock, server_now: "not-a-time" });
    await render();
    expect(host.querySelector("[role='alert']")).not.toBeNull();
    expect(host.textContent).not.toContain("Next accepted block");
    expect(host.textContent).not.toContain("Finish my lab");
  });

  it("discards a late old-account response after an account change", async () => {
    const oldTasks = deferred<Response>();
    fetchMock.mockImplementation(async (input) => {
      if (String(input).endsWith("/tasks")) return oldTasks.promise;
      return String(input).includes("/lookup") ? json(clock) : json(plan);
    });
    await render();
    respond([], { ...plan, blocks: [] });
    await render("workspace:b");
    expect(host.textContent).toContain("Add your first task");
    await act(async () => oldTasks.resolve(json({ tasks: [task] })));
    expect(host.textContent).not.toContain("Finish my lab");
    expect(host.textContent).not.toContain("45m");
  });

  it("ignores an older refresh that finishes after a newer visible-page read", async () => {
    const oldTasks = deferred<Response>();
    fetchMock.mockImplementation(async (input) => {
      if (String(input).endsWith("/tasks")) return oldTasks.promise;
      return String(input).includes("/lookup") ? json(clock) : json(plan);
    });
    await render();
    respond([], { ...plan, blocks: [] });
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    await act(async () => oldTasks.resolve(json({ tasks: [task] })));
    expect(host.textContent).toContain("Add your first task");
    expect(host.textContent).not.toContain("Finish my lab");
  });
});
