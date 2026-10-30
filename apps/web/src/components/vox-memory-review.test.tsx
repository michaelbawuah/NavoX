// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  consumeMemoryForgetDraft,
  type VoxMemoryReview as MemoryReview,
} from "../lib/vox-memory-review";
import { VoxMemoryReview } from "./vox-memory-review";

const mocks = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
const proposal: MemoryReview = {
  status: "review_required",
  applied: false,
  query: "morning routine",
  destination: "/settings?section=memory#memory-privacy",
  matches: [],
  ambiguous: false,
  has_more_matches: false,
  search_complete: false,
  searched_items: 100,
  message: "Review in Settings",
};
let root: Root, host: HTMLDivElement;
const onReview = vi.fn(),
  onClose = vi.fn();
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("fetch", vi.fn());
  mocks.push.mockReset();
  onReview.mockClear();
  onClose.mockClear();
  sessionStorage.clear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
async function mount() {
  await act(async () =>
    root.render(
      <VoxMemoryReview
        proposal={proposal}
        accountKey="school:student"
        onReview={onReview}
        onClose={onClose}
      />,
    ),
  );
}
it("waits for a click, preserves call routing and never performs a delete", async () => {
  await mount();
  expect(fetch).not.toHaveBeenCalled();
  expect(mocks.push).not.toHaveBeenCalled();
  expect(host.textContent).toContain("Nothing has been deleted");
  expect(host.textContent).toContain("More memories may match");
  const button = host.querySelector("button");
  if (!button) throw new Error("Missing review button");
  await act(async () => button.click());
  expect(onReview).toHaveBeenCalledOnce();
  expect(fetch).not.toHaveBeenCalled();
  expect(mocks.push).toHaveBeenCalledWith(
    "/settings?section=memory#memory-privacy",
  );
  expect(mocks.push.mock.calls[0][0]).not.toContain("morning");
  expect(consumeMemoryForgetDraft(sessionStorage, "school:other")).toBeNull();
  expect(consumeMemoryForgetDraft(sessionStorage, "school:student")).toBe(
    "morning routine",
  );
});
it("keeps the review visible if private browser handoff storage is unavailable", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("unavailable");
  });
  await mount();
  const button = host.querySelector("button");
  if (!button) throw new Error("Missing review button");
  await act(async () => button.click());
  expect(mocks.push).not.toHaveBeenCalled();
  expect(onReview).not.toHaveBeenCalled();
  expect(host.textContent).toContain("Open Settings → Memory & privacy");
  expect(fetch).not.toHaveBeenCalled();
});
