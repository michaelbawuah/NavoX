// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { studentRequest } from "../lib/student-schedule";
import { studentClassDraftKey } from "../lib/student-voice";
import { StudentClassesWorkspace } from "./student-classes";

vi.mock("./use-vox-request", () => ({
  useVoxRequest: () => ({
    available: true,
    busy: false,
    notice: "",
    ask: vi.fn(),
  }),
}));
vi.mock("./student-time-lookup", () => ({ StudentTimeLookup: () => null }));
vi.mock("../lib/student-schedule", async (original) => ({
  ...(await original<typeof import("../lib/student-schedule")>()),
  studentRequest: vi.fn(),
}));
const owner = "personal:student";
const profile = {
  school_name: "Test school",
  lms: "none",
  display_name: "Student",
  timezone: "America/New_York",
  term_start: "2026-08-20",
  term_end: "2026-12-20",
  revision: 1,
};
const timetable = {
  server_now: "2026-10-19T14:00:00Z",
  timezone: "America/New_York",
  date: "2026-10-19",
  days: 7,
  courses: [],
  fixed_blocks: [],
  breaks: [],
  exceptions: [],
  occurrences: [],
  fixed_occurrences: [],
  conflicts: [],
  warnings: [],
};
let host: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  sessionStorage.clear();
  vi.mocked(studentRequest).mockImplementation(async (path, _parse, method) => {
    if (method && method !== "GET") throw new Error("Unexpected save");
    if (path === "/profile") return profile;
    if (path.startsWith("/courses")) return [];
    return timetable;
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  sessionStorage.clear();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});
function button(text: string) {
  const found = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
    (item) => item.textContent?.trim() === text,
  );
  if (!found) throw new Error(`Missing ${text}`);
  return found;
}
function handoff(
  accountKey = owner,
  transcript = "Add CS 2110 Monday at 10 am to 11 am in Statler",
) {
  sessionStorage.setItem(
    studentClassDraftKey(accountKey),
    JSON.stringify({
      accountKey,
      transcript,
      createdAt: new Date().toISOString(),
    }),
  );
  act(() => window.dispatchEvent(new Event("navox:class-draft-ready")));
}
function courseName() {
  const label = [...host.querySelectorAll("label")].find(
    (item) => item.textContent?.trim() === "Course name",
  );
  const input = label?.querySelector("input");
  if (!input) throw new Error("Missing course name");
  return input;
}
describe("same-page Vox class handoff", () => {
  it("opens the editable class review without saving or remounting Classes", async () => {
    await act(async () =>
      root.render(<StudentClassesWorkspace accountId={owner} />),
    );
    handoff();
    expect(courseName().value).toBe("CS 2110");
    expect(host.textContent).toContain("Nothing has been added yet.");
    expect(sessionStorage.getItem(studentClassDraftKey(owner))).toBeNull();
    expect(
      vi
        .mocked(studentRequest)
        .mock.calls.every(([, , method]) => !method || method === "GET"),
    ).toBe(true);
  });
  it("queues a spoken class while a typed class is being edited", async () => {
    await act(async () =>
      root.render(<StudentClassesWorkspace accountId={owner} />),
    );
    act(() => button("Add a class").click());
    const field = courseName();
    act(() => {
      Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set?.call(field, "My unfinished course");
      field.dispatchEvent(new Event("input", { bubbles: true }));
    });
    handoff();
    expect(courseName().value).toBe("My unfinished course");
    expect(button("Review class from Vox").disabled).toBe(true);
    act(() => button("Cancel").click());
    expect(button("Review class from Vox").disabled).toBe(false);
    act(() => button("Review class from Vox").click());
    expect(courseName().value).toBe("CS 2110");
    expect(
      host.querySelector('[aria-label="Class draft from Vox"]'),
    ).toBeNull();
  });
  it("does not consume another account’s draft or carry a queued draft across accounts", async () => {
    await act(async () =>
      root.render(<StudentClassesWorkspace accountId={owner} />),
    );
    handoff("personal:other");
    expect(host.textContent).not.toContain("You said:");
    expect(
      sessionStorage.getItem(studentClassDraftKey("personal:other")),
    ).not.toBeNull();
    act(() => button("Add a class").click());
    handoff();
    expect(
      host.querySelector('[aria-label="Class draft from Vox"]'),
    ).not.toBeNull();
    await act(async () =>
      root.render(<StudentClassesWorkspace accountId="personal:new" />),
    );
    expect(
      host.querySelector('[aria-label="Class draft from Vox"]'),
    ).toBeNull();
    expect(host.textContent).not.toContain("You said:");
  });
});
