// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { VoxCalendarReview as Review } from "../lib/vox-calendar-review";
import { VoxCalendarReview } from "./vox-calendar-review";

let root: Root, host: HTMLDivElement;
const onReview = vi.fn(),
  onClose = vi.fn();
const review: Review = {
  kind: "calendar-event",
  event_id: "11111111-1111-4111-8111-111111111111",
  status: "pending",
  voice_approval_available: false,
  destination: "/connections#calendar",
};
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("fetch", vi.fn());
  onReview.mockReset();
  onClose.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
async function mount(proposal: Review = review) {
  await act(async () =>
    root.render(
      <VoxCalendarReview
        proposal={proposal}
        onReview={onReview}
        onClose={onClose}
      />,
    ),
  );
}
it("only opens fixed calendar activity after a student click", async () => {
  await mount();
  expect(fetch).not.toHaveBeenCalled();
  expect(onReview).not.toHaveBeenCalled();
  expect(host.textContent).toContain(
    "approve on screen before an event is added",
  );
  const link = host.querySelector("a");
  expect(link?.getAttribute("href")).toBe("/connections#calendar");
  link?.addEventListener("click", (event) => event.preventDefault());
  await act(async () =>
    link?.dispatchEvent(
      new MouseEvent("click", { bubbles: true, cancelable: true }),
    ),
  );
  expect(onReview).toHaveBeenCalledOnce();
  expect(fetch).not.toHaveBeenCalled();
});
it("offers contextual voice review only when the admitted host says it is available", async () => {
  await mount({ ...review, voice_approval_available: true });
  expect(host.textContent).toContain("complete action read-back");
  expect(host.querySelectorAll("button")).toHaveLength(1);
  await act(async () => host.querySelector("button")?.click());
  expect(onClose).toHaveBeenCalledOnce();
  expect(fetch).not.toHaveBeenCalled();
});
it.each(["uncertain", "failed"] as const)(
  "keeps possible study changes explicit for %s outcomes",
  async (status) => {
    await mount({ ...review, status });
    expect(host.textContent).toContain(
      "separately approved study-plan changes",
    );
    expect(host.textContent).not.toContain("nothing changed");
    expect(fetch).not.toHaveBeenCalled();
  },
);
it("distinguishes a verified creation with an available Undo review from a deletion", async () => {
  await mount({ ...review, status: "created", voice_approval_available: true });
  expect(host.textContent).toContain("Event creation was verified");
  expect(host.textContent).toContain("exact Undo action for your review");
  expect(host.textContent).toContain("nothing has been deleted");
  expect(fetch).not.toHaveBeenCalled();
});
