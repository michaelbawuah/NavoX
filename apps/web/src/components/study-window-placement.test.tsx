// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { StudyPlan, StudyTask } from "../lib/student-planning";
import { StudyWindowPlacement } from "./study-window-placement";

const task: StudyTask = {
  id: "11111111-1111-4111-8111-111111111111",
  title: "New essay",
  course_id: null,
  due_at: "2026-10-25T03:00:00Z",
  estimated_minutes: 120,
  remaining_minutes: 120,
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
  source_reviewable: false,
  source_review_notice: null,
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
const intent = {
  accountKey: "personal:student",
  start_at: "2026-10-24T14:00:00Z",
  end_at: "2026-10-24T16:00:00Z",
  task_id: task.id,
  timezone: preferences.timezone,
  created_at: "2026-10-24T13:00:00Z",
};
const empty: StudyPlan = {
  revision: 1,
  blocks: [],
  needs_review: false,
  review_notice: null,
  has_more_history: false,
};
const existing = {
  id: "22222222-2222-4222-8222-222222222222",
  task_id: task.id,
  plan_id: "33333333-3333-4333-8333-333333333333",
  title: task.title,
  start_at: "2026-10-24T18:00:00Z",
  end_at: "2026-10-24T19:00:00Z",
  minutes: 60,
  focus_window: false,
  revision: 1,
  status: "planned" as const,
  actual_minutes: 0,
  outcome_at: null,
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(intent.created_at));
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const input = JSON.parse(String(init?.body));
      const after = {
        ...existing,
        start_at: input.start_at,
        end_at: new Date(
          Date.parse(input.start_at) + (input.minutes ?? 60) * 60000,
        ).toISOString(),
        minutes: input.minutes ?? 60,
      };
      return Response.json({
        preview_hash: "a".repeat(64),
        after,
        task,
        remaining_unplanned_minutes: 60,
        recovery_required: false,
        preserved_blocks: [],
        unplanned_tasks: [
          {
            task_id: task.id,
            title: task.title,
            minutes: 60,
            due_at: task.due_at,
          },
        ],
        ...(url.includes("/move/") ? { before: existing } : {}),
        warnings: [],
        other_blocks_change: false,
        alarms_change: false,
        calendar_events_change: false,
      });
    }),
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
async function click(label: string) {
  const target = [...host.querySelectorAll("button")].find(
    (button) => button.textContent?.trim() === label,
  );
  if (!target) throw Error(`Missing ${label}`);
  await act(async () => target.click());
}
async function mount(plan = empty, onAddTask = vi.fn()) {
  const saved = vi.fn();
  await act(async () =>
    root.render(
      <StudyWindowPlacement
        intent={intent}
        tasks={[task]}
        plan={plan}
        preferences={preferences}
        creating={false}
        disabled={false}
        onAddTask={onAddTask}
        onClose={vi.fn()}
        onSaved={saved}
      />,
    ),
  );
  return saved;
}
it("places genuinely unplanned effort only after an exact owner-bound preview and approval", async () => {
  const saved = await mount();
  expect(fetch).not.toHaveBeenCalled();
  await click("Review placement");
  expect(saved).not.toHaveBeenCalled();
  expect(host.textContent).toContain(
    "60 minutes of this task will still need a plan",
  );
  await click("Approve placement");
  expect(saved).toHaveBeenCalledTimes(1);
  const [url, options] = vi.mocked(fetch).mock.calls[1];
  expect(url).toMatch(/placements\/confirm$/);
  expect(options?.headers).toMatchObject({
    "X-NavoX-Account": intent.accountKey,
  });
  expect(JSON.parse(String(options?.body))).toMatchObject({
    task_id: task.id,
    start_at: "2026-10-24T14:00:00.000Z",
    window_end_at: intent.end_at,
    minutes: 60,
    preview_hash: "a".repeat(64),
    confirmed: true,
  });
});
it("reuses Move for a real existing block and demands fresh calendar coverage", async () => {
  const saved = await mount({ ...empty, blocks: [existing] });
  await click("Review suggested move");
  const [url, options] = vi.mocked(fetch).mock.calls[0];
  expect(url).toContain(`/blocks/${existing.id}/move/preview`);
  expect(JSON.parse(String(options?.body))).toMatchObject({
    require_calendar: true,
    window_end_at: intent.end_at,
  });
  expect(host.querySelector("select")?.disabled).toBe(true);
  expect(saved).not.toHaveBeenCalled();
  await click("Approve move");
  expect(saved).toHaveBeenCalledTimes(1);
});
it("starts the normal task-creation flow without fabricating a block or saving anything", async () => {
  const add = vi.fn();
  await mount(empty, add);
  await click("Add new work for this window");
  expect(add).toHaveBeenCalledTimes(1);
  expect(fetch).not.toHaveBeenCalled();
});
it("retries an uncertain confirmation with the same exact request and preview", async () => {
  const saved = await mount();
  await click("Review placement");
  vi.mocked(fetch).mockRejectedValueOnce(new Error("network"));
  await click("Approve placement");
  expect(saved).not.toHaveBeenCalled();
  const first = JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body));
  await click("Retry this approval");
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body))).toEqual(
    first,
  );
  expect(saved).toHaveBeenCalledTimes(1);
});

it("requires a specific partial-plan acknowledgment and preserves it for an uncertain retry", async () => {
  const originalFetch = vi.mocked(fetch).getMockImplementation();
  vi.mocked(fetch).mockImplementation(async (...args) => {
    const response = await originalFetch?.(...args);
    const value = await response?.json();
    return Response.json({
      ...value,
      recovery_required: true,
      unplanned_tasks: [
        {
          task_id: "55555555-5555-4555-8555-555555555555",
          title: "Earlier essay still needing time",
          minutes: 90,
          due_at: null,
        },
      ],
    });
  });
  const saved = await mount({ ...empty, needs_review: true });
  await click("Review placement");
  const approval = [...host.querySelectorAll("button")].find(
    (item) => item.textContent === "Start this reviewed plan",
  );
  expect(approval?.disabled).toBe(true);
  expect(host.textContent).toContain("Earlier essay still needing time");
  expect(host.textContent).toContain("Retired blocks are not restored");
  await act(async () =>
    host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.click(),
  );
  vi.mocked(fetch).mockRejectedValueOnce(new Error("network"));
  await click("Start this reviewed plan");
  const first = JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body));
  expect(first.recovery_acknowledged).toBe(true);
  expect(
    host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.disabled,
  ).toBe(true);
  await click("Retry this approval");
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body))).toEqual(
    first,
  );
  expect(saved.mock.calls[0][0].unplanned_tasks[0].title).toBe(
    "Earlier essay still needing time",
  );
});

it("keeps the task and exact selected window while opening a full plan review", async () => {
  const replan = vi.fn();
  await act(async () =>
    root.render(
      <StudyWindowPlacement
        intent={intent}
        tasks={[task]}
        plan={{ ...empty, needs_review: true }}
        preferences={preferences}
        creating={false}
        disabled={false}
        onAddTask={vi.fn()}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        onReplan={replan}
      />,
    ),
  );
  await click("Review full plan and keep this window");
  expect(replan).toHaveBeenCalledTimes(1);
  expect(host.querySelector("select")?.value).toBe(task.id);
  expect(fetch).not.toHaveBeenCalled();
});
