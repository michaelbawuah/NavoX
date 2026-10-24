// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  routineRequest,
  type StudyTask,
  studyRequest,
} from "../lib/student-planning";
import { studentRequest } from "../lib/student-schedule";
import { studyWindowKey } from "../lib/study-placement";
import { StudentDailyPlan } from "./student-daily-plan";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("./use-study-openings", () => ({
  useStudyOpenings: () => ({ data: null, error: false }),
}));
vi.mock("../lib/product-features", () => ({
  productFeatures: { studentGoals: false },
}));
vi.mock("../lib/student-planning", async (original) => ({
  ...(await original<typeof import("../lib/student-planning")>()),
  studyRequest: vi.fn(),
  routineRequest: vi.fn(),
}));
vi.mock("../lib/student-schedule", async (original) => ({
  ...(await original<typeof import("../lib/student-schedule")>()),
  studentRequest: vi.fn(),
}));

const task: StudyTask = {
  id: "11111111-1111-4111-8111-111111111111",
  title: "Original lab report",
  course_id: null,
  due_at: null,
  estimated_minutes: 60,
  remaining_minutes: 60,
  actual_minutes: 0,
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
  source_review_notice: null,
  source_reviewable: false,
};
const block = {
  id: "22222222-2222-4222-8222-222222222222",
  task_id: task.id,
  plan_id: "33333333-3333-4333-8333-333333333333",
  title: task.title,
  start_at: "2026-10-19T14:30:00Z",
  end_at: "2026-10-19T15:30:00Z",
  minutes: 60,
  focus_window: false,
  revision: 1,
  status: "planned",
  actual_minutes: 0,
  outcome_at: null,
};
const preferences = {
  wake_time: "07:00",
  sleep_time: "23:00",
  focus_windows: [],
  never_schedule: [],
  max_study_minutes: 90,
  break_minutes: 15,
  revision: 1,
  timezone: "America/New_York",
};
const timetable = {
  server_now: "2026-10-19T13:00:00Z",
  timezone: preferences.timezone,
  date: "2026-10-19",
  days: 14,
  courses: [],
  fixed_blocks: [],
  breaks: [],
  exceptions: [],
  occurrences: [],
  fixed_occurrences: [],
  conflicts: [],
  warnings: [],
};
const preview = {
  preview_id: "44444444-4444-4444-8444-444444444444",
  preview_hash: "a".repeat(64),
  expires_at: "2026-10-19T13:15:00Z",
  base_revision: 1,
  requires_acceptance: true,
  replaced_blocks: [block],
  result: {
    start_at: timetable.server_now,
    end_at: "2026-10-26T04:00:00Z",
    blocks: [block],
    unfitted_work: [],
    capacities: [],
    warnings: [],
    planning_only: true,
  },
};
let host: HTMLDivElement, root: Root;
let serverTitle: string;
let pendingTasks: Promise<StudyTask[]> | null;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(timetable.server_now));
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  window.history.replaceState({}, "", "/planner");
  sessionStorage.clear();
  serverTitle = task.title;
  pendingTasks = null;
  vi.mocked(studyRequest).mockImplementation(async (path) => {
    if (path === "/tasks")
      return pendingTasks ?? [{ ...task, title: serverTitle }];
    if (path === "/preferences") return preferences;
    if (path === "/plan")
      return {
        revision: 1,
        blocks: [{ ...block, title: serverTitle }],
        needs_review: false,
        review_notice: null,
        has_more_history: false,
      };
    if (path === "/preview") return preview;
    throw Error(`Unexpected planning request ${path}`);
  });
  vi.mocked(studentRequest).mockResolvedValue(timetable);
  vi.mocked(routineRequest).mockResolvedValue({
    preview_hash: "b".repeat(64),
    task,
    removed_blocks: [block],
    credited_minutes: 0,
    other_blocks_change: false,
    alarms_change: false,
    calendar_events_change: false,
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

it("keeps a Today window through reviewed new-task creation and shows remaining work after partial recovery", async () => {
  const owner = "personal:student";
  const intent = {
    accountKey: owner,
    start_at: "2026-10-19T16:00:00Z",
    end_at: "2026-10-19T18:00:00Z",
    task_id: task.id,
    timezone: preferences.timezone,
    created_at: timetable.server_now,
  };
  sessionStorage.setItem(studyWindowKey(owner), JSON.stringify(intent));
  window.history.replaceState({}, "", "/planner#study-window");
  const added = {
    ...task,
    id: "55555555-5555-4555-8555-555555555555",
    title: "New work for this window",
  };
  let rows = [task];
  let currentPlan = {
    revision: 1,
    blocks: [block],
    needs_review: false,
    review_notice: null,
    has_more_history: false,
  };
  const placed = {
    ...block,
    id: "66666666-6666-4666-8666-666666666666",
    task_id: added.id,
    title: added.title,
    start_at: intent.start_at,
    end_at: "2026-10-19T17:00:00Z",
  };
  const placementReview = {
    preview_hash: "c".repeat(64),
    after: placed,
    task: added,
    remaining_unplanned_minutes: 0,
    recovery_required: true,
    preserved_blocks: [],
    unplanned_tasks: [
      { task_id: task.id, title: task.title, minutes: 60, due_at: null },
    ],
    warnings: [],
    other_blocks_change: false,
    alarms_change: false,
    calendar_events_change: false,
  };
  vi.mocked(studyRequest).mockImplementation(async (path) => {
    if (path === "/tasks") return rows;
    if (path === "/preferences") return preferences;
    if (path === "/plan") return currentPlan;
    throw Error(`Unexpected planning request ${path}`);
  });
  vi.mocked(routineRequest).mockImplementation(async (_owner, path) => {
    if (path === "/tasks/review")
      return {
        preview_hash: "b".repeat(64),
        before: null,
        proposed: added,
        replaced_blocks: [block],
        timezone: preferences.timezone,
        course_label: null,
        alarms_change: false,
        calendar_events_change: false,
      };
    if (path === "/tasks/confirm") {
      rows = [task, added];
      currentPlan = { ...currentPlan, blocks: [], needs_review: true };
      return added;
    }
    if (path === "/placements/preview") return placementReview;
    if (path === "/placements/confirm") {
      currentPlan = { ...currentPlan, blocks: [placed], needs_review: false };
      return placementReview;
    }
    throw Error(`Unexpected reviewed request ${path}`);
  });
  await mount();
  act(() => button("Add new work for this window").click());
  type(taskInput(), added.title);
  await act(async () =>
    taskInput()
      .closest("form")
      ?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(host.textContent).toContain(
    "Saving this task retires these future blocks",
  );
  await act(async () => button("Approve task changes").click());
  expect(
    host.querySelector<HTMLSelectElement>(
      '[aria-label="Plan this free window"] select',
    )?.value,
  ).toBe(added.id);
  await act(async () => button("Review placement").click());
  const request = vi
    .mocked(routineRequest)
    .mock.calls.find(([, path]) => path === "/placements/preview");
  expect(request?.[4]).toMatchObject({
    task_id: added.id,
    start_at: "2026-10-19T16:00:00.000Z",
    window_end_at: intent.end_at,
  });
  expect(button("Start this reviewed plan").disabled).toBe(true);
  await act(async () =>
    host
      .querySelector<HTMLInputElement>(
        '[aria-label="Plan this free window"] input[type="checkbox"]',
      )
      ?.click(),
  );
  await act(async () => button("Start this reviewed plan").click());
  const summary = host.querySelector(
    '[aria-label="Saved placement and remaining work"]',
  );
  expect(summary?.textContent).toContain(task.title);
  expect(summary?.textContent).toContain(
    "Previously retired blocks have not been restored",
  );
  expect(host.querySelector('[aria-label="Plan this free window"]')).toBeNull();
  const confirmed = vi
    .mocked(routineRequest)
    .mock.calls.find(([, path]) => path === "/placements/confirm");
  expect(confirmed?.[4]).toMatchObject({
    recovery_acknowledged: true,
    task_id: added.id,
    window_end_at: intent.end_at,
  });
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});
async function mount() {
  await act(async () =>
    root.render(<StudentDailyPlan accountId="personal:student" />),
  );
}
function button(label: string) {
  const result = [...host.querySelectorAll("button")].find(
    (item) => item.textContent?.trim() === label,
  );
  if (!result) throw Error(`Missing ${label}`);
  return result;
}
function taskInput() {
  const result = host.querySelector<HTMLInputElement>('input[maxlength="200"]');
  if (!result) throw Error("Missing task input");
  return result;
}
function type(input: HTMLInputElement, text: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set?.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function reads() {
  return vi
    .mocked(studyRequest)
    .mock.calls.filter(([path]) => path === "/tasks").length;
}
async function refreshSignals() {
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("navox:study-plan-changed"));
    await vi.advanceTimersByTimeAsync(60_000);
  });
}

describe("quiet Planner refresh", () => {
  it("refreshes on focus, visibility and a minute timer without disabling the saved view", async () => {
    await mount();
    let finish!: (value: StudyTask[]) => void;
    pendingTasks = new Promise((resolve) => {
      finish = resolve;
    });
    serverTitle = "Updated in another tab";
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(reads()).toBe(2);
    expect(button("Replan week").disabled).toBe(false);
    expect(host.querySelector('[aria-busy="true"]')).toBeNull();
    expect(host.textContent).toContain(task.title);
    pendingTasks = null;
    await act(async () => finish([{ ...task, title: serverTitle }]));
    expect(host.textContent).toContain(serverTitle);
    serverTitle = "Updated when visible";
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(host.textContent).toContain(serverTitle);
    serverTitle = "Updated after a minute";
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(host.textContent).toContain(serverTitle);
  });

  it("keeps typed task edits and resumes refreshing after the editor closes", async () => {
    await mount();
    act(() => button("Add task").click());
    type(taskInput(), "Keep my unfinished essay");
    serverTitle = "Changed elsewhere";
    await refreshSignals();
    expect(reads()).toBe(1);
    expect(taskInput().value).toBe("Keep my unfinished essay");
    act(() => button("Cancel").click());
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(reads()).toBe(2);
    expect(host.textContent).toContain(serverTitle);
  });

  it("discards a background response if an editor opened while it was loading", async () => {
    await mount();
    let finish!: (value: StudyTask[]) => void;
    pendingTasks = new Promise((resolve) => {
      finish = resolve;
    });
    serverTitle = "Changed during the read";
    await act(async () => window.dispatchEvent(new Event("focus")));
    act(() => button("Add task").click());
    type(taskInput(), "My new essay");
    pendingTasks = null;
    await act(async () => finish([{ ...task, title: serverTitle }]));
    expect(taskInput().value).toBe("My new essay");
    expect(host.textContent).toContain(task.title);
    expect(host.textContent).not.toContain(serverTitle);
  });

  it.each(["plan", "move", "completion"])(
    "preserves an active %s review",
    async (kind) => {
      await mount();
      if (kind === "plan") await act(async () => button("Replan week").click());
      else if (kind === "move") act(() => button("Move").click());
      else
        await act(async () =>
          host
            .querySelector<HTMLInputElement>(
              `input[aria-label="Mark ${task.title} complete"]`,
            )
            ?.click(),
        );
      const reviewLabel =
        kind === "plan"
          ? "Use this plan"
          : kind === "move"
            ? "Review move"
            : "Approve completion";
      expect(button(reviewLabel)).toBeDefined();
      serverTitle = "Should stay outside this review";
      await refreshSignals();
      expect(reads()).toBe(1);
      expect(button(reviewLabel)).toBeDefined();
      expect(host.textContent).not.toContain(serverTitle);
    },
  );
});
