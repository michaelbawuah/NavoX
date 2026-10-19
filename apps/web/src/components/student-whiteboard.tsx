"use client";

import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import {
  createSavedBoardsApi,
  readSavedBoardSnapshot,
  restoredBoardPlayback,
  type SavedBoard,
  type SavedBoardSnapshot,
  type SavedBoardsApi,
  type SavedBoardsCollection,
  SavedBoardsRequestError,
  snapshotForLesson,
} from "../lib/saved-boards";
import type { StudentCourse } from "../lib/student-schedule";
import { useWhiteboardMotion } from "../lib/use-whiteboard-motion";
import { whiteboardLessons } from "../lib/whiteboard-lessons";
import {
  advancePlayback,
  initialPlayback,
  needsAnswer,
  visibleBoardActions,
} from "../lib/whiteboard-playback";
import { type Lesson, layoutLesson, stripCues } from "../lib/whiteboard-tutor";
import styles from "./student-whiteboard.module.css";
import { WhiteboardScene } from "./whiteboard-scene";
import "katex/dist/katex.min.css";

type Point = { x: number; y: number };
type Stroke = { id: number; points: Point[] };

type PendingBoardChange =
  | { type: "create"; input: Parameters<SavedBoardsApi["create"]>[0] }
  | {
      type: "update";
      id: string;
      input: Parameters<SavedBoardsApi["update"]>[1];
    }
  | {
      type: "erase";
      id: string;
      input: Parameters<SavedBoardsApi["erase"]>[1];
    };

type BoardProps = {
  storageEnabled?: boolean;
  accountKey?: string;
  api?: SavedBoardsApi;
};

export function StudentWhiteboard({
  storageEnabled = false,
  accountKey = "",
  api,
}: BoardProps) {
  const boundApi = useMemo(
    () => api ?? createSavedBoardsApi(accountKey),
    [accountKey, api],
  );
  return (
    <StudentWhiteboardSession
      key={`${storageEnabled}:${accountKey}`}
      storageEnabled={storageEnabled}
      accountKey={accountKey}
      api={boundApi}
    />
  );
}

function StudentWhiteboardSession({
  storageEnabled = false,
  accountKey = "",
  api,
}: Required<BoardProps>) {
  const [lessonId, setLessonId] = useState(whiteboardLessons[0].id);
  const [instance, setInstance] = useState(0);
  const [collection, setCollection] = useState<SavedBoardsCollection | null>(
    null,
  );
  const [courses, setCourses] = useState<StudentCourse[]>([]);
  const [selected, setSelected] = useState<SavedBoard | null>(null);
  const [restored, setRestored] = useState<SavedBoardSnapshot>();
  const [title, setTitle] = useState(whiteboardLessons[0].title);
  const [courseId, setCourseId] = useState("");
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<PendingBoardChange | null>(null);
  const [blocked, setBlocked] = useState(false);
  const [masked, setMasked] = useState(false);
  const [notice, setNotice] = useState("Loading your saved Boards…");
  const [confirmErase, setConfirmErase] = useState<string | null>(null);
  const snapshot = useRef<((title: string) => SavedBoardSnapshot) | null>(null);
  const collectionNow = useRef(collection);
  collectionNow.current = collection;
  const epoch = useRef(0);
  const active = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const busyNow = useRef(false);
  const pendingNow = useRef<PendingBoardChange | null>(null);
  const lesson = whiteboardLessons.find((item) => item.id === lessonId);

  const clearPrivate = useCallback(() => {
    setCollection(null);
    setCourses([]);
    setSelected(null);
    setRestored(undefined);
    setTitle(whiteboardLessons[0].title);
    setLessonId(whiteboardLessons[0].id);
    setCourseId("");
    setFilter("all");
    setConfirmErase(null);
    pendingNow.current = null;
    setPending(null);
    snapshot.current = null;
    setInstance((value) => value + 1);
  }, []);

  const invalidate = useCallback(() => {
    ++epoch.current;
    active.current?.abort();
    active.current = null;
    busyNow.current = false;
    setBusy(false);
    setMasked(true);
  }, []);

  const run = useCallback(
    async <T,>(
      operation: (signal: AbortSignal) => Promise<T>,
      success: (value: T) => void,
      failure: (cause: unknown) => void,
    ) => {
      if (!storageEnabled || !accountKey || busyNow.current) return;
      busyNow.current = true;
      setBusy(true);
      setMasked(true);
      const current = epoch.current;
      const controller = new AbortController();
      active.current = controller;
      const valid = () =>
        mounted.current &&
        current === epoch.current &&
        !controller.signal.aborted;
      let checkingAccount = true;
      try {
        const before = await api.account(controller.signal);
        if (!valid()) return;
        if (before !== accountKey) throw new Error("Account changed");
        checkingAccount = false;
        const result = await operation(controller.signal);
        if (!valid()) return;
        checkingAccount = true;
        const after = await api.account(controller.signal);
        if (!valid()) return;
        if (after !== accountKey) throw new Error("Account changed");
        checkingAccount = false;
        setMasked(false);
        success(result);
      } catch (cause) {
        if (!valid()) return;
        if (!checkingAccount) {
          try {
            const afterFailure = await api.account(controller.signal);
            if (!valid()) return;
            checkingAccount = afterFailure !== accountKey;
          } catch {
            if (!valid()) return;
            checkingAccount = true;
          }
        }
        if (checkingAccount) {
          clearPrivate();
          setMasked(true);
          setBlocked(true);
          setNotice(
            "Your private Board was cleared. Sign in again, then reload saved Boards.",
          );
        } else {
          setMasked(false);
          failure(cause);
        }
      } finally {
        if (active.current === controller) {
          active.current = null;
          busyNow.current = false;
          if (mounted.current) setBusy(false);
        }
      }
    },
    [accountKey, api, clearPrivate, storageEnabled],
  );

  const reload = useCallback(
    (discard = false) => {
      if (pendingNow.current) {
        setNotice(
          "Check the pending change before reloading or making another change.",
        );
        return;
      }
      void run(
        async (signal) => {
          const boards = await api.list(signal);
          const ownedCourses = api.courses
            ? await api.courses(signal).catch(() => [])
            : [];
          return { boards, ownedCourses };
        },
        ({ boards, ownedCourses }) => {
          if (
            collectionNow.current &&
            collectionNow.current.generation !== boards.generation
          ) {
            setSelected(null);
            setRestored(undefined);
            setTitle(whiteboardLessons[0].title);
            setLessonId(whiteboardLessons[0].id);
            setCourseId("");
            setInstance((value) => value + 1);
          }
          setCollection(boards);
          setCourses(ownedCourses.filter((course) => !course.archived));
          if (discard) {
            setSelected(null);
            setRestored(undefined);
            setInstance((value) => value + 1);
            setTitle(
              whiteboardLessons.find((item) => item.id === lessonId)?.title ??
                "Board",
            );
            setCourseId("");
          }
          setBlocked(false);
          setNotice(
            "Saved Boards are private to this account. Save when you want to keep a lesson.",
          );
        },
        () => {
          setMasked(false);
          setNotice(
            "Saving is unavailable on this deployment. You can still explore the guided lessons. Retained saved Board controls can still erase a Board.",
          );
        },
      );
    },
    [api, lessonId, run],
  );
  const reloadRef = useRef(reload);
  reloadRef.current = reload;

  useEffect(() => {
    mounted.current = true;
    if (!storageEnabled)
      return () => {
        mounted.current = false;
      };
    const suspend = () => invalidate();
    const resume = () => {
      if (document.visibilityState !== "visible") return;
      if (pendingNow.current) {
        void run(
          async () => undefined,
          () =>
            setNotice(
              "A change is awaiting confirmation. Check the same change before continuing.",
            ),
          () => setNotice("Sign in again before checking the pending change."),
        );
      } else reloadRef.current();
    };
    const visibility = () => {
      if (document.visibilityState === "visible") resume();
      else suspend();
    };
    const clear = () => {
      invalidate();
      clearPrivate();
      setBlocked(true);
      setNotice(
        "Private Board data was cleared. Reload saved Boards to continue.",
      );
    };
    const channel =
      typeof BroadcastChannel === "undefined"
        ? null
        : new BroadcastChannel("navox-account-session");
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("pagehide", clear);
    window.addEventListener("storage", clear);
    channel?.addEventListener("message", clear);
    if (document.visibilityState === "visible") reloadRef.current();
    else suspend();
    return () => {
      mounted.current = false;
      ++epoch.current;
      active.current?.abort();
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pagehide", clear);
      window.removeEventListener("storage", clear);
      channel?.removeEventListener("message", clear);
      channel?.close();
    };
  }, [clearPrivate, invalidate, run, storageEnabled]);

  function resetLesson(id: string) {
    const next = whiteboardLessons.find((item) => item.id === id);
    if (!next) return;
    setLessonId(id);
    setTitle(next.title);
    setCourseId("");
    setSelected(null);
    setRestored(undefined);
    setConfirmErase(null);
    setInstance((value) => value + 1);
  }

  function newLesson(id: string) {
    if (busyNow.current || pendingNow.current) return;
    resetLesson(id);
  }

  function openBoard(id: string) {
    if (pendingNow.current) return;
    void run(
      (signal) => api.open(id, signal),
      (saved) => {
        setSelected(saved);
        setRestored(saved.snapshot);
        setLessonId(saved.lesson_id);
        setTitle(saved.title);
        setCourseId(saved.course_id ?? "");
        setInstance((value) => value + 1);
        setBlocked(false);
        setNotice("Saved Board reopened, paused at your saved step.");
      },
      () => {
        setBlocked(true);
        setNotice(
          "This saved Board could not be reopened. It may have changed, been erased, or use an older lesson edition. Reload to check it; erasure remains available.",
        );
      },
    );
  }

  function mutate(command: PendingBoardChange) {
    pendingNow.current = command;
    setPending(command);
    void run(
      async (signal) => {
        if (command.type === "create")
          return {
            saved: await api.create(command.input, signal),
            fresh: null,
          };
        if (command.type === "update")
          return {
            saved: await api.update(command.id, command.input, signal),
            fresh: null,
          };
        await api.erase(command.id, command.input, signal);
        const fresh = await api.list(signal).catch(() => null);
        return { saved: null, fresh };
      },
      ({ saved, fresh }) => {
        pendingNow.current = null;
        setPending(null);
        if (saved) {
          setSelected(saved);
          setCollection((previous) =>
            previous
              ? {
                  ...previous,
                  boards: [
                    saved,
                    ...previous.boards.filter((item) => item.id !== saved.id),
                  ],
                }
              : previous,
          );
          setNotice(
            "Board saved. Your completed place, answers, revisit marks and drawings are kept. Unfinished lesson writing restarts when reopened; later changes need another save.",
          );
        } else if (command.type === "erase") {
          setCollection(
            (previous) =>
              fresh ??
              (previous
                ? {
                    ...previous,
                    boards: previous.boards.filter(
                      (item) => item.id !== command.id,
                    ),
                  }
                : previous),
          );
          setBlocked(!fresh);
          if (selected?.id === command.id) resetLesson(lessonId);
          setConfirmErase(null);
          setNotice(
            fresh
              ? "Saved Board permanently erased."
              : "Saved Board permanently erased. Reload saved Boards before saving another; other retained erasure controls remain available.",
          );
        }
      },
      (cause) => {
        if (cause instanceof SavedBoardsRequestError && cause.uncertain) {
          setNotice(
            "The change could not be confirmed. Check this same change before making another; your original request will be retried safely.",
          );
          return;
        }
        pendingNow.current = null;
        setPending(null);
        if (
          cause instanceof SavedBoardsRequestError &&
          [409, 410, 404].includes(cause.status ?? 0)
        ) {
          setBlocked(true);
          setNotice(
            "This saved Board changed or was erased. Reload saved Boards and reopen the saved version before saving again. Your current progress has not overwritten it.",
          );
        } else {
          setNotice(
            "This change could not be saved. Saving may be unavailable on this deployment. Your guided lesson remains open.",
          );
        }
      },
    );
  }

  function save() {
    if (
      !collection ||
      !snapshot.current ||
      blocked ||
      pendingNow.current ||
      busyNow.current
    )
      return;
    let captured: SavedBoardSnapshot;
    try {
      captured = snapshot.current(title.trim());
    } catch {
      setNotice(
        "Give this Board a title of 1–160 characters and keep drawings within the displayed limit before saving.",
      );
      return;
    }
    const request_id = crypto.randomUUID();
    mutate(
      selected
        ? {
            type: "update",
            id: selected.id,
            input: {
              request_id,
              revision: selected.revision,
              snapshot: captured,
            },
          }
        : {
            type: "create",
            input: {
              request_id,
              generation: collection.generation,
              course_id: courseId || null,
              snapshot: captured,
            },
          },
    );
  }

  const capture = useCallback((get: (title: string) => SavedBoardSnapshot) => {
    snapshot.current = get;
    return () => {
      if (snapshot.current === get) snapshot.current = null;
    };
  }, []);
  if (!lesson) return <p>This lesson could not be opened.</p>;
  const locked = busy || Boolean(pending);
  const recent =
    collection?.boards.filter(
      (item) =>
        filter === "all" ||
        (filter === "none"
          ? item.course_id === null
          : item.course_id === filter),
    ) ?? [];
  const courseName = (id: string | null) =>
    id
      ? (courses.find((course) => course.id === id)?.code ?? "Saved course")
      : "Independent practice";

  return (
    <section className={styles.workspace} aria-label="NavoX Board">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>A little clearer, one step at a time</p>
          <h1>NavoX Board.</h1>
          <p>See the idea take shape. Pause, try a step, and make it yours.</p>
        </div>
        <span className={styles.badge}>Guided lessons</span>
      </header>
      {storageEnabled && (
        <section className={styles.savedPanel} aria-label="Saved Boards">
          <p role="status">{busy ? "Checking your saved Boards…" : notice}</p>
          <button
            type="button"
            disabled={busy || Boolean(pending)}
            onClick={() => reload(blocked)}
          >
            Reload saved Boards
          </button>
          {masked && (
            <p>Your private Board is hidden while your sign-in is checked.</p>
          )}
          {pending && !masked && (
            <button
              type="button"
              disabled={busy}
              onClick={() => mutate(pending)}
            >
              Check pending change
            </button>
          )}
          <div hidden={masked}>
            <div className={styles.saveFields}>
              <label htmlFor="board-save-title">Saved Board title</label>
              <input
                id="board-save-title"
                value={title}
                maxLength={160}
                disabled={locked}
                onChange={(event) => setTitle(event.target.value)}
              />
              <label htmlFor="board-save-course">Course</label>
              <select
                id="board-save-course"
                value={courseId}
                disabled={locked || Boolean(selected)}
                onChange={(event) => setCourseId(event.target.value)}
              >
                <option value="">Independent practice</option>
                {selected?.course_id &&
                  !courses.some(
                    (course) => course.id === selected.course_id,
                  ) && <option value={selected.course_id}>Saved course</option>}
                {courses.map((course) => (
                  <option key={course.id} value={course.id}>
                    {course.code} · {course.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className={styles.savePrimary}
                disabled={locked || blocked || !collection || !title.trim()}
                onClick={save}
              >
                {selected ? "Save Board changes" : "Save this Board"}
              </button>
              {selected && (
                <button
                  type="button"
                  disabled={locked}
                  onClick={() => newLesson(lessonId)}
                >
                  Start a new Board
                </button>
              )}
            </div>
            <div className={styles.recentHeading}>
              <h2>Recent Boards</h2>
              <label>
                Show course
                <select
                  aria-label="Filter saved Boards by course"
                  value={filter}
                  disabled={locked}
                  onChange={(event) => setFilter(event.target.value)}
                >
                  <option value="all">All courses</option>
                  <option value="none">Independent practice</option>
                  {Array.from(
                    new Set(
                      collection?.boards
                        .map((item) => item.course_id)
                        .filter((id): id is string => Boolean(id)),
                    ),
                  ).map((id) => (
                    <option key={id} value={id}>
                      {courseName(id)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {recent.length === 0 && (
              <p>
                No saved Boards in this view yet. Save a lesson to come back to
                your place.
              </p>
            )}
            <ul className={styles.recentBoards}>
              {recent.map((item) => (
                <li key={item.id}>
                  <div>
                    <strong>{item.title}</strong>
                    <span className={styles.recentMetadata}>
                      {courseName(item.course_id)} · Step {item.step_index + 1}
                      {item.complete ? " · Completed" : ""}
                    </span>
                  </div>
                  <button
                    type="button"
                    disabled={locked}
                    onClick={() => openBoard(item.id)}
                  >
                    Reopen {item.title}
                  </button>
                  {confirmErase === item.id ? (
                    <>
                      <button
                        type="button"
                        disabled={locked}
                        onClick={() =>
                          mutate({
                            type: "erase",
                            id: item.id,
                            input: {
                              request_id: crypto.randomUUID(),
                              revision: item.revision,
                            },
                          })
                        }
                      >
                        Permanently erase {item.title}
                      </button>
                      <button
                        type="button"
                        disabled={locked}
                        onClick={() => setConfirmErase(null)}
                      >
                        Keep saved Board
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      disabled={locked}
                      onClick={() => setConfirmErase(item.id)}
                    >
                      Erase {item.title}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}
      <div hidden={storageEnabled && masked}>
        <div className={styles.lessonPicker}>
          <label htmlFor="whiteboard-lesson">What are we working on?</label>
          <select
            id="whiteboard-lesson"
            value={lesson.id}
            disabled={locked}
            onChange={(event) => newLesson(event.target.value)}
          >
            {whiteboardLessons.map((item) => (
              <option key={item.id} value={item.id}>
                {item.subject} · {item.title}
              </option>
            ))}
          </select>
          <p>
            Five short practice walkthroughs, ready to explore. Choosing a
            lesson starts a new Board; save your current place first.
          </p>
        </div>
        <fieldset className={styles.playerFrame} disabled={locked}>
          <legend className={styles.srOnly}>Current lesson</legend>
          <LessonPlayer
            key={`${lesson.id}:${instance}`}
            lesson={lesson}
            initialSnapshot={restored}
            capture={capture}
            suspended={masked || locked}
            storageEnabled={storageEnabled}
          />
        </fieldset>
      </div>
    </section>
  );
}

function LessonPlayer({
  lesson,
  initialSnapshot,
  capture,
  suspended,
  storageEnabled,
}: {
  lesson: Lesson;
  initialSnapshot?: SavedBoardSnapshot;
  capture: (get: (title: string) => SavedBoardSnapshot) => () => void;
  suspended: boolean;
  storageEnabled: boolean;
}) {
  const scene = useMemo(() => layoutLesson(lesson), [lesson]);
  const [playback, dispatch] = useReducer(
    (
      state: ReturnType<typeof initialPlayback>,
      event: Parameters<typeof advancePlayback>[2],
    ) => advancePlayback(lesson, state, event),
    initialSnapshot,
    (saved) => (saved ? restoredBoardPlayback(saved) : initialPlayback()),
  );
  const [speed, setSpeed] = useState(1);
  const [motionReset, setMotionReset] = useState(0);
  const commitReveal = useCallback(() => dispatch({ type: "tick" }), []);
  const { reveal, cancel: cancelReveal } = useWhiteboardMotion({
    lesson,
    playback,
    speed,
    suspended,
    onTick: commitReveal,
    resetKey: motionReset,
  });
  const [zoom, setZoom] = useState(1);
  const [tool, setTool] = useState<"pan" | "pen">("pan");
  const [strokes, setStrokes] = useState<Stroke[]>(
    initialSnapshot?.strokes ?? [],
  );
  const [marked, setMarked] = useState<ReadonlySet<string>>(
    new Set(initialSnapshot?.marked),
  );
  const viewport = useRef<HTMLElement>(null);
  const paper = useRef<HTMLDivElement>(null);
  const gesture = useRef<{
    pointerId: number;
    x: number;
    y: number;
    left: number;
    top: number;
  } | null>(null);
  const drawing = useRef<{ strokeId: number; pointerId: number } | null>(null);
  const totalPoints = strokes.reduce(
    (count, stroke) => count + stroke.points.length,
    0,
  );
  const step = lesson.steps[playback.stepIndex];
  const waiting = needsAnswer(lesson, playback);
  const visible = useMemo(
    () =>
      visibleBoardActions(lesson, {
        stepIndex: playback.stepIndex,
        revealed: playback.revealed,
      }),
    [lesson, playback.stepIndex, playback.revealed],
  );
  const selectedAnswer = playback.answers[step.id];
  const isRunning = playback.playing && !waiting;
  // Student ink stays unchanged while a lesson element reveals. Avoid rebuilding
  // up to 20,000 annotation coordinates on each presentation frame.
  const annotations = useMemo(
    () =>
      strokes.map((stroke) => (
        <path
          key={stroke.id}
          d={stroke.points
            .map(
              (point, index) =>
                `${index === 0 ? "M" : "L"}${point.x.toFixed(1)},${point.y.toFixed(1)}`,
            )
            .join(" ")}
          fill="none"
          stroke="#a35d24"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )),
    [strokes],
  );
  const board = useMemo(
    () => (
      <WhiteboardScene
        scene={scene}
        stepIndex={playback.stepIndex}
        visibleActionIds={visible}
        reveal={reveal ?? undefined}
      />
    ),
    [scene, playback.stepIndex, visible, reveal],
  );

  useEffect(
    () =>
      capture((title) =>
        readSavedBoardSnapshot({
          ...snapshotForLesson(lesson, playback, marked, strokes),
          title,
        }),
      ),
    [capture, lesson, playback, marked, strokes],
  );

  useEffect(() => {
    if (!suspended) return;
    drawing.current = null;
    gesture.current = null;
    dispatch({ type: "pause" });
  }, [suspended]);

  useEffect(() => {
    if (window.innerWidth < 580) setZoom(2.5);
  }, []);

  useEffect(() => {
    const pauseAway = () => {
      if (document.visibilityState !== "visible") dispatch({ type: "pause" });
    };
    document.addEventListener("visibilitychange", pauseAway);
    return () => document.removeEventListener("visibilitychange", pauseAway);
  }, []);

  useEffect(() => {
    const panel = viewport.current;
    const sheet = paper.current;
    const active = scene.items
      .filter(
        (item) =>
          item.stepIndex === playback.stepIndex &&
          (visible.has(item.action.id) || item.action.id === reveal?.actionId),
      )
      .at(-1);
    if (!panel || !sheet || !active || tool !== "pan") return;
    const scale = sheet.clientWidth / scene.width;
    const top = active.y * scale;
    const bottom = (active.y + active.height) * scale;
    const left = active.x * scale;
    const right = (active.x + active.width) * scale;
    if (left < panel.scrollLeft || right > panel.scrollLeft + panel.clientWidth)
      panel.scrollLeft = Math.max(0, left - 24);
    if (top < panel.scrollTop || bottom > panel.scrollTop + panel.clientHeight)
      panel.scrollTop = Math.max(0, top - 48);
  }, [playback.stepIndex, reveal?.actionId, scene, tool, visible]);

  function resetMotion() {
    cancelReveal();
    setMotionReset((value) => value + 1);
  }

  function changeStep(index: number) {
    drawing.current = null;
    resetMotion();
    dispatch({ type: "jump", index });
  }

  function panStart(event: ReactPointerEvent<HTMLElement>) {
    if (suspended || tool !== "pan" || event.button !== 0 || gesture.current)
      return;
    if (
      event.target instanceof Element &&
      event.target.closest("button, select, input, a")
    )
      return;
    dispatch({ type: "pause" });
    if (event.pointerType === "touch") return;
    const panel = event.currentTarget;
    gesture.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      left: panel.scrollLeft,
      top: panel.scrollTop,
    };
    panel.setPointerCapture(event.pointerId);
  }

  function panMove(event: ReactPointerEvent<HTMLElement>) {
    const start = gesture.current;
    if (suspended || !start || start.pointerId !== event.pointerId) return;
    event.currentTarget.scrollLeft = start.left - (event.clientX - start.x);
    event.currentTarget.scrollTop = start.top - (event.clientY - start.y);
  }

  function panEnd(event: ReactPointerEvent<HTMLElement>) {
    if (gesture.current?.pointerId === event.pointerId) gesture.current = null;
  }

  function point(event: ReactPointerEvent<SVGSVGElement>): Point | null {
    const rect = event.currentTarget.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: Math.max(
        0,
        Math.min(
          scene.width,
          ((event.clientX - rect.left) / rect.width) * scene.width,
        ),
      ),
      y: Math.max(
        0,
        Math.min(
          scene.height,
          ((event.clientY - rect.top) / rect.height) * scene.height,
        ),
      ),
    };
  }

  function beginStroke(event: ReactPointerEvent<SVGSVGElement>) {
    if (
      suspended ||
      tool !== "pen" ||
      event.button !== 0 ||
      strokes.length >= 80 ||
      totalPoints > 19998 ||
      drawing.current
    )
      return;
    const start = point(event);
    if (!start) return;
    dispatch({ type: "pause" });
    const usedIds = new Set(strokes.map((stroke) => stroke.id));
    let id = 1;
    while (usedIds.has(id)) ++id;
    drawing.current = { strokeId: id, pointerId: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
    setStrokes((previous) => [...previous, { id, points: [start, start] }]);
  }

  function moveStroke(event: ReactPointerEvent<SVGSVGElement>) {
    if (
      suspended ||
      drawing.current === null ||
      drawing.current.pointerId !== event.pointerId ||
      tool !== "pen"
    )
      return;
    const next = point(event);
    if (!next) return;
    const id = drawing.current.strokeId;
    setStrokes((previous) => {
      if (
        previous.reduce((count, stroke) => count + stroke.points.length, 0) >=
        20000
      )
        return previous;
      return previous.map((stroke) =>
        stroke.id === id && stroke.points.length < 1500
          ? { ...stroke, points: [...stroke.points, next] }
          : stroke,
      );
    });
  }

  function endStroke(event: ReactPointerEvent<SVGSVGElement>) {
    if (drawing.current?.pointerId === event.pointerId) drawing.current = null;
  }

  return (
    <div className={styles.player}>
      <section
        className={styles.boardColumn}
        aria-label="Lesson board and controls"
      >
        <header className={styles.lessonHeader}>
          <div>
            <p className={styles.eyebrow}>
              {lesson.subject} / {lesson.topic}
            </p>
            <h2>{lesson.title}</h2>
            <p>{lesson.description}</p>
          </div>
          <span>~{lesson.minutes} min with practice</span>
        </header>
        <fieldset className={styles.toolbar}>
          <legend className={styles.srOnly}>Board controls</legend>
          <button
            type="button"
            className={styles.primary}
            disabled={waiting || playback.complete}
            onClick={() => dispatch({ type: isRunning ? "pause" : "play" })}
          >
            {isRunning
              ? "Pause lesson"
              : playback.complete
                ? "Lesson complete"
                : "Play lesson"}
          </button>
          <button
            type="button"
            onClick={() => {
              resetMotion();
              dispatch({ type: "replay" });
            }}
          >
            Replay step
          </button>
          <label className={styles.speed}>
            Pace
            <select
              value={speed}
              onChange={(event) => setSpeed(Number(event.target.value))}
            >
              <option value={0.65}>Slower</option>
              <option value={1}>Normal</option>
              <option value={1.5}>Faster</option>
            </select>
          </label>
          <div className={styles.toolGroup}>
            <button
              type="button"
              aria-pressed={tool === "pan"}
              onClick={() => {
                setTool("pan");
                drawing.current = null;
              }}
            >
              Pan
            </button>
            <button
              type="button"
              aria-pressed={tool === "pen"}
              onClick={() => {
                setTool("pen");
                dispatch({ type: "pause" });
              }}
            >
              Pen
            </button>
          </div>
          <div className={styles.zoom}>
            <button
              type="button"
              aria-label="Zoom out"
              disabled={zoom <= 1}
              onClick={() => setZoom((value) => Math.max(1, value - 0.25))}
            >
              −
            </button>
            <output aria-label="Board zoom">{Math.round(zoom * 100)}%</output>
            <button
              type="button"
              aria-label="Zoom in"
              disabled={zoom >= 2.5}
              onClick={() => setZoom((value) => Math.min(2.5, value + 0.25))}
            >
              +
            </button>
            <button
              type="button"
              onClick={() => {
                setZoom(1);
                if (viewport.current) {
                  viewport.current.scrollLeft = 0;
                  viewport.current.scrollTop = 0;
                }
              }}
            >
              Fit board
            </button>
          </div>
        </fieldset>
        <section
          ref={viewport}
          className={styles.viewport}
          data-tool={tool}
          onPointerDown={panStart}
          onPointerMove={panMove}
          onWheel={() => dispatch({ type: "pause" })}
          onKeyDown={(event) => {
            if (
              [
                "ArrowUp",
                "ArrowDown",
                "ArrowLeft",
                "ArrowRight",
                "PageUp",
                "PageDown",
                "Home",
                "End",
              ].includes(event.key)
            )
              dispatch({ type: "pause" });
          }}
          onPointerUp={panEnd}
          onPointerCancel={panEnd}
          onLostPointerCapture={panEnd}
          // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users need to focus and scroll the board.
          tabIndex={0}
          aria-label="Board. Scroll to pan; use zoom controls to enlarge."
        >
          <div
            ref={paper}
            className={styles.paper}
            style={{ width: `${zoom * 100}%` }}
          >
            {board}
            <svg
              className={styles.ink}
              viewBox={`0 0 ${scene.width} ${scene.height}`}
              role="img"
              aria-label="Your pen annotations"
              data-pen={tool === "pen"}
              onPointerDown={beginStroke}
              onPointerMove={moveStroke}
              onPointerUp={endStroke}
              onPointerCancel={endStroke}
              onLostPointerCapture={endStroke}
            >
              <title>Your drawings for this lesson</title>
              {annotations}
            </svg>
            {visible.size === 0 && reveal === null && (
              <div className={styles.emptyBoard}>
                <span aria-hidden="true">✳</span>
                <p>Room for the idea to unfold.</p>
                <button
                  type="button"
                  onClick={() => dispatch({ type: "play" })}
                >
                  Start this lesson
                </button>
              </div>
            )}
          </div>
        </section>
        <div className={styles.boardFooter}>
          <p>
            {strokes.length >= 80 || totalPoints >= 20000
              ? "Drawing limit reached (80 strokes or 20,000 points). Undo or clear a stroke to keep drawing."
              : strokes.at(-1)?.points.length === 1500
                ? "This stroke reached its 1,500-point limit. Start another stroke, or undo it."
                : storageEnabled
                  ? "Save keeps your drawings and completed writing. Unfinished lesson writing restarts when reopened."
                  : "Drawings stay in this lesson and clear when you leave."}
          </p>
          <button
            type="button"
            disabled={!strokes.length}
            onClick={() => setStrokes((previous) => previous.slice(0, -1))}
          >
            Undo stroke
          </button>
          <button
            type="button"
            disabled={!strokes.length}
            onClick={() => {
              drawing.current = null;
              setStrokes([]);
            }}
          >
            Clear drawings
          </button>
        </div>
      </section>
      <aside
        className={styles.lessonAside}
        aria-label="Lesson steps and quick checks"
      >
        <div
          className={styles.stepHeading}
          aria-live="polite"
          aria-atomic="true"
        >
          <span className={styles.stepNumber}>
            {String(playback.stepIndex + 1).padStart(2, "0")}
          </span>
          <p>
            Step {playback.stepIndex + 1} of {lesson.steps.length}
          </p>
          <h3>{step.title}</h3>
        </div>
        <p className={styles.narration}>{stripCues(step.say)}</p>
        <nav aria-label="Lesson timeline" className={styles.timeline}>
          {lesson.steps.map((item, index) => (
            <button
              type="button"
              key={item.id}
              aria-current={index === playback.stepIndex ? "step" : undefined}
              onClick={() => changeStep(index)}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              {item.title}
              {marked.has(item.id) && (
                <span className={styles.marked}>Revisit</span>
              )}
            </button>
          ))}
        </nav>
        {step.check && playback.revealed === step.actions.length && (
          <section className={styles.check} aria-label="Quick check">
            <p className={styles.eyebrow}>Your turn</p>
            <h3>{step.check.question}</h3>
            <div className={styles.choices}>
              {step.check.choices.map((choice, index) => (
                <button
                  type="button"
                  key={choice}
                  aria-pressed={selectedAnswer === index}
                  onClick={() => dispatch({ type: "answer", choice: index })}
                >
                  {choice}
                </button>
              ))}
            </div>
            {selectedAnswer !== undefined && (
              <p role="status" className={styles.feedback}>
                <strong>
                  {selectedAnswer === step.check.answer
                    ? "That’s it. "
                    : "Let’s look at that. "}
                </strong>
                {step.check.explanation}
              </p>
            )}
          </section>
        )}
        {waiting && (
          <p className={styles.waiting} role="status">
            Take your time. Choose an answer when you’re ready.
          </p>
        )}
        {playback.complete && (
          <p className={styles.finished} role="status">
            Walkthrough complete. Revisit any step, or try another lesson.
          </p>
        )}
        <div className={styles.stepActions}>
          <button
            type="button"
            disabled={playback.stepIndex === 0}
            onClick={() => changeStep(playback.stepIndex - 1)}
          >
            Previous step
          </button>
          <button
            type="button"
            disabled={playback.stepIndex === lesson.steps.length - 1}
            onClick={() => changeStep(playback.stepIndex + 1)}
          >
            Next step
          </button>
        </div>
        <button
          type="button"
          className={styles.markStep}
          aria-pressed={marked.has(step.id)}
          onClick={() =>
            setMarked((previous) => {
              const next = new Set(previous);
              if (next.has(step.id)) next.delete(step.id);
              else next.add(step.id);
              return next;
            })
          }
        >
          {marked.has(step.id)
            ? "Marked for another look"
            : "Mark this step for another look"}
        </button>
        <button
          type="button"
          className={styles.restart}
          onClick={() => {
            drawing.current = null;
            resetMotion();
            dispatch({ type: "restart" });
            setStrokes([]);
            setMarked(new Set());
          }}
        >
          Restart lesson
        </button>
      </aside>
    </div>
  );
}
