// @vitest-environment jsdom
import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VoxTaskReview } from "./vox-task-review";

const id = "11111111-1111-4111-8111-111111111111";
const taskId = "22222222-2222-4222-8222-222222222222";
const fields = {
  title: "Write lab report",
  course_id: null,
  due_at: "2026-10-26T19:00:00Z",
  estimated_minutes: 60,
  remaining_minutes: 45,
  difficulty: 3,
  priority: 3,
  course_weight: 1,
  status: "active" as const,
};
const task = {
  ...fields,
  id: taskId,
  revision: 1,
  actual_minutes: 0,
  source_item_id: null,
  source_revision: null,
  source_current_revision: null,
  source_current_title: null,
  source_current_due_at: null,
  source_needs_review: false,
  source_reviewable: false,
  source_review_notice: null,
};
const proposal = {
  kind: "task" as const,
  preview_id: id,
  preview_hash: "a".repeat(64),
  timezone: "America/New_York",
  task: fields,
};
const preview = {
  preview_hash: proposal.preview_hash,
  timezone: proposal.timezone,
  before: null,
  course_label: null,
  proposed: fields,
  alarms_change: false,
  calendar_events_change: false,
  replaced_blocks: [
    {
      id,
      task_id: taskId,
      plan_id: id,
      title: "Existing calculus block",
      start_at: "2026-10-24T17:00:00Z",
      end_at: "2026-10-24T18:00:00Z",
      minutes: 60,
      focus_window: false,
      revision: 1,
      status: "planned",
      actual_minutes: 0,
      outcome_at: null,
    },
  ],
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) =>
      Response.json(String(url).endsWith("/confirm") ? task : preview),
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
async function mount(value = proposal, accountKey = "workspace:student") {
  await act(async () =>
    root.render(
      <StrictMode>
        <VoxTaskReview
          proposal={value}
          accountKey={accountKey}
          onClose={vi.fn()}
        />
      </StrictMode>,
    ),
  );
}
function approve() {
  return [...host.querySelectorAll("button")].find(
    (b) => b.textContent === "Approve task changes",
  )!;
}

it("reviews every removed block and sends the exact hash only after an explicit click", async () => {
  await mount();
  expect(host.textContent).toContain("Existing calculus block");
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(([url]) => String(url).endsWith("/review")),
  ).toBe(true);
  await act(async () => {
    approve().click();
    approve().click();
  });
  const writes = vi
    .mocked(fetch)
    .mock.calls.filter(([url]) => String(url).endsWith("/confirm"));
  expect(writes).toHaveLength(1);
  expect(writes[0][1]?.headers).toMatchObject({
    "X-NavoX-Account": "workspace:student",
  });
  expect(JSON.parse(String(writes[0][1]?.body))).toMatchObject({
    request_id: id,
    preview_hash: proposal.preview_hash,
    remaining_minutes: 45,
  });
  expect(host.textContent).toContain("Task saved.");
});

it("uses the existing task route and revision for unfinished work", async () => {
  const update = { ...proposal, task_id: taskId, revision: 1 };
  await mount(update);
  await act(async () => approve().click());
  const write = vi
    .mocked(fetch)
    .mock.calls.find(([url]) => String(url).endsWith("/confirm"))!;
  expect(String(write[0])).toContain(`/tasks/${taskId}/confirm`);
  expect(JSON.parse(String(write[1]?.body)).revision).toBe(1);
});

it("cannot approve when the task or affected blocks have changed", async () => {
  vi.mocked(fetch).mockImplementation(async () =>
    Response.json({ ...preview, preview_hash: "b".repeat(64) }),
  );
  await mount();
  expect(host.textContent).toContain("changed");
  expect(approve()).toBeUndefined();
});

it("ignores a prior account's delayed confirmation after the review owner changes", async () => {
  let finish!: (value: Response) => void;
  vi.mocked(fetch).mockImplementation(async (url) =>
    String(url).endsWith("/confirm")
      ? new Promise<Response>((resolve) => {
          finish = resolve;
        })
      : Response.json(preview),
  );
  await mount();
  act(() => approve().click());
  await mount(proposal, "workspace:another");
  await act(async () => finish(Response.json(task)));
  expect(host.textContent).not.toContain("Task saved.");
  expect(host.textContent).toContain("Review your new task");
});
