// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  type SavedBoard,
  type SavedBoardSnapshot,
  type SavedBoardsApi,
  SavedBoardsRequestError,
  snapshotForLesson,
} from "../lib/saved-boards";
import { whiteboardLessons } from "../lib/whiteboard-lessons";
import { initialPlayback } from "../lib/whiteboard-playback";
import { StudentWhiteboard } from "./student-whiteboard";

const accountA =
  "11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222";
const accountB =
  "33333333-3333-4333-8333-333333333333:44444444-4444-4444-8444-444444444444";
const boardId = "55555555-5555-4555-8555-555555555555";
const secondId = "66666666-6666-4666-8666-666666666666";
const courseId = "77777777-7777-4777-8777-777777777777";
const lesson = whiteboardLessons[0];
let host: HTMLDivElement;
let root: Root;
let account: string;
let generation: number;
let boards: Map<string, SavedBoard>;
let api: SavedBoardsApi;

function full(
  snapshot: SavedBoardSnapshot,
  id = boardId,
  revision = 1,
  course_id: string | null = null,
): SavedBoard {
  return {
    id,
    revision,
    course_id,
    created_at: "2026-10-19T12:00:00Z",
    updated_at: "2026-10-19T12:00:00Z",
    title: snapshot.title,
    lesson_id: snapshot.lesson_id,
    lesson_version: snapshot.lesson_version,
    step_index: snapshot.step_index,
    complete: snapshot.complete,
    snapshot: structuredClone(snapshot),
  };
}
function fixture(title = "My private practice", id = boardId) {
  const stepIndex = lesson.steps.findIndex((step) => step.check);
  const checkStep = lesson.steps[stepIndex];
  const snapshot = snapshotForLesson(
    lesson,
    {
      ...initialPlayback(),
      stepIndex,
      revealed: checkStep.actions.length,
      answers: { [checkStep.id]: 1 },
    },
    [checkStep.id],
    [
      {
        id: 4,
        points: [
          { x: 20, y: 30 },
          { x: 40, y: 50 },
        ],
      },
    ],
  );
  return full({ ...snapshot, title }, id);
}
function button(name: string) {
  const found = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
    (item) =>
      (item.getAttribute("aria-label") || item.textContent?.trim()) === name,
  );
  if (!found) throw new Error(`Missing button: ${name}`);
  return found;
}
async function click(name: string) {
  await act(async () => button(name).click());
}
async function settle() {
  await act(async () => {});
}
function select(selector: string, value: string) {
  const input = host.querySelector<HTMLSelectElement>(selector);
  if (!input) throw new Error(`Missing select: ${selector}`);
  act(() => {
    input.value = value;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}
function rename(value: string) {
  const input = host.querySelector<HTMLInputElement>("#board-save-title");
  if (!input) throw new Error("Missing saved Board title");
  act(() => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )?.set?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function timeline(index: number) {
  const item = host.querySelectorAll<HTMLButtonElement>(
    'nav[aria-label="Lesson timeline"] button',
  )[index];
  if (!item) throw new Error("Missing timeline step");
  act(() => item.click());
}
function ink() {
  const element = host.querySelector<SVGSVGElement>(
    'svg[aria-label="Your pen annotations"]',
  );
  if (!element) throw new Error("Missing ink layer");
  return element;
}
function draw() {
  const element = ink();
  const [, , width, height] = (element.getAttribute("viewBox") ?? "")
    .split(" ")
    .map(Number);
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: width,
    bottom: height,
    width,
    height,
    toJSON: () => ({}),
  });
  Object.defineProperty(element, "setPointerCapture", {
    configurable: true,
    value: vi.fn(),
  });
  for (const [type, x, y] of [
    ["pointerdown", 10, 20],
    ["pointermove", 30, 40],
    ["pointerup", 30, 40],
  ] as const) {
    const event = new MouseEvent(type, {
      bubbles: true,
      button: 0,
      clientX: x,
      clientY: y,
    });
    Object.defineProperties(event, {
      pointerId: { value: 7 },
      pointerType: { value: "pen" },
    });
    act(() => element.dispatchEvent(event));
  }
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
async function render(key = accountA) {
  await act(async () =>
    root.render(
      createElement(StudentWhiteboard, {
        storageEnabled: true,
        accountKey: key,
        api,
      }),
    ),
  );
}
function visibleText() {
  return [...host.querySelectorAll<HTMLElement>("*")]
    .filter(
      (element) =>
        !element.closest("[hidden]") && element.childElementCount === 0,
    )
    .map((element) => element.textContent)
    .join(" ");
}

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query === "(prefers-reduced-motion: reduce)",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(() => {
      throw new Error("Tests must use the injected owned API");
    }),
  );
  // Cross-tab events are exercised through the shared storage signal below.
  vi.stubGlobal("BroadcastChannel", undefined);
  account = accountA;
  generation = 0;
  boards = new Map();
  api = {
    account: vi.fn(async () => account),
    list: vi.fn(async () => ({
      generation,
      revision: boards.size,
      boards: [...boards.values()].map(
        ({ snapshot: _, ...summary }) => summary,
      ),
    })),
    courses: vi.fn(async () => [
      {
        id: courseId,
        code: "ECE 101",
        name: "Digital systems",
        term_start: "2026-08-20",
        term_end: "2026-12-20",
        professor: null,
        archived: false,
        revision: 1,
        meetings: [],
      },
    ]),
    open: vi.fn(async (id) => {
      const item = boards.get(id);
      if (!item) throw new SavedBoardsRequestError("Unavailable", 404, false);
      return structuredClone(item);
    }),
    create: vi.fn(async (input) => {
      const item = full(input.snapshot, boardId, 1, input.course_id);
      boards.set(item.id, item);
      return structuredClone(item);
    }),
    update: vi.fn(async (id, input) => {
      const item = full(
        input.snapshot,
        id,
        input.revision + 1,
        boards.get(id)?.course_id ?? null,
      );
      boards.set(id, item);
      return structuredClone(item);
    }),
    erase: vi.fn(async (id) => {
      boards.delete(id);
      ++generation;
    }),
  };
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("saved authored Boards", () => {
  it("saves an explicit title, owned course, answer, marker and ink; then updates the same revision", async () => {
    await render();
    rename("Clock edges before class");
    select("#board-save-course", courseId);
    const index = lesson.steps.findIndex((step) => step.check);
    timeline(index);
    await click(lesson.steps[index].check?.choices[1] ?? "");
    await click("Mark this step for another look");
    await click("Pen");
    draw();
    await click("Save this Board");
    expect(api.create).toHaveBeenCalledTimes(1);
    const input = vi.mocked(api.create).mock.calls[0][0];
    expect(input.generation).toBe(0);
    expect(input.course_id).toBe(courseId);
    expect(input.request_id).toMatch(/^[0-9a-f-]{36}$/u);
    expect(input.snapshot).toMatchObject({
      title: "Clock edges before class",
      step_index: index,
      answers: { [lesson.steps[index].id]: 1 },
      marked: [lesson.steps[index].id],
      strokes: [
        {
          id: 1,
          points: [
            { x: 10, y: 20 },
            { x: 10, y: 20 },
            { x: 30, y: 40 },
          ],
        },
      ],
    });
    expect(button("Save Board changes").disabled).toBe(false);
    expect(
      host.querySelector<HTMLSelectElement>("#board-save-course")?.disabled,
    ).toBe(true);
    rename("Clock edges, reviewed");
    await click("Save Board changes");
    expect(api.update).toHaveBeenCalledWith(
      boardId,
      expect.objectContaining({
        revision: 1,
        snapshot: expect.objectContaining({ title: "Clock edges, reviewed" }),
      }),
      expect.any(AbortSignal),
    );
    expect(api.create).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("later changes need another save.");
  });

  it("reopens saved progress paused and resets the same lesson when a different saved Board opens", async () => {
    boards.set(boardId, fixture());
    boards.set(
      secondId,
      full(
        {
          ...snapshotForLesson(lesson, initialPlayback(), [], []),
          title: "Fresh notebook",
        },
        secondId,
      ),
    );
    await render();
    await click("Reopen My private practice");
    expect(ink().querySelector("path")?.getAttribute("d")).toBe(
      "M20.0,30.0 L40.0,50.0",
    );
    expect(button("Marked for another look").getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(
      host.querySelector(
        '[aria-label="Quick check"] button[aria-pressed="true"]',
      ),
    ).toBeTruthy();
    expect(
      [...host.querySelectorAll("button")].some(
        (item) => item.textContent === "Pause lesson",
      ),
    ).toBe(false);
    await click("Reopen Fresh notebook");
    expect(ink().querySelectorAll("path")).toHaveLength(0);
    expect(host.querySelectorAll("g[data-action-id]")).toHaveLength(0);
    expect(
      button("Mark this step for another look").getAttribute("aria-pressed"),
    ).toBe("false");
    expect(
      host.querySelector<HTMLInputElement>("#board-save-title")?.value,
    ).toBe("Fresh notebook");
  });

  it("can draw and save after reopening a valid Board with the largest safe stroke ID", async () => {
    const saved = fixture();
    saved.snapshot.strokes[0].id = Number.MAX_SAFE_INTEGER;
    boards.set(boardId, saved);
    await render();
    await click("Reopen My private practice");
    await click("Pen");
    draw();
    await click("Save Board changes");
    expect(
      vi
        .mocked(api.update)
        .mock.calls[0][1].snapshot.strokes.map((stroke) => stroke.id),
    ).toEqual([Number.MAX_SAFE_INTEGER, 1]);
  });

  it("filters recent Boards by owned course without guessing a course for independent practice", async () => {
    boards.set(boardId, { ...fixture("Course practice"), course_id: courseId });
    boards.set(secondId, fixture("Independent notes", secondId));
    await render();
    expect(
      host.querySelector<HTMLSelectElement>("#board-save-course")?.value,
    ).toBe("");
    select('[aria-label="Filter saved Boards by course"]', courseId);
    expect(host.textContent).toContain("Course practice");
    expect(host.textContent).not.toContain("Independent notes");
    select('[aria-label="Filter saved Boards by course"]', "none");
    expect(host.textContent).not.toContain("Course practice");
    expect(host.textContent).toContain("Independent notes");
  });

  it("retains an uncertain create's exact command and blocks edits until the same change resolves", async () => {
    await render();
    const create = vi.mocked(api.create);
    create.mockRejectedValueOnce(
      new SavedBoardsRequestError("Unconfirmed", null, true),
    );
    await click("Pen");
    draw();
    await click("Save this Board");
    const command = structuredClone(create.mock.calls[0][0]);
    expect(
      host.querySelector<HTMLInputElement>("#board-save-title")?.disabled,
    ).toBe(true);
    expect(button("Save this Board").disabled).toBe(true);
    // SVG events do not inherit fieldset disabled; the player must fence them itself.
    draw();
    expect(ink().querySelectorAll("path")).toHaveLength(1);
    await click("Check pending change");
    expect(create).toHaveBeenCalledTimes(2);
    expect(create.mock.calls[1][0]).toEqual(command);
    expect(button("Save Board changes").disabled).toBe(false);
    await click("Save Board changes");
    expect(vi.mocked(api.update).mock.calls[0][1].request_id).not.toBe(
      command.request_id,
    );
  });

  it("requires an explicit reload after a revision conflict instead of silently overwriting", async () => {
    boards.set(boardId, fixture());
    await render();
    await click("Reopen My private practice");
    vi.mocked(api.update).mockRejectedValueOnce(
      new SavedBoardsRequestError("Conflict", 409, false),
    );
    await click("Save Board changes");
    expect(button("Save Board changes").disabled).toBe(true);
    expect(api.create).not.toHaveBeenCalled();
    expect(host.textContent).toContain("has not overwritten");
    boards.set(boardId, { ...fixture(), revision: 2 });
    await click("Reload saved Boards");
    await click("Reopen My private practice");
    await click("Save Board changes");
    expect(vi.mocked(api.update).mock.calls[1][1].revision).toBe(2);
  });

  it("permanently erases a reopened Board, clears its private progress and refreshes the create generation", async () => {
    boards.set(boardId, fixture());
    await render();
    await click("Reopen My private practice");
    await click("Erase My private practice");
    await click("Permanently erase My private practice");
    expect(api.erase).toHaveBeenCalledWith(
      boardId,
      expect.objectContaining({ revision: 1 }),
      expect.any(AbortSignal),
    );
    expect(host.textContent).not.toContain("My private practice");
    expect(ink().querySelectorAll("path")).toHaveLength(0);
    expect(host.querySelectorAll("g[data-action-id]")).toHaveLength(0);
    await click("Save this Board");
    expect(vi.mocked(api.create).mock.calls[0][0].generation).toBe(1);
  });

  it("keeps other erasure handles when feature-off reads fail and blocks stale creates", async () => {
    boards.set(boardId, fixture());
    boards.set(secondId, fixture("Erase this too", secondId));
    await render();
    vi.mocked(api.list).mockRejectedValue(
      new SavedBoardsRequestError("Disabled", 404, false),
    );
    await click("Erase My private practice");
    await click("Permanently erase My private practice");
    expect(button("Save this Board").disabled).toBe(true);
    await click("Erase Erase this too");
    await click("Permanently erase Erase this too");
    expect(api.erase).toHaveBeenCalledTimes(2);
    expect(host.textContent).not.toContain("Erase this too");
  });

  it("keeps guided lessons working when saving is unavailable", async () => {
    vi.mocked(api.list).mockRejectedValue(
      new SavedBoardsRequestError("Disabled", 404, false),
    );
    await render();
    expect(host.textContent).toContain("Saving is unavailable");
    expect(button("Save this Board").disabled).toBe(true);
    timeline(1);
    expect(host.querySelectorAll("g[data-action-id]").length).toBeGreaterThan(
      0,
    );
    expect(api.create).not.toHaveBeenCalled();
  });
});

describe("saved Board privacy and delayed results", () => {
  it("masks on tab hide and clears private progress if the returning account changed", async () => {
    boards.set(boardId, fixture());
    await render();
    await click("Reopen My private practice");
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(visibleText()).not.toContain("My private practice");
    expect(ink().closest("[hidden]")).not.toBeNull();
    account = accountB;
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(host.textContent).not.toContain("My private practice");
    expect(ink().querySelectorAll("path")).toHaveLength(0);
  });

  it("masks private content throughout an account preflight and clears it on mismatch", async () => {
    boards.set(boardId, fixture());
    await render();
    await click("Reopen My private practice");
    const check = deferred<string>();
    vi.mocked(api.account).mockReturnValueOnce(check.promise);
    await click("Reload saved Boards");
    expect(visibleText()).not.toContain("My private practice");
    expect(ink().closest("[hidden]")).not.toBeNull();
    account = accountB;
    await act(async () => check.resolve(accountB));
    expect(host.textContent).not.toContain("My private practice");
    expect(ink().querySelectorAll("path")).toHaveLength(0);
  });

  it("rejects a late open response after the account prop switches", async () => {
    boards.set(boardId, fixture());
    await render();
    const late = deferred<SavedBoard>();
    vi.mocked(api.open).mockReturnValueOnce(late.promise);
    await click("Reopen My private practice");
    account = accountB;
    boards.clear();
    await render(accountB);
    await act(async () => late.resolve(fixture("Old account secret")));
    expect(host.textContent).not.toContain("Old account secret");
    expect(host.textContent).not.toContain("My private practice");
    expect(ink().querySelectorAll("path")).toHaveLength(0);
  });

  it("clears old account content when a failed operation reveals that cookies switched", async () => {
    boards.set(boardId, fixture());
    await render();
    await click("Reopen My private practice");
    vi.mocked(api.update).mockImplementationOnce(async () => {
      account = accountB;
      throw new SavedBoardsRequestError("Scope mismatch", 409, false);
    });
    await click("Save Board changes");
    expect(host.textContent).not.toContain("My private practice");
    expect(ink().querySelectorAll("path")).toHaveLength(0);
    expect(visibleText()).toContain("Sign in again");
  });

  it("aborts and ignores a late mutation result while hidden, then retries the same command", async () => {
    await render();
    const late = deferred<SavedBoard>();
    vi.mocked(api.create).mockReturnValueOnce(late.promise);
    await click("Save this Board");
    const command = vi.mocked(api.create).mock.calls[0][0];
    const signal = vi.mocked(api.create).mock.calls[0][1];
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(signal?.aborted).toBe(true);
    await act(async () => late.resolve(full(command.snapshot)));
    expect(visibleText()).not.toContain("Board saved.");
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    await click("Check pending change");
    expect(vi.mocked(api.create).mock.calls[1][0]).toEqual(command);
  });

  it("clears private RAM after a strong session invalidation and reloads only after account revalidation", async () => {
    boards.set(boardId, fixture());
    await render();
    await click("Reopen My private practice");
    act(() => window.dispatchEvent(new Event("storage")));
    expect(host.textContent).not.toContain("My private practice");
    expect(ink().querySelectorAll("path")).toHaveLength(0);
    const calls = vi.mocked(api.account).mock.calls.length;
    await click("Reload saved Boards");
    expect(vi.mocked(api.account).mock.calls.length).toBeGreaterThan(calls);
    expect(host.textContent).toContain("My private practice");
  });

  it("clears a reset generation's current private progress without creating another saved Board", async () => {
    boards.set(boardId, fixture());
    await render();
    await click("Reopen My private practice");
    ++generation;
    boards.clear();
    await click("Reload saved Boards");
    expect(host.textContent).not.toContain("My private practice");
    expect(ink().querySelectorAll("path")).toHaveLength(0);
    expect(api.create).not.toHaveBeenCalled();
    await settle();
  });
});
