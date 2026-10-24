// @vitest-environment jsdom
import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VoxPlanReview } from "./vox-plan-review";

const id = "11111111-1111-4111-8111-111111111111";
const task = "22222222-2222-4222-8222-222222222222";
const block = {
  id,
  task_id: task,
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
};
const proposal = {
  kind: "study-plan" as const,
  preview_id: id,
  preview_hash: "a".repeat(64),
  timezone: "America/New_York",
};
const preview = {
  ...proposal,
  expires_at: "2026-10-24T16:30:00Z",
  base_revision: 1,
  requires_acceptance: true,
  replaced_blocks: [block],
  result: {
    start_at: "2026-10-24T16:00:00Z",
    end_at: "2026-10-25T04:00:00Z",
    blocks: [
      {
        ...block,
        title: "Proposed calculus block",
        start_at: "2026-10-24T19:00:00Z",
        end_at: "2026-10-24T20:00:00Z",
      },
    ],
    capacities: [],
    warnings: [],
    unfitted_work: [],
    planning_only: true,
  },
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, init) =>
      init?.method === "POST"
        ? Response.json({
            revision: 2,
            blocks: [],
            needs_review: false,
            has_more_history: false,
            review_notice: null,
          })
        : Response.json(preview),
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
async function mount() {
  await act(async () =>
    root.render(
      <StrictMode>
        <VoxPlanReview
          proposal={proposal}
          accountKey="workspace:student"
          onClose={vi.fn()}
        />
      </StrictMode>,
    ),
  );
}
it("shows every replacement and only applies the exact reviewed hash after explicit approval", async () => {
  await mount();
  expect(host.textContent).toContain("Existing calculus block");
  expect(host.textContent).toContain("Proposed calculus block");
  expect(
    vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === "GET"),
  ).toBe(true);
  const button = [...host.querySelectorAll("button")].find(
    (b) => b.textContent === "Use this plan",
  )!;
  expect(button.disabled).toBe(true);
  await act(async () =>
    host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(),
  );
  await act(async () => button.click());
  const calls = vi
    .mocked(fetch)
    .mock.calls.filter(([, init]) => init?.method === "POST");
  expect(calls).toHaveLength(1);
  expect(calls[0][1]?.headers).toMatchObject({
    "X-NavoX-Account": "workspace:student",
  });
  expect(JSON.parse(String(calls[0][1]?.body))).toMatchObject({
    preview_id: id,
    preview_hash: proposal.preview_hash,
    confirmed: true,
  });
  expect(host.textContent).toContain("Your reviewed study plan is saved.");
});
it("does not offer approval for a changed preview", async () => {
  vi.mocked(fetch).mockImplementation(async () =>
    Response.json({ ...preview, preview_hash: "b".repeat(64) }),
  );
  await mount();
  expect(host.textContent).toContain("The proposal changed");
  expect(host.querySelector('input[type="checkbox"]')).toBeNull();
});
