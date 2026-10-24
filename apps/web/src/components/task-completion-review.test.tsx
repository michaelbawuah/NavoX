// @vitest-environment jsdom
import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { StudyTask } from "../lib/student-planning";
import { TaskCompletionReview } from "./task-completion-review";

const id = "11111111-1111-4111-8111-111111111111";
const task: StudyTask = {
  id,
  title: "Lab report",
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
const preview = {
  preview_hash: "a".repeat(64),
  task,
  removed_blocks: [],
  credited_minutes: 0,
  other_blocks_change: false,
  alarms_change: false,
  calendar_events_change: false,
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(preview)),
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
it("leaves the task unchanged until its exact completion review is approved", async () => {
  const saved = vi.fn();
  await act(async () =>
    root.render(
      <StrictMode>
        <TaskCompletionReview
          task={task}
          accountKey="workspace:student"
          timezone="America/New_York"
          onClose={vi.fn()}
          onSaved={saved}
        />
      </StrictMode>,
    ),
  );
  expect(saved).not.toHaveBeenCalled();
  expect(host.textContent).toContain(
    "This does not record study minutes or a streak.",
  );
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(([url]) => String(url).endsWith("/preview")),
  ).toBe(true);
  const approve = [...host.querySelectorAll("button")].find(
    (b) => b.textContent === "Approve completion",
  );
  if (!approve) throw Error("Missing approval");
  await act(async () => approve.click());
  expect(saved).toHaveBeenCalledTimes(1);
  const [, options] = vi.mocked(fetch).mock.calls.at(-1) ?? [];
  expect(options?.headers).toMatchObject({
    "X-NavoX-Account": "workspace:student",
  });
  expect(JSON.parse(String(options?.body))).toMatchObject({
    revision: 1,
    preview_hash: preview.preview_hash,
  });
});
it("cannot approve a rejected completion review", async () => {
  vi.mocked(fetch).mockImplementation(async () =>
    Response.json({ detail: "The task changed" }, { status: 409 }),
  );
  await act(async () =>
    root.render(
      <TaskCompletionReview
        task={task}
        accountKey="workspace:student"
        timezone="America/New_York"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    ),
  );
  expect(host.textContent).toContain("The task changed");
  expect(host.textContent).not.toContain("Approve completion");
});
