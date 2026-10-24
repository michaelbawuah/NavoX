// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { StudyBlock, StudyTask } from "../lib/student-planning";
import { TodayStudyActions } from "./today-study-actions";

vi.mock("../lib/student-planning", () => ({
  readStudyOutcomePreview: (value: unknown) => value,
}));
let host: HTMLDivElement, root: Root;
const block = {
  id: "block",
  task_id: "task",
  revision: 1,
  title: "Practice algebra",
  minutes: 30,
  start_at: "2026-10-24T12:00:00Z",
} as StudyBlock;
const task = { id: "task", revision: 2, remaining_minutes: 90 } as StudyTask;
const result = {
  preview_hash: "a".repeat(64),
  block: { actual_minutes: 30 },
  task: { title: "Practice algebra", remaining_minutes: 60 },
  replaced_blocks: [
    {
      id: "old",
      title: "Old block",
      start_at: "2026-10-24T15:00:00Z",
      end_at: "2026-10-24T16:00:00Z",
    },
  ],
  result: {
    blocks: [
      {
        task_id: "task",
        title: "New block",
        start_at: "2026-10-24T17:00:00Z",
        end_at: "2026-10-24T18:00:00Z",
      },
    ],
    warnings: [],
    unfitted_work: [],
  },
};
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(result)),
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
async function click(text: string) {
  const button = [...host.querySelectorAll("button")].find(
    (b) => b.textContent === text,
  );
  if (!button) throw Error("Missing button");
  await act(async () => button.click());
}
it("shows every replacement before approval and confirms the reviewed hash", async () => {
  const saved = vi.fn();
  await act(async () =>
    root.render(
      <TodayStudyActions
        block={block}
        task={task}
        accountKey="workspace:student"
        timezone="UTC"
        now={Date.parse("2026-10-24T13:00:00Z")}
        onSaved={saved}
      />,
    ),
  );
  await click("Done");
  expect(fetch).not.toHaveBeenCalled();
  await click("Preview changes");
  expect(vi.mocked(fetch).mock.calls[0][0]).toMatch(/outcome\/preview$/);
  expect(saved).not.toHaveBeenCalled();
  expect(host.textContent).toContain("Old block");
  expect(host.textContent).toContain("New block");
  expect(host.textContent).toContain("alarms will stay");
  await click("Approve changes");
  const [url, options] = vi.mocked(fetch).mock.calls[1];
  expect(url).toMatch(/outcome\/confirm$/);
  expect(JSON.parse(String(options?.body)).preview_hash).toBe(
    result.preview_hash,
  );
  expect(options?.headers).toMatchObject({
    "X-NavoX-Account": "workspace:student",
  });
  expect(saved).toHaveBeenCalledTimes(1);
});
it("cancel never commits a preview and future blocks cannot report completed time", async () => {
  await act(async () =>
    root.render(
      <TodayStudyActions
        block={block}
        task={task}
        accountKey="workspace:student"
        timezone="UTC"
        now={Date.parse("2026-10-24T10:00:00Z")}
        onSaved={vi.fn()}
      />,
    ),
  );
  expect(
    host
      .querySelector('button[aria-label="Done: Practice algebra"]')
      ?.hasAttribute("disabled"),
  ).toBe(true);
  await click("Skip");
  await click("Preview changes");
  await click("Cancel");
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(host.textContent).not.toContain("Approve changes");
});
