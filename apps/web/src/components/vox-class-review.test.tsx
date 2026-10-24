// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  consumeStudentClassDraft,
  studentClassDraftKey,
} from "../lib/student-voice";
import { VoxClassReview } from "./vox-class-review";

const mocks = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
const proposal = {
  kind: "class" as const,
  preview_id: "11111111-1111-4111-8111-111111111111",
  preview_hash: "a".repeat(64),
  timezone: "America/New_York",
  transcript: "Add CS 2110 lecture Monday 10am to 11am in Science Hall 101",
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("fetch", vi.fn());
  sessionStorage.clear();
  mocks.push.mockReset();
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
      <VoxClassReview
        proposal={proposal}
        accountKey="workspace:student"
        onClose={vi.fn()}
      />,
    ),
  );
}
it("opens an owner-bound expiring class draft without saving or starting another microphone", async () => {
  await mount();
  expect(sessionStorage.length).toBe(0);
  expect(host.textContent).toContain("Nothing has been added");
  act(() => host.querySelector("button")!.click());
  expect(mocks.push).toHaveBeenCalledWith("/classes?add=1");
  expect(fetch).not.toHaveBeenCalled();
  expect(
    consumeStudentClassDraft(sessionStorage, "workspace:foreign"),
  ).toBeNull();
  expect(consumeStudentClassDraft(sessionStorage, "workspace:student")).toBe(
    proposal.transcript,
  );
  expect(
    sessionStorage.getItem(studentClassDraftKey("workspace:student")),
  ).toBeNull();
});
it("keeps the review visible when storage is unavailable", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  await mount();
  act(() => host.querySelector("button")!.click());
  expect(mocks.push).not.toHaveBeenCalled();
  expect(host.textContent).toContain("could not carry the note");
});
