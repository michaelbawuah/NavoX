// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  type ProgressReceipt,
  TodayProgressReceipt,
} from "./today-progress-receipt";

vi.mock("../lib/student-planning", () => ({
  readStudyPlan: (value: unknown) => value,
}));
let host: HTMLDivElement, root: Root;
const receipt = {
  savedAt: Date.now(),
  task: { title: "Algebra", actual_minutes: 0, remaining_minutes: 90 },
  block: {
    id: "original",
    title: "Original block",
    start_at: "2026-10-24T15:00:00Z",
    end_at: "2026-10-24T16:00:00Z",
  },
  result: {
    undo_request_id: "saved-outcome",
    plan: { revision: 4 },
    replaced_blocks: [],
    result: {
      blocks: [
        {
          task_id: "task",
          title: "Replacement block",
          start_at: "2026-10-24T17:00:00Z",
          end_at: "2026-10-24T18:00:00Z",
        },
      ],
    },
  },
} as unknown as ProgressReceipt;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({})),
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
it("shows the inverse changes before Undo writes, then sends the exact saved revision", async () => {
  const close = vi.fn();
  await act(async () =>
    root.render(
      <TodayProgressReceipt
        receipt={receipt}
        accountKey="workspace:student"
        timezone="UTC"
        onClose={close}
      />,
    ),
  );
  await click("Undo");
  expect(fetch).not.toHaveBeenCalled();
  expect(host.textContent).toContain("Original block");
  expect(host.textContent).toContain("Replacement block");
  expect(host.textContent).toContain("90 minutes remaining");
  await click("Approve Undo");
  expect(
    JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body)),
  ).toMatchObject({ outcome_request_id: "saved-outcome", revision: 4 });
  expect(close).toHaveBeenCalledTimes(1);
});
it("retries an uncertain Undo with the same request ID", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(Error("network"));
  await act(async () =>
    root.render(
      <TodayProgressReceipt
        receipt={receipt}
        accountKey="workspace:student"
        timezone="UTC"
        onClose={vi.fn()}
      />,
    ),
  );
  await click("Undo");
  await click("Approve Undo");
  await click("Approve Undo");
  expect(vi.mocked(fetch).mock.calls[0][1]?.body).toEqual(
    vi.mocked(fetch).mock.calls[1][1]?.body,
  );
});
