import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import backendCatalog from "../../../../services/api/navox/board/catalog.json";
import {
  authoredBoardEditions,
  createSavedBoardsApi,
  readSavedBoard,
  readSavedBoardSnapshot,
  readSavedBoardSummary,
  readSavedBoardsCollection,
  restoredBoardPlayback,
  type SavedBoard,
  type SavedBoardSnapshot,
  SavedBoardsRequestError,
  snapshotForLesson,
} from "./saved-boards";
import { whiteboardLessons } from "./whiteboard-lessons";
import { initialPlayback } from "./whiteboard-playback";
import { layoutLesson } from "./whiteboard-tutor";

const id = "11111111-1111-4111-8111-111111111111";
const userId = "22222222-2222-4222-8222-222222222222";
const workspaceId = "33333333-3333-4333-8333-333333333333";
const requestId = "44444444-4444-4444-8444-444444444444";
const accountKey = `${workspaceId}:${userId}`;
const signal = () => new AbortController().signal;
function snapshot(
  changes: Partial<SavedBoardSnapshot> = {},
): SavedBoardSnapshot {
  return {
    ...snapshotForLesson(whiteboardLessons[0], initialPlayback(), [], []),
    ...changes,
  };
}
function saved(changes: Partial<SavedBoard> = {}): SavedBoard {
  const value = snapshot();
  return {
    id,
    revision: 1,
    course_id: null,
    created_at: "2026-10-19T09:30:00+00:00",
    updated_at: "2026-10-19T09:30:00Z",
    title: value.title,
    lesson_id: value.lesson_id,
    lesson_version: value.lesson_version,
    step_index: value.step_index,
    complete: value.complete,
    snapshot: value,
    ...changes,
  };
}
function summary() {
  const { snapshot: _, ...value } = saved();
  return value;
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([key, entry]) => `${JSON.stringify(key)}:${canonical(entry)}`)
      .join(",")}}`;
  return JSON.stringify(value);
}

describe("authored Board editions across the server boundary", () => {
  it("pins the complete authored lessons, step limits and renderer dimensions", () => {
    expect(backendCatalog.version).toBe(1);
    expect(Object.keys(authoredBoardEditions).sort()).toEqual(
      whiteboardLessons.map((lesson) => lesson.id).sort(),
    );
    expect(backendCatalog.lessons).toEqual(
      whiteboardLessons.map((lesson) => {
        const scene = layoutLesson(lesson);
        const lessonVersion = createHash("sha256")
          .update(canonical(lesson), "utf8")
          .digest("hex");
        expect(authoredBoardEditions[lesson.id]).toBe(lessonVersion);
        return {
          id: lesson.id,
          lesson_version: lessonVersion,
          steps: lesson.steps.map((step) => ({
            id: step.id,
            actions: step.actions.length,
            choices: step.check?.choices.length ?? null,
          })),
          width: scene.width,
          height: scene.height,
        };
      }),
    );
  });
});

describe("saved Board trust boundary", () => {
  it.each(whiteboardLessons)(
    "round trips $id and restores paused",
    (lesson) => {
      const step = lesson.steps.findIndex((entry) => entry.check);
      const value = snapshotForLesson(
        lesson,
        {
          stepIndex: step,
          revealed: lesson.steps[step].actions.length,
          complete: false,
          playing: true,
          answers: { [lesson.steps[step].id]: 0 },
        },
        [lesson.steps[0].id],
        [{ id: 3, points: [{ x: 0, y: 0 }] }],
      );
      expect(readSavedBoardSnapshot(value)).toEqual(value);
      expect(restoredBoardPlayback(value)).toEqual({
        stepIndex: step,
        revealed: lesson.steps[step].actions.length,
        complete: false,
        playing: false,
        answers: { [lesson.steps[step].id]: 0 },
      });
    },
  );

  it("copies all mutable data rather than retaining a server object", () => {
    const value = snapshot({
      marked: ["ece-setup"],
      answers: { "ece-edge": 0 },
      strokes: [{ id: 1, points: [{ x: 20, y: 40 }] }],
    });
    const parsed = readSavedBoardSnapshot(value);
    value.answers["ece-edge"] = 1;
    value.marked.length = 0;
    value.strokes[0].points[0].x = 99;
    expect(parsed.answers).toEqual({ "ece-edge": 0 });
    expect(parsed.marked).toEqual(["ece-setup"]);
    expect(parsed.strokes[0].points[0].x).toBe(20);
  });

  it.each([
    { version: 2 },
    { lesson_id: "missing-lesson" },
    { lesson_version: "0".repeat(64) },
    { lesson_version: "javascript:alert(1)" },
    { title: "" },
    { title: "x".repeat(161) },
    { title: "<img src=x>" },
    { title: "private\u0000title" },
    { step_index: 3 },
    { step_index: -1 },
    { step_index: 0.5 },
    { revealed: 4 },
    { revealed: -1 },
    { complete: "false" },
    { complete: true },
    { answers: { missing: 0 } },
    { answers: { "ece-setup": 0 } },
    { answers: { "ece-edge": 3 } },
    { answers: { "ece-edge": false } },
    { answers: JSON.parse('{"__proto__":0}') },
    { marked: ["ece-setup", "ece-setup"] },
    { marked: ["missing"] },
    { playing: true },
    { strokes: [{ id: 0, points: [{ x: 0, y: 0 }] }] },
    { strokes: [{ id: 1, points: [] }] },
    { strokes: [{ id: 1, points: [{ x: Number.NaN, y: 0 }] }] },
    { strokes: [{ id: 1, points: [{ x: 0, y: Number.POSITIVE_INFINITY }] }] },
    { strokes: [{ id: 1, points: [{ x: -0.1, y: 0 }] }] },
    { strokes: [{ id: 1, points: [{ x: 1121, y: 0 }] }] },
    { strokes: [{ id: 1, points: [{ x: 0, y: 593 }] }] },
    { strokes: [{ id: 1, points: [{ x: 0, y: 0, pressure: 1 }] }] },
    {
      strokes: [
        { id: 1, points: [{ x: 0, y: 0 }] },
        { id: 1, points: [{ x: 0, y: 0 }] },
      ],
    },
  ])("rejects invalid or untrusted snapshot %#", (change) => {
    expect(() => readSavedBoardSnapshot({ ...snapshot(), ...change })).toThrow(
      /could not be checked/u,
    );
  });

  it("requires the final quick check before complete, without claiming correctness", () => {
    const value = snapshot({ step_index: 2, revealed: 1, complete: true });
    expect(() => readSavedBoardSnapshot(value)).toThrow();
    expect(
      readSavedBoardSnapshot({ ...value, answers: { "ece-example": 0 } })
        .complete,
    ).toBe(true);
  });

  it.each([
    [
      Array.from({ length: 81 }, (_, index) => ({
        id: index + 1,
        points: [{ x: 0, y: 0 }],
      })),
    ],
    [[{ id: 1, points: Array.from({ length: 1501 }, () => ({ x: 0, y: 0 })) }]],
    [
      Array.from({ length: 14 }, (_, index) => ({
        id: index + 1,
        points: Array.from({ length: 1500 }, () => ({ x: 0, y: 0 })),
      })),
    ],
  ])("rejects stroke and aggregate point overflows %#", (strokes) => {
    expect(() => readSavedBoardSnapshot(snapshot({ strokes }))).toThrow();
  });

  it("accepts exact point and scene limits", () => {
    const strokes = Array.from({ length: 14 }, (_, index) => ({
      id: index + 1,
      points: Array.from({ length: index === 13 ? 500 : 1500 }, () => ({
        x: 1120,
        y: 592,
      })),
    }));
    expect(readSavedBoardSnapshot(snapshot({ strokes })).strokes).toHaveLength(
      14,
    );
  });

  it("keeps stale lesson editions listable for deletion, but cannot open them", () => {
    const stale = { ...summary(), lesson_version: "0".repeat(64) };
    expect(readSavedBoardSummary(stale)).toEqual(stale);
    expect(() =>
      readSavedBoard({
        ...stale,
        snapshot: snapshot({ lesson_version: stale.lesson_version }),
      }),
    ).toThrow();
  });

  it.each([
    { title: "different title" },
    { lesson_id: "calculus-parts" },
    { lesson_version: "0".repeat(64) },
    { step_index: 1 },
    { complete: true },
  ])("rejects inconsistent summary bindings %#", (change) => {
    expect(() => readSavedBoard({ ...saved(), ...change })).toThrow();
  });

  it.each([
    { id: "../../other-owner" },
    { course_id: "missing-id" },
    { revision: 0 },
    { revision: 1.5 },
    { created_at: "yesterday" },
    { updated_at: "2026-10-18T09:30:00Z" },
    { step_index: 20 },
    { private_extra: "should not exist" },
  ])("rejects malformed summaries %#", (change) => {
    expect(() => readSavedBoardSummary({ ...summary(), ...change })).toThrow();
  });

  it("checks collection ownership handles, generations and duplicate IDs", () => {
    const value = { generation: 0, revision: 2, boards: [summary()] };
    expect(readSavedBoardsCollection(value)).toEqual(value);
    for (const change of [
      { generation: -1 },
      { revision: -1 },
      { boards: [summary(), summary()] },
      { owner_id: id },
    ])
      expect(() =>
        readSavedBoardsCollection({ ...value, ...change }),
      ).toThrow();
  });
});

describe("saved Board HTTP client", () => {
  it("is inert until called and proves the workspace/user account order", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        Response.json({ id: userId, workspace: { id: workspaceId } }),
      );
    const api = createSavedBoardsApi(
      accountKey,
      fetcher,
      "https://api.navox.test/api/v1",
    );
    expect(fetcher).not.toHaveBeenCalled();
    await expect(api.account(signal())).resolves.toBe(
      `${workspaceId}:${userId}`,
    );
    expect(fetcher).toHaveBeenCalledWith(
      "https://api.navox.test/api/v1/auth/me",
      expect.objectContaining({
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("loads, opens, saves with exact command fences, updates and erases", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        Response.json({ generation: 2, revision: 7, boards: [summary()] }),
      )
      .mockResolvedValueOnce(Response.json(saved()))
      .mockResolvedValueOnce(Response.json(saved(), { status: 201 }))
      .mockResolvedValueOnce(Response.json(saved({ revision: 2 })))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const api = createSavedBoardsApi(
      accountKey,
      fetcher,
      "https://api.navox.test/api/v1",
    );
    expect((await api.list(signal())).generation).toBe(2);
    expect(await api.open(id, signal())).toEqual(saved());
    const input = {
      request_id: requestId,
      generation: 2,
      course_id: null,
      snapshot: snapshot(),
    };
    expect(await api.create(input, signal())).toEqual(saved());
    expect(fetcher.mock.calls[2][1]).toEqual(
      expect.objectContaining({
        method: "POST",
        headers: {
          "X-NavoX-Account": accountKey,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(input),
      }),
    );
    expect(
      (
        await api.update(
          id,
          { request_id: requestId, revision: 1, snapshot: snapshot() },
          signal(),
        )
      ).revision,
    ).toBe(2);
    await expect(
      api.erase(id, { request_id: requestId, revision: 2 }, signal()),
    ).resolves.toBeUndefined();
    expect(fetcher.mock.calls[4]).toEqual([
      `https://api.navox.test/api/v1/board/saved/${id}`,
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({ request_id: requestId, revision: 2 }),
      }),
    ]);
  });

  it.each([400, 401, 403, 404, 409, 410, 422, 429, 500, 502, 408])(
    "never echoes private error bodies for status %i",
    async (status) => {
      const api = createSavedBoardsApi(
        accountKey,
        vi
          .fn<typeof fetch>()
          .mockResolvedValue(
            Response.json({ detail: "PRIVATE STUDENT CONTENT" }, { status }),
          ),
      );
      const cause = await api
        .create(
          {
            request_id: requestId,
            generation: 0,
            course_id: null,
            snapshot: snapshot(),
          },
          signal(),
        )
        .catch((error: unknown) => error);
      expect(cause).toBeInstanceOf(SavedBoardsRequestError);
      expect(cause).toMatchObject({
        status,
        uncertain: status >= 500 || status === 408,
      });
      expect(String(cause)).not.toContain("PRIVATE");
    },
  );

  it("marks an unreadable successful mutation as uncertain without exposing content", async () => {
    const api = createSavedBoardsApi(
      accountKey,
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(Response.json({ secret: "PRIVATE" })),
    );
    const cause = await api
      .update(
        id,
        { request_id: requestId, revision: 1, snapshot: snapshot() },
        signal(),
      )
      .catch((error: unknown) => error);
    expect(cause).toMatchObject({ status: 200, uncertain: true });
    expect(String(cause)).not.toContain("PRIVATE");
  });

  it("sanitizes rejected fetch details and preserves mutation uncertainty", async () => {
    const api = createSavedBoardsApi(
      accountKey,
      vi
        .fn<typeof fetch>()
        .mockRejectedValue(new Error("PRIVATE STUDENT CONTENT")),
    );
    await expect(api.list(signal())).rejects.toMatchObject({
      status: null,
      uncertain: false,
    });
    const cause = await api
      .erase(id, { request_id: requestId, revision: 1 }, signal())
      .catch((error: unknown) => error);
    expect(cause).toMatchObject({ status: null, uncertain: true });
    expect(String(cause)).not.toContain("PRIVATE");
  });

  it("propagates caller cancellation through the combined signal", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json(saved()));
    const api = createSavedBoardsApi(accountKey, fetcher);
    const controller = new AbortController();
    await api.open(id, controller.signal);
    const sent = fetcher.mock.calls[0][1]?.signal;
    expect(sent?.aborted).toBe(false);
    controller.abort();
    expect(sent?.aborted).toBe(true);
  });

  it.each([
    new Response("PRIVATE", { headers: { "Content-Type": "text/html" } }),
    new Response("PRIVATE", {
      headers: {
        "Content-Type": "application/json",
        "Content-Length": "3000000",
      },
    }),
    new Response("[".repeat(2 * 1024 * 1024 + 1), {
      headers: { "Content-Type": "application/json" },
    }),
    new Response("{broken PRIVATE", {
      headers: { "Content-Type": "application/json" },
    }),
  ])("bounds and validates response bodies %#", async (response) => {
    const api = createSavedBoardsApi(
      accountKey,
      vi.fn<typeof fetch>().mockResolvedValue(response),
    );
    await expect(api.open(id, signal())).rejects.toMatchObject({
      status: 200,
      uncertain: false,
    });
  });

  it("rejects invalid paths and snapshots before any network call", () => {
    const fetcher = vi.fn<typeof fetch>();
    const api = createSavedBoardsApi(accountKey, fetcher);
    expect(() => api.open("../other", signal())).toThrow();
    expect(() =>
      api.create(
        {
          request_id: requestId,
          generation: 0,
          course_id: null,
          snapshot: snapshot({ lesson_version: "0".repeat(64) }),
        },
        signal(),
      ),
    ).toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps the expected account binding after a cookie account change", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        Response.json({ id, workspace: { id: workspaceId } }),
      )
      .mockResolvedValueOnce(
        Response.json({ detail: "Account changed" }, { status: 409 }),
      );
    const api = createSavedBoardsApi(accountKey, fetcher);
    expect(await api.account(signal())).toBe(`${workspaceId}:${id}`);
    await expect(
      api.erase(id, { request_id: requestId, revision: 1 }, signal()),
    ).rejects.toMatchObject({ status: 409, uncertain: false });
    expect(fetcher.mock.calls[0][1]?.headers).toEqual({});
    expect(fetcher.mock.calls[1][1]?.headers).toEqual({
      "X-NavoX-Account": accountKey,
      "Content-Type": "application/json",
    });
  });

  it("refuses scoped calls without an explicit valid account binding", async () => {
    const fetcher = vi.fn<typeof fetch>();
    for (const key of ["", id, `${workspaceId}:${userId}:extra`, "work:owner"])
      await expect(
        createSavedBoardsApi(key, fetcher).list(signal()),
      ).rejects.toMatchObject({ status: null, uncertain: false });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects revision overflow before sending a mutation", () => {
    const fetcher = vi.fn<typeof fetch>();
    const api = createSavedBoardsApi(accountKey, fetcher);
    expect(() =>
      api.erase(
        id,
        { request_id: requestId, revision: 2_147_483_648 },
        signal(),
      ),
    ).toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });
});
