// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { saveMemoryCandidateDraft } from "../lib/vox-memory-save-review";
import { memorySaveReviewFixture } from "../test-fixtures/memory-save-review";
import { MemorySaveReviewPanel } from "./memory-save-review";
import { VoxMemorySaveReview } from "./vox-memory-save-review";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  call: null as null | {
    state: { callId: string };
    controller: { hangUp: () => Promise<void> };
  },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("./vox-call-provider", () => ({ useVoxCall: () => mocks.call }));
let root: Root, host: HTMLDivElement;
const onSaved = vi.fn(),
  onUncertain = vi.fn(),
  onReview = vi.fn(),
  onClose = vi.fn();
function fact() {
  const review = memorySaveReviewFixture().review;
  return {
    id: review.id,
    label: review.label,
    content: review.content,
    source_kind: "vox",
    source_conversation_id: review.source_conversation_id,
    sensitive: true,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}
beforeEach(() => {
  mocks.call = null;
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(fact(), { status: 201 })),
  );
  for (const callback of [onSaved, onUncertain, onReview, onClose, mocks.push])
    callback.mockReset();
  sessionStorage.clear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
async function settings(accountKey = "school:student", disabled = false) {
  await act(async () =>
    root.render(
      <MemorySaveReviewPanel
        accountKey={accountKey}
        disabled={disabled}
        onSaved={onSaved}
        onUncertain={onUncertain}
      />,
    ),
  );
}
function button(label: string) {
  const value = [...host.querySelectorAll("button")].find(
    (node) => node.textContent === label,
  );
  if (!value) throw Error(`Missing ${label}`);
  return value;
}
async function click(label: string) {
  await act(async () => button(label).click());
}
async function agree() {
  const checkbox = host.querySelector<HTMLInputElement>(
    'input[type="checkbox"]',
  );
  if (!checkbox) throw Error("Missing agreement");
  await act(async () => checkbox.click());
}
it("requires both a review-navigation click and independent sensitive confirmation", async () => {
  const proposal = memorySaveReviewFixture();
  await act(async () =>
    root.render(
      <VoxMemorySaveReview
        proposal={proposal}
        accountKey="school:student"
        onReview={onReview}
        onClose={onClose}
      />,
    ),
  );
  expect(fetch).not.toHaveBeenCalled();
  expect(mocks.push).not.toHaveBeenCalled();
  expect(sessionStorage.length).toBe(0);
  await click("Review in Settings");
  expect(onReview).toHaveBeenCalledOnce();
  expect(mocks.push).toHaveBeenCalledWith("/settings#memory-privacy");
  expect(JSON.stringify(mocks.push.mock.calls)).not.toContain(
    proposal.review.proposal,
  );
  await settings();
  expect(host.textContent).toContain(proposal.review.content);
  expect(button("Save this memory").disabled).toBe(true);
  expect(fetch).not.toHaveBeenCalled();
  await agree();
  await click("Save this memory");
  expect(fetch).toHaveBeenCalledOnce();
  const [url, options] = vi.mocked(fetch).mock.calls[0];
  expect(String(url)).toMatch(/\/memory\/candidates\/confirm$/);
  expect(String(url)).not.toContain(proposal.review.proposal);
  expect(JSON.parse(String(options?.body))).toEqual({
    review_id: proposal.review.id,
    proposal: proposal.review.proposal,
    confirmation: "SAVE MEMORY",
    sensitive_agreement: true,
  });
  expect(options?.headers).toMatchObject({
    "X-NavoX-Account": "school:student",
  });
  expect(onSaved).toHaveBeenCalledOnce();
});
it("does not save on Keep, storage failure or an expired review", async () => {
  const proposal = memorySaveReviewFixture();
  await act(async () =>
    root.render(
      <VoxMemorySaveReview
        proposal={proposal}
        accountKey="school:student"
        onReview={onReview}
        onClose={onClose}
      />,
    ),
  );
  await click("Don’t save this");
  expect(onClose).toHaveBeenCalledOnce();
  expect(fetch).not.toHaveBeenCalled();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw Error("unavailable");
  });
  await click("Review in Settings");
  expect(mocks.push).not.toHaveBeenCalled();
  expect(onReview).not.toHaveBeenCalled();
  expect(host.textContent).toContain("could not be opened");
});
it("allows a neutral statement only after the explicit save click without preselecting private agreement", async () => {
  const proposal = memorySaveReviewFixture();
  proposal.review.sensitive = false;
  saveMemoryCandidateDraft(sessionStorage, "school:student", proposal.review);
  await settings();
  expect(
    (host.querySelector('input[type="checkbox"]') as HTMLInputElement).checked,
  ).toBe(false);
  expect(button("Save this memory").disabled).toBe(false);
  expect(fetch).not.toHaveBeenCalled();
  await click("Save this memory");
  expect(
    JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))
      .sensitive_agreement,
  ).toBe(false);
});
it("clears private review text at expiry without saving", async () => {
  vi.useFakeTimers();
  const proposal = memorySaveReviewFixture();
  saveMemoryCandidateDraft(sessionStorage, "school:student", proposal.review);
  await settings();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(120001);
  });
  expect(host.textContent).not.toContain(proposal.review.content);
  expect(host.textContent).toContain("review expired");
  expect(fetch).not.toHaveBeenCalled();
});
it("fences a pending save and private text when the owner changes", async () => {
  const proposal = memorySaveReviewFixture();
  saveMemoryCandidateDraft(sessionStorage, "school:student", proposal.review);
  await settings();
  await agree();
  let resolve: (value: Response) => void = () => {};
  vi.mocked(fetch).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  await act(async () => button("Save this memory").click());
  await settings("school:other");
  expect(host.textContent).not.toContain(proposal.review.content);
  await act(async () => resolve(Response.json(fact(), { status: 201 })));
  expect(onSaved).not.toHaveBeenCalled();
});
it("does not retry an uncertain save before checking saved state", async () => {
  saveMemoryCandidateDraft(
    sessionStorage,
    "school:student",
    memorySaveReviewFixture().review,
  );
  await settings();
  await agree();
  vi.mocked(fetch).mockRejectedValueOnce(Error("lost response"));
  await click("Save this memory");
  expect(onUncertain).toHaveBeenCalledOnce();
  expect(button("Save this memory").disabled).toBe(true);
  await click("Save this memory");
  expect(fetch).toHaveBeenCalledOnce();
  expect(host.textContent).toContain("could not be confirmed");
});
it("does not claim a save when a valid-looking receipt belongs to another source", async () => {
  saveMemoryCandidateDraft(
    sessionStorage,
    "school:student",
    memorySaveReviewFixture().review,
  );
  await settings();
  await agree();
  vi.mocked(fetch).mockResolvedValueOnce(
    Response.json(
      { ...fact(), source_conversation_id: "other-source" },
      { status: 201 },
    ),
  );
  await click("Save this memory");
  expect(onSaved).not.toHaveBeenCalled();
  expect(onUncertain).toHaveBeenCalledOnce();
  expect(button("Save this memory").disabled).toBe(true);
  expect(host.textContent).toContain("could not be confirmed");
});
it("queues a second review until an uncertain first save has been checked", async () => {
  const first = memorySaveReviewFixture(),
    second = memorySaveReviewFixture();
  second.review.id = "10000000-0000-4000-8000-000000000002";
  second.review.content = "Second synthetic private candidate.";
  saveMemoryCandidateDraft(sessionStorage, "school:student", first.review);
  await settings();
  await agree();
  let reject: (reason: Error) => void = () => {};
  vi.mocked(fetch).mockImplementationOnce(
    () =>
      new Promise((_resolve, fail) => {
        reject = fail;
      }),
  );
  await act(async () => button("Save this memory").click());
  saveMemoryCandidateDraft(sessionStorage, "school:student", second.review);
  await act(async () =>
    window.dispatchEvent(new Event("navox:memory-save-review-ready")),
  );
  expect(host.textContent).toContain(first.review.content);
  expect(host.textContent).not.toContain(second.review.content);
  expect(sessionStorage.length).toBe(1);
  await act(async () => reject(Error("lost first response")));
  expect(onUncertain).toHaveBeenCalledOnce();
  await click("Don’t save this");
  expect(host.textContent).toContain("Check saved facts");
  expect(host.textContent).not.toContain("was not saved");
  await act(async () =>
    window.dispatchEvent(new Event("navox:memory-save-review-ready")),
  );
  expect(host.textContent).not.toContain(second.review.content);
  expect(sessionStorage.length).toBe(1);
  // Parent keeps controls disabled until it reloads current saved facts.
  await settings("school:student", true);
  await settings("school:student", false);
  expect(host.textContent).toContain(second.review.content);
  expect(
    (host.querySelector('input[type="checkbox"]') as HTMLInputElement).checked,
  ).toBe(false);
  expect(fetch).toHaveBeenCalledOnce();
});
it("clears the local Vox call before confirming a private source memory", async () => {
  let finish: () => void = () => {};
  const hangUp = vi.fn(
    () =>
      new Promise<void>((done) => {
        finish = done;
      }),
  );
  mocks.call = { state: { callId: "active-call" }, controller: { hangUp } };
  saveMemoryCandidateDraft(
    sessionStorage,
    "school:student",
    memorySaveReviewFixture().review,
  );
  await settings();
  await agree();
  expect(host.textContent).toContain("source conversation also stays out");
  expect(host.textContent).toContain("Saving ends your current Vox call");
  await act(async () => button("Save this memory").click());
  expect(hangUp).toHaveBeenCalledOnce();
  expect(fetch).not.toHaveBeenCalled();
  await act(async () => finish());
  expect(fetch).toHaveBeenCalledOnce();
  expect(onSaved).toHaveBeenCalledOnce();
});
