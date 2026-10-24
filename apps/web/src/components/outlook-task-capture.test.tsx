// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { outlookTaskApi, prepareOutlookTask } from "../lib/outlook-tasks";
import {
  type StudentEmailApi,
  StudentEmailRequestError,
  type StudentEmailReview,
} from "../lib/student-email";
import { OutlookTaskCapture } from "./outlook-task-capture";

vi.mock("../lib/product-features", () => ({
  productFeatures: { studentEmail: true, studentPlanning: true },
}));
vi.mock("../lib/outlook-tasks", () => ({
  outlookTaskApi: vi.fn(),
  prepareOutlookTask: vi.fn(),
}));
const id = "11111111-1111-4111-8111-111111111111";
const sourceId = "22222222-2222-4222-8222-222222222222";
const message = {
  id: "opaque/+message=",
  subject: "Office hours",
  sender: "teacher@example.com",
  to: ["owner@example.com"],
  body: "Please confirm a time.",
  preview: "Please confirm a time.",
  sent_at: "2026-10-24T09:00:00Z",
  open_url: null,
  reason: "Possible request",
  truncated: false,
};
let host: HTMLDivElement,
  root: Root,
  api: StudentEmailApi,
  review: StudentEmailReview;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  review = {
    review_id: id,
    revision: 2,
    source_id: sourceId,
    source_hash: "a".repeat(64),
    expires_at: new Date(Date.now() + 20 * 60_000).toISOString(),
    classification: {
      category: "needs_reply",
      reasons: [],
      evidence: [],
      proposed_deadline: null,
      ambiguities: [],
    },
    correction: {
      category: "needs_reply",
      not_important: false,
      course_id: null,
      title: "Prepare office-hours questions",
      due_at: null,
      estimated_minutes: 20,
      confirmed_course: true,
      confirmed_deadline: true,
    },
    content_truncated: false,
    reply_handoff: null,
    actions_executed: false,
    task_id: null,
    existing_task: null,
    notice: "Reviewed details",
  };
  api = {
    account: vi.fn(async () => "workspace:owner"),
    context: vi.fn(async () => ({
      timezone: "UTC",
      server_now: new Date().toISOString(),
      courses: [],
    })),
    search: vi.fn(),
    analyze: vi.fn(),
    review: vi.fn(async () => review),
    correct: vi.fn(async () => review),
    accept: vi.fn(async () => ({
      task_id: id,
      reused: false,
      available: true,
      actions_executed: false as const,
      notice: null,
    })),
    erase: vi.fn(),
    forget: vi.fn(),
  };
  vi.mocked(outlookTaskApi).mockReturnValue(api);
  vi.mocked(prepareOutlookTask).mockResolvedValue(review);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
async function mount(accountKey = "workspace:owner") {
  await act(async () =>
    root.render(
      <OutlookTaskCapture
        accountKey={accountKey}
        connectionId={id}
        message={message}
        timezone="UTC"
      />,
    ),
  );
}
function button(label: string) {
  const found = [...host.querySelectorAll("button")].find(
    (value) => value.textContent === label,
  );
  expect(found).toBeDefined();
  return found!;
}
async function prepare() {
  await act(async () => {
    (host.querySelector('input[type="checkbox"]') as HTMLInputElement).click();
  });
  await act(async () => button("Review email as a task").click());
}
it("reads only after consent to the selected message and creates no task while preparing", async () => {
  await mount();
  expect(prepareOutlookTask).not.toHaveBeenCalled();
  expect(button("Review email as a task").disabled).toBe(true);
  await prepare();
  expect(prepareOutlookTask).toHaveBeenCalledWith(
    "workspace:owner",
    id,
    message.id,
    expect.any(String),
    expect.any(AbortSignal),
  );
  expect(api.accept).not.toHaveBeenCalled();
  expect(button("Create reviewed task").disabled).toBe(true);
  expect(host.textContent).not.toContain("No existing Inbox reply flow");
});
it("retries an unknown preview with its original request id", async () => {
  vi.mocked(prepareOutlookTask).mockRejectedValueOnce(
    new StudentEmailRequestError("Connection lost", null, true),
  );
  await mount();
  await prepare();
  const first = vi.mocked(prepareOutlookTask).mock.calls[0];
  await act(async () => button("Check this exact review request").click());
  expect(vi.mocked(prepareOutlookTask).mock.calls[1].slice(0, 4)).toEqual(
    first.slice(0, 4),
  );
  expect(api.accept).not.toHaveBeenCalled();
});
it("requires final task consent and reuses an uncertain acceptance exactly", async () => {
  vi.mocked(api.accept).mockRejectedValueOnce(
    new StudentEmailRequestError("Connection lost", null, true),
  );
  await mount();
  await prepare();
  const consent = [...host.querySelectorAll("label")]
    .find((label) => label.textContent?.includes("I confirm creating a task"))
    ?.querySelector("input");
  expect(consent).toBeDefined();
  await act(async () => consent!.click());
  await act(async () => button("Create reviewed task").click());
  expect(api.accept).toHaveBeenCalledTimes(1);
  const decision = vi.mocked(api.accept).mock.calls[0][1];
  expect(decision).toMatchObject({
    revision: 2,
    source_hash: review.source_hash,
    confirmed: true,
  });
  await act(async () => button("Check this exact task request").click());
  expect(vi.mocked(api.accept).mock.calls[1][1]).toEqual(decision);
  expect(host.textContent).toContain("Task saved");
});
it("drops a delayed private result when the account changes", async () => {
  let resolve!: (value: StudentEmailReview) => void;
  vi.mocked(prepareOutlookTask).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  await mount();
  await prepare();
  const signal = vi.mocked(prepareOutlookTask).mock.calls[0][4];
  await mount("workspace:other");
  expect(signal?.aborted).toBe(true);
  await act(async () => resolve(review));
  expect(host.textContent).not.toContain("Prepare office-hours questions");
  expect(api.accept).not.toHaveBeenCalled();
});
