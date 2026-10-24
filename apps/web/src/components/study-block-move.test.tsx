// @vitest-environment jsdom
import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { StudyBlockMove } from "./study-block-move";

const block = {
  id: "11111111-1111-4111-8111-111111111111",
  task_id: "22222222-2222-4222-8222-222222222222",
  plan_id: "33333333-3333-4333-8333-333333333333",
  title: "Review calculus",
  start_at: "2026-10-24T17:00:00Z",
  end_at: "2026-10-24T18:00:00Z",
  minutes: 60,
  focus_window: false,
  revision: 1,
  status: "planned" as const,
  actual_minutes: 0,
  outcome_at: null,
};
const result = {
  preview_hash: "a".repeat(64),
  before: block,
  after: { ...block, revision: 2 },
  warnings: ["Calendar checked"],
  other_blocks_change: false,
  alarms_change: false,
  calendar_events_change: false,
};
let host: HTMLDivElement, root: Root;
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
async function click(label: string) {
  const b = [...host.querySelectorAll("button")].find(
    (b) => b.textContent === label,
  );
  if (!b) throw Error("Missing " + label);
  await act(async () => b.click());
}
async function mount(saved = vi.fn()) {
  await act(async () =>
    root.render(
      <StrictMode>
        <StudyBlockMove
          block={block}
          accountKey="owner:student"
          timezone="America/New_York"
          now={Date.parse("2026-10-24T12:00:00Z")}
          onSaved={saved}
        />
      </StrictMode>,
    ),
  );
  return saved;
}
it("shows the exact move before confirming with its hash, including Strict Mode", async () => {
  const saved = await mount();
  await click("Move");
  expect(fetch).not.toHaveBeenCalled();
  await click("Review move");
  expect(host.textContent).toContain(
    "Other study blocks, alarms and calendar events stay unchanged.",
  );
  expect(saved).not.toHaveBeenCalled();
  await click("Approve move");
  expect(saved).toHaveBeenCalledTimes(1);
  const [url, options] = vi.mocked(fetch).mock.calls[1];
  expect(url).toMatch(/move\/confirm$/);
  expect(options?.headers).toMatchObject({
    "X-NavoX-Account": "owner:student",
  });
  expect(JSON.parse(String(options?.body)).preview_hash).toBe(
    result.preview_hash,
  );
});
it("cancelling and a rejected preview cannot commit", async () => {
  await mount();
  await click("Move");
  await click("Review move");
  await click("Keep current time");
  expect(fetch).toHaveBeenCalledTimes(1);
  vi.mocked(fetch).mockResolvedValueOnce(
    Response.json({ detail: "That time is busy" }, { status: 409 }),
  );
  await click("Move");
  await click("Review move");
  expect(host.textContent).not.toContain("Approve move");
  expect(host.textContent).toContain("That time is busy");
});

it("previews a suggested start without moving until the exact review is approved", async () => {
  const saved = vi.fn();
  await act(async () =>
    root.render(
      <StudyBlockMove
        block={block}
        accountKey="owner:student"
        timezone="America/New_York"
        now={Date.parse("2026-10-24T07:00:00Z")}
        suggestedStart="2026-10-24T20:00:00Z"
        onSaved={saved}
        onKeepCurrent={vi.fn()}
      />,
    ),
  );
  expect(fetch).not.toHaveBeenCalled();
  await click("Review suggested move");
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(vi.mocked(fetch).mock.calls[0][0]).toMatch(/move\/preview$/);
  expect(
    JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body)).start_at,
  ).toBe("2026-10-24T20:00:00.000Z");
  expect(saved).not.toHaveBeenCalled();
  await click("Approve move");
  expect(saved).toHaveBeenCalledOnce();
});

it("retries an uncertain move with the same reviewed hash and request identity", async () => {
  await mount();
  await click("Move");
  await click("Review move");
  vi.mocked(fetch).mockRejectedValueOnce(new TypeError("Response lost"));
  await click("Approve move");
  expect(host.textContent).toContain("may have saved");
  expect(host.textContent).toContain("Close review");
  await click("Approve move");
  const first = JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body));
  const retry = JSON.parse(String(vi.mocked(fetch).mock.calls[2][1]?.body));
  expect(retry).toEqual(first);
});
