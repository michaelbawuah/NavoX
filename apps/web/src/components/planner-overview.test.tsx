// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { goalsRequest, type StudentGoalsBoard } from "../lib/student-goals";
import type { StudyBlock, StudyPlan, StudyTask } from "../lib/student-planning";
import type { StudentTimetable } from "../lib/student-schedule";
import { PlannerOverview } from "./planner-overview";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../lib/product-features", () => ({
  productFeatures: { studentGoals: true },
}));
vi.mock("../lib/student-goals", async (original) => ({
  ...(await original<typeof import("../lib/student-goals")>()),
  goalsRequest: vi.fn(),
}));
vi.mock("./use-study-openings", () => ({
  useStudyOpenings: () => ({ data: null }),
}));
vi.mock("./today-study-actions", () => ({
  TodayStudyActions: () => <p>Study outcome controls</p>,
}));

const timetable: StudentTimetable = {
  server_now: "2026-10-26T17:00:00Z",
  timezone: "UTC",
  date: "2026-10-26",
  days: 7,
  courses: [],
  fixed_blocks: [],
  breaks: [],
  exceptions: [],
  occurrences: [],
  fixed_occurrences: [],
  conflicts: [],
  warnings: [],
};
const task: StudyTask = {
  id: "task",
  title: "Algebra",
  course_id: null,
  due_at: null,
  estimated_minutes: 120,
  remaining_minutes: 90,
  difficulty: 1,
  priority: 1,
  course_weight: 1,
  status: "active",
  actual_minutes: 0,
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
function block(
  id: string,
  hour: number,
  status: StudyBlock["status"] = "planned",
  actual = 0,
): StudyBlock {
  return {
    id,
    plan_id: "plan",
    task_id: task.id,
    title: id,
    revision: 1,
    status,
    start_at: `2026-10-26T${hour}:00:00Z`,
    end_at: `2026-10-26T${hour + 1}:00:00Z`,
    minutes: 60,
    focus_window: false,
    actual_minutes: actual,
    outcome_at: status === "planned" ? null : "2026-10-26T16:00:00Z",
  };
}
const emptyBoard = { goals: [] } as unknown as StudentGoalsBoard;
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.mocked(goalsRequest).mockResolvedValue(emptyBoard);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});
async function render(blocks: StudyBlock[], accountKey = "workspace:student") {
  const plan: StudyPlan = {
    revision: 1,
    blocks,
    has_more_history: false,
    needs_review: false,
    review_notice: null,
  };
  await act(async () =>
    root.render(
      <PlannerOverview
        plan={plan}
        tasks={[task]}
        timetable={timetable}
        accountKey={accountKey}
        disabled={false}
        onAdd={vi.fn()}
        onEdit={vi.fn()}
        onReplan={vi.fn()}
        onRoutine={vi.fn()}
        onReload={vi.fn()}
      />,
    ),
  );
}
it("does not advertise an ended block as next, while permitting explicit past-outcome review", async () => {
  await render([block("Morning study", 10)]);
  expect(host.textContent).toContain("No upcoming study block");
  expect(host.textContent).not.toContain("Next study block");
  expect(host.textContent).not.toContain("Study outcome controls");
  const past = [...host.querySelectorAll("button")].find(
    (b) => b.textContent === "Morning study",
  );
  if (!past) throw Error("Missing past block");
  act(() => past.click());
  expect(host.textContent).toContain("Selected study block");
  expect(host.textContent).toContain("Study outcome controls");
});
it("separates remaining planned minutes from actual recorded work in the selected range", async () => {
  await render([
    block("Done", 10, "done", 45),
    block("Longer", 12, "took_longer", 75),
    block("Evening", 18),
    block("Skipped", 14, "skipped"),
  ]);
  expect(host.textContent).toContain("1h of study planned.");
  expect(host.textContent).toContain("2h of study recorded.");
  const tomorrow = [...host.querySelectorAll("button")].find(
    (b) => b.textContent === "Tomorrow",
  );
  if (!tomorrow) throw Error("Missing Tomorrow");
  act(() => tomorrow.click());
  expect(host.textContent).toContain("No study blocks planned in this view.");
  expect(host.textContent).not.toContain("of study recorded.");
});
it("returns to an honest next label when a selected block is removed by a fresh plan", async () => {
  const past = block("Past", 10),
    future = block("Future", 18);
  await render([past, future]);
  const selected = [...host.querySelectorAll("button")].find(
    (b) => b.textContent === "Past",
  );
  if (!selected) throw Error("Missing past block");
  act(() => selected.click());
  await render([future]);
  expect(host.textContent).toContain("Next study block");
  expect(host.textContent).not.toContain("Selected study block");
});
it("binds goals to the captured account and discards an old account's delayed result", async () => {
  let finish: (value: StudentGoalsBoard) => void = () => {};
  vi.mocked(goalsRequest).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await render([], "workspace:first");
  const firstSignal = vi.mocked(goalsRequest).mock.calls[0][4];
  expect(vi.mocked(goalsRequest).mock.calls[0][5]).toBe("workspace:first");
  await render([], "workspace:second");
  expect(firstSignal?.aborted).toBe(true);
  expect(vi.mocked(goalsRequest).mock.calls[1][5]).toBe("workspace:second");
  await act(async () =>
    finish({
      goals: [
        {
          id: "private",
          title: "Old account private goal",
          status: "active",
          tracking: "qualitative",
          progress: { reported_count: null, qualitative_latest: "unknown" },
        },
      ],
    } as unknown as StudentGoalsBoard),
  );
  expect(host.textContent).not.toContain("Old account private goal");
});
