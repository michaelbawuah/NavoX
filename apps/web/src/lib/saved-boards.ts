import type {
  SavedBoard,
  SavedBoardCreate,
  SavedBoardErase,
  SavedBoardSnapshot,
  SavedBoardSummary,
  SavedBoardsCollection,
  SavedBoardUpdate,
} from "@navox/contracts";
import {
  readStudentCourse,
  readStudentList,
  type StudentCourse,
} from "./student-schedule";
import { whiteboardLessons } from "./whiteboard-lessons";
import type { PlaybackState } from "./whiteboard-playback";
import { type Lesson, layoutLesson } from "./whiteboard-tutor";

export type {
  SavedBoard,
  SavedBoardCreate,
  SavedBoardErase,
  SavedBoardSnapshot,
  SavedBoardSummary,
  SavedBoardsCollection,
  SavedBoardUpdate,
} from "@navox/contracts";

/** SHA-256 of recursively key-sorted, compact UTF-8 JSON for the entire authored lesson. */
export const authoredBoardEditions: Readonly<Record<string, string>> = {
  "ece-flip-flop":
    "824230ecec3338661047231fd76a153146d2bfc16c6ae9250dfd8d6052d44781",
  "calculus-parts":
    "0acb238372aa182544f00637570ccf35ee800dbc5813cdd75a8c10cbbd31ab13",
  "physics-net-force":
    "44f0727f711efb05f731c5e668e4096699067cce220b2ed5ff4c61b3edca534f",
  "linear-algebra-dot-product":
    "32db1c1717e8774b73d81ddd0a37045dd5c9f059b4c5e79cbc18e6362832835f",
  "statistics-mean-variance":
    "e7bc6bca43696a19648c2572ad074951cf731cabee51bee47e96fd103201120e",
};

const MAX_BODY_BYTES = 2 * 1024 * 1024;
const MAX_REVISION = 2_147_483_647;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/iu;
const LESSON_ID = /^[a-z][a-z0-9-]{0,79}$/u;
const EDITION = /^[0-9a-f]{64}$/u;
const SUMMARY_KEYS = [
  "id",
  "revision",
  "course_id",
  "created_at",
  "updated_at",
  "title",
  "lesson_id",
  "lesson_version",
  "step_index",
  "complete",
] as const;
const INVALID =
  "This saved Board could not be checked. Reload your saved Boards to continue.";

function invalid(): never {
  throw new Error(INVALID);
}

function object(value: unknown, keys?: readonly string[]) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    (Object.getPrototypeOf(value) !== Object.prototype &&
      Object.getPrototypeOf(value) !== null)
  )
    return invalid();
  const item = value as Record<string, unknown>;
  if (keys && Object.keys(item).some((key) => !keys.includes(key)))
    return invalid();
  return item;
}

function integer(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER) {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < min ||
    value > max
  )
    return invalid();
  return value;
}

function boolean(value: unknown) {
  if (typeof value !== "boolean") return invalid();
  return value;
}

function identifier(value: unknown) {
  if (typeof value !== "string" || !LESSON_ID.test(value)) return invalid();
  return value;
}

function uuid(value: unknown) {
  if (typeof value !== "string" || !UUID.test(value)) return invalid();
  return value.toLowerCase();
}

function edition(value: unknown) {
  if (typeof value !== "string" || !EDITION.test(value)) return invalid();
  return value;
}

function title(value: unknown) {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    [...value].length > 160 ||
    /[<>\p{Cc}]/u.test(value)
  )
    return invalid();
  return value;
}

function timestamp(value: unknown) {
  if (
    typeof value !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/u.test(
      value,
    ) ||
    !Number.isFinite(Date.parse(value))
  )
    return invalid();
  return value;
}

/** Return a fresh, finite snapshot tied to the exact currently authored lesson edition. */
export function readSavedBoardSnapshot(value: unknown): SavedBoardSnapshot {
  const item = object(value, [
    "version",
    "lesson_id",
    "lesson_version",
    "title",
    "step_index",
    "revealed",
    "complete",
    "answers",
    "marked",
    "strokes",
  ]);
  const lessonId = identifier(item.lesson_id);
  const lessonVersion = edition(item.lesson_version);
  const lesson = whiteboardLessons.find((entry) => entry.id === lessonId);
  if (
    item.version !== 1 ||
    !lesson ||
    lessonVersion !== authoredBoardEditions[lessonId]
  )
    return invalid();
  const stepIndex = integer(item.step_index, 0, lesson.steps.length - 1);
  const step = lesson.steps[stepIndex];
  const revealed = integer(item.revealed, 0, step.actions.length);
  const complete = boolean(item.complete);
  const answers: Record<string, number> = {};
  for (const [id, choice] of Object.entries(object(item.answers))) {
    const check = lesson.steps.find((entry) => entry.id === id)?.check;
    if (!check) return invalid();
    answers[id] = integer(choice, 0, check.choices.length - 1);
  }
  if (
    complete &&
    (stepIndex !== lesson.steps.length - 1 ||
      revealed !== step.actions.length ||
      (step.check && answers[step.id] === undefined))
  )
    return invalid();
  if (!Array.isArray(item.marked) || item.marked.length > lesson.steps.length)
    return invalid();
  const marked = item.marked.map((id) => {
    if (!lesson.steps.some((entry) => entry.id === id)) return invalid();
    return identifier(id);
  });
  if (new Set(marked).size !== marked.length) return invalid();
  if (!Array.isArray(item.strokes) || item.strokes.length > 80)
    return invalid();
  const scene = layoutLesson(lesson);
  const strokeIds = new Set<number>();
  let points = 0;
  const strokes = item.strokes.map((value) => {
    const stroke = object(value, ["id", "points"]);
    const id = integer(stroke.id, 1);
    if (
      strokeIds.has(id) ||
      !Array.isArray(stroke.points) ||
      stroke.points.length < 1 ||
      stroke.points.length > 1500
    )
      return invalid();
    strokeIds.add(id);
    points += stroke.points.length;
    if (points > 20_000) return invalid();
    return {
      id,
      points: stroke.points.map((value) => {
        const point = object(value, ["x", "y"]);
        if (
          typeof point.x !== "number" ||
          !Number.isFinite(point.x) ||
          point.x < 0 ||
          point.x > scene.width ||
          typeof point.y !== "number" ||
          !Number.isFinite(point.y) ||
          point.y < 0 ||
          point.y > scene.height
        )
          return invalid();
        return { x: point.x, y: point.y };
      }),
    };
  });
  const snapshot: SavedBoardSnapshot = {
    version: 1,
    lesson_id: lessonId,
    lesson_version: lessonVersion,
    title: title(item.title),
    step_index: stepIndex,
    revealed,
    complete,
    answers,
    marked,
    strokes,
  };
  if (
    new TextEncoder().encode(JSON.stringify(snapshot)).length > MAX_BODY_BYTES
  )
    return invalid();
  return snapshot;
}

/** Old edition summaries remain visible so students can still erase them. */
export function readSavedBoardSummary(value: unknown): SavedBoardSummary {
  const item = object(value, SUMMARY_KEYS);
  const createdAt = timestamp(item.created_at);
  const updatedAt = timestamp(item.updated_at);
  if (Date.parse(updatedAt) < Date.parse(createdAt)) return invalid();
  return {
    id: uuid(item.id),
    revision: integer(item.revision, 1, MAX_REVISION),
    course_id: item.course_id === null ? null : uuid(item.course_id),
    created_at: createdAt,
    updated_at: updatedAt,
    title: title(item.title),
    lesson_id: identifier(item.lesson_id),
    lesson_version: edition(item.lesson_version),
    step_index: integer(item.step_index, 0, 19),
    complete: boolean(item.complete),
  };
}

export function readSavedBoard(value: unknown): SavedBoard {
  const item = object(value, [...SUMMARY_KEYS, "snapshot"]);
  const summaryValue = { ...item };
  delete summaryValue.snapshot;
  const summary = readSavedBoardSummary(summaryValue);
  const snapshot = readSavedBoardSnapshot(item.snapshot);
  if (
    summary.title !== snapshot.title ||
    summary.lesson_id !== snapshot.lesson_id ||
    summary.lesson_version !== snapshot.lesson_version ||
    summary.step_index !== snapshot.step_index ||
    summary.complete !== snapshot.complete
  )
    return invalid();
  return { ...summary, snapshot };
}

export function readSavedBoardsCollection(
  value: unknown,
): SavedBoardsCollection {
  const item = object(value, ["generation", "revision", "boards"]);
  if (!Array.isArray(item.boards) || item.boards.length > 50) return invalid();
  const boards = item.boards.map(readSavedBoardSummary);
  if (new Set(boards.map((board) => board.id)).size !== boards.length)
    return invalid();
  return {
    generation: integer(item.generation, 0, MAX_REVISION),
    revision: integer(item.revision, 0, MAX_REVISION),
    boards,
  };
}

export function snapshotForLesson(
  lesson: Lesson,
  playback: PlaybackState,
  marked: Iterable<string>,
  strokes: SavedBoardSnapshot["strokes"],
): SavedBoardSnapshot {
  return readSavedBoardSnapshot({
    version: 1,
    lesson_id: lesson.id,
    lesson_version: authoredBoardEditions[lesson.id],
    title: lesson.title,
    step_index: playback.stepIndex,
    revealed: playback.revealed,
    complete: playback.complete,
    answers: playback.answers,
    marked: [...marked],
    strokes,
  });
}

export function restoredBoardPlayback(
  value: SavedBoardSnapshot,
): PlaybackState {
  const snapshot = readSavedBoardSnapshot(value);
  return {
    stepIndex: snapshot.step_index,
    revealed: snapshot.revealed,
    playing: false,
    complete: snapshot.complete,
    answers: snapshot.answers,
  };
}

export interface SavedBoardsApi {
  account(signal: AbortSignal): Promise<string>;
  list(signal: AbortSignal): Promise<SavedBoardsCollection>;
  open(id: string, signal: AbortSignal): Promise<SavedBoard>;
  create(input: SavedBoardCreate, signal: AbortSignal): Promise<SavedBoard>;
  update(
    id: string,
    input: SavedBoardUpdate,
    signal: AbortSignal,
  ): Promise<SavedBoard>;
  erase(id: string, input: SavedBoardErase, signal: AbortSignal): Promise<void>;
  courses?(signal: AbortSignal): Promise<StudentCourse[]>;
}

export class SavedBoardsRequestError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly uncertain: boolean,
  ) {
    super(message);
    this.name = "SavedBoardsRequestError";
  }
}

async function boundedResponse(response: Response): Promise<unknown> {
  if (
    response.headers
      .get("content-type")
      ?.split(";", 1)[0]
      .trim()
      .toLowerCase() !== "application/json"
  )
    return invalid();
  const length = response.headers.get("content-length");
  if (length && (!/^\d+$/u.test(length) || Number(length) > MAX_BODY_BYTES))
    return invalid();
  if (!response.body) return invalid();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_BODY_BYTES) {
        await reader.cancel();
        return invalid();
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

/** Construction is inert. The account workspace decides when storage may be contacted. */
export function createSavedBoardsApi(
  expectedAccountKey: string,
  fetcher?: typeof fetch,
  baseUrl = apiBaseUrl,
): SavedBoardsApi {
  async function request<T>(
    path: string,
    parse: (value: unknown) => T,
    signal: AbortSignal,
    method = "GET",
    input?: unknown,
  ): Promise<T> {
    const scoped = path !== "/auth/me";
    const parts = expectedAccountKey.split(":");
    if (
      scoped &&
      (parts.length !== 2 || !parts.every((part) => UUID.test(part)))
    )
      throw new SavedBoardsRequestError(
        "Sign in again before opening saved Boards.",
        null,
        false,
      );
    let response: Response | undefined;
    try {
      const body = input === undefined ? undefined : JSON.stringify(input);
      if (body && new TextEncoder().encode(body).length > MAX_BODY_BYTES)
        return invalid();
      response = await (fetcher ?? fetch)(`${baseUrl}${path}`, {
        method,
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]),
        headers: {
          ...(scoped
            ? { "X-NavoX-Account": expectedAccountKey.toLowerCase() }
            : {}),
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        ...(body === undefined ? {} : { body }),
      });
      if (!response.ok) {
        // Do not decode or display provider/server text, which can include private input.
        await response.body?.cancel();
        throw new SavedBoardsRequestError(
          response.status === 409 || response.status === 410
            ? "This saved Board changed or was deleted. Reload your saved Boards."
            : "Saved Boards are unavailable. Reload them before making a change.",
          response.status,
          method !== "GET" &&
            (response.status >= 500 || response.status === 408),
        );
      }
      const value =
        response.status === 204 ? undefined : await boundedResponse(response);
      return parse(value);
    } catch (cause) {
      if (cause instanceof SavedBoardsRequestError) throw cause;
      throw new SavedBoardsRequestError(
        method === "GET"
          ? INVALID
          : "The save or deletion could not be confirmed. Check the same change before making another.",
        response?.status ?? null,
        method !== "GET",
      );
    }
  }
  return {
    account: (signal) =>
      request(
        "/auth/me",
        (value) => {
          const item = object(value);
          return `${uuid(object(item.workspace).id)}:${uuid(item.id)}`;
        },
        signal,
      ),
    list: (signal) =>
      request("/board/saved", readSavedBoardsCollection, signal),
    courses: (signal) =>
      request(
        "/student/courses",
        (value) => readStudentList(value, "courses", readStudentCourse),
        signal,
      ),
    open: (id, signal) =>
      request(`/board/saved/${uuid(id)}`, readSavedBoard, signal),
    create: (input, signal) =>
      request("/board/saved", readSavedBoard, signal, "POST", {
        request_id: uuid(input.request_id),
        generation: integer(input.generation, 0, MAX_REVISION),
        course_id: input.course_id === null ? null : uuid(input.course_id),
        snapshot: readSavedBoardSnapshot(input.snapshot),
      }),
    update: (id, input, signal) =>
      request(`/board/saved/${uuid(id)}`, readSavedBoard, signal, "PATCH", {
        request_id: uuid(input.request_id),
        revision: integer(input.revision, 1, MAX_REVISION),
        snapshot: readSavedBoardSnapshot(input.snapshot),
      }),
    erase: (id, input, signal) =>
      request(
        `/board/saved/${uuid(id)}`,
        (value) => {
          if (value !== undefined) return invalid();
        },
        signal,
        "DELETE",
        {
          request_id: uuid(input.request_id),
          revision: integer(input.revision, 1, MAX_REVISION),
        },
      ),
  };
}
