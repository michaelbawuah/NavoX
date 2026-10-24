// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PlannerWelcome } from "./planner-welcome";

vi.mock("./use-vox-request", () => ({
  useVoxRequest: () => ({ available: false, busy: false, notice: "" }),
}));
const preferences = {
  wake_time: "08:00",
  sleep_time: "23:00",
  focus_windows: [],
  never_schedule: [],
  max_study_minutes: 90,
  break_minutes: 15,
  revision: 1,
  timezone: "America/New_York",
  wake_times: ["07:00", "08:00", "07:00", "08:00", "08:00", "10:00", "10:30"],
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
function change(label: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(
    `input[aria-label="${label}"]`,
  );
  if (!input) throw Error(label);
  act(() => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
it("reviews separate actual class-day and other-day times, including weekend classes", async () => {
  const review = vi.fn(
    async (_value: { wake_times?: string[] | null }) => false,
  );
  await act(async () =>
    root.render(
      <PlannerWelcome
        preferences={preferences}
        hasClasses
        classDays={[0, 2, 6]}
        disabled={false}
        onCapture={vi.fn()}
        onRoutine={review}
      />,
    ),
  );
  change("Class-day wake time", "06:30");
  change("Weekend and non-class-day wake time", "09:00");
  await act(async () =>
    host
      .querySelectorAll("form")[1]
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(review).toHaveBeenCalledTimes(1);
  expect(review.mock.calls[0][0].wake_times).toEqual([
    "06:30",
    "09:00",
    "06:30",
    "09:00",
    "09:00",
    "09:00",
    "06:30",
  ]);
  expect(host.textContent).not.toContain("Routine saved.");
});
it("does not invent class days before class times exist", async () => {
  await act(async () =>
    root.render(
      <PlannerWelcome
        preferences={preferences}
        hasClasses={false}
        classDays={[]}
        disabled={false}
        onCapture={vi.fn()}
        onRoutine={vi.fn()}
      />,
    ),
  );
  expect(
    host.querySelector<HTMLInputElement>(
      'input[aria-label="Class-day wake time"]',
    )?.disabled,
  ).toBe(true);
  expect(host.textContent).toContain("Add your class times");
});
