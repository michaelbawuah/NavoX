"use client";

import {
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import { whiteboardLessons } from "../lib/whiteboard-lessons";
import {
  advancePlayback,
  initialPlayback,
  needsAnswer,
  playbackDelay,
  visibleBoardActions,
} from "../lib/whiteboard-playback";
import { type Lesson, layoutLesson, stripCues } from "../lib/whiteboard-tutor";
import styles from "./student-whiteboard.module.css";
import { WhiteboardScene } from "./whiteboard-scene";
import "katex/dist/katex.min.css";

type Point = { x: number; y: number };
type Stroke = { id: number; points: Point[] };

export function StudentWhiteboard() {
  const [lessonId, setLessonId] = useState(whiteboardLessons[0].id);
  const lesson = whiteboardLessons.find((item) => item.id === lessonId);
  if (!lesson) return <p>This lesson could not be opened.</p>;
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
      <div className={styles.lessonPicker}>
        <label htmlFor="whiteboard-lesson">What are we working on?</label>
        <select
          id="whiteboard-lesson"
          value={lesson.id}
          onChange={(event) => setLessonId(event.target.value)}
        >
          {whiteboardLessons.map((item) => (
            <option key={item.id} value={item.id}>
              {item.subject} · {item.title}
            </option>
          ))}
        </select>
        <p>Five short practice walkthroughs, ready to explore.</p>
      </div>
      <LessonPlayer key={lesson.id} lesson={lesson} />
    </section>
  );
}

function LessonPlayer({ lesson }: { lesson: Lesson }) {
  const scene = useMemo(() => layoutLesson(lesson), [lesson]);
  const [playback, dispatch] = useReducer(
    (
      state: ReturnType<typeof initialPlayback>,
      event: Parameters<typeof advancePlayback>[2],
    ) => advancePlayback(lesson, state, event),
    undefined,
    initialPlayback,
  );
  const [speed, setSpeed] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [tool, setTool] = useState<"pan" | "pen">("pan");
  const [strokes, setStrokes] = useState<Stroke[]>([]);
  const [marked, setMarked] = useState<ReadonlySet<string>>(new Set());
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
  const nextStroke = useRef(0);
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
  const board = useMemo(
    () => (
      <WhiteboardScene
        scene={scene}
        stepIndex={playback.stepIndex}
        visibleActionIds={visible}
      />
    ),
    [scene, playback.stepIndex, visible],
  );

  useEffect(() => {
    if (!playback.playing) return;
    const timer = window.setTimeout(
      () => dispatch({ type: "tick" }),
      playbackDelay(lesson, playback, speed),
    );
    return () => window.clearTimeout(timer);
  }, [lesson, playback, speed]);

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
          item.stepIndex === playback.stepIndex && visible.has(item.action.id),
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
  }, [playback.stepIndex, scene, tool, visible]);

  function changeStep(index: number) {
    drawing.current = null;
    dispatch({ type: "jump", index });
  }

  function panStart(event: ReactPointerEvent<HTMLElement>) {
    if (tool !== "pan" || event.button !== 0 || gesture.current) return;
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
    if (!start || start.pointerId !== event.pointerId) return;
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
      tool !== "pen" ||
      event.button !== 0 ||
      strokes.length >= 80 ||
      drawing.current
    )
      return;
    const start = point(event);
    if (!start) return;
    dispatch({ type: "pause" });
    const id = ++nextStroke.current;
    drawing.current = { strokeId: id, pointerId: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
    setStrokes((previous) => [...previous, { id, points: [start, start] }]);
  }

  function moveStroke(event: ReactPointerEvent<SVGSVGElement>) {
    if (
      drawing.current === null ||
      drawing.current.pointerId !== event.pointerId ||
      tool !== "pen"
    )
      return;
    const next = point(event);
    if (!next) return;
    const id = drawing.current.strokeId;
    setStrokes((previous) =>
      previous.map((stroke) =>
        stroke.id === id && stroke.points.length < 1500
          ? { ...stroke, points: [...stroke.points, next] }
          : stroke,
      ),
    );
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
          <button type="button" onClick={() => dispatch({ type: "replay" })}>
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
              {strokes.map((stroke) => (
                <path
                  key={stroke.id}
                  d={stroke.points
                    .map(
                      (p, i) =>
                        `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`,
                    )
                    .join(" ")}
                  fill="none"
                  stroke="#a35d24"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              ))}
            </svg>
            {visible.size === 0 && (
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
            {strokes.length >= 80
              ? "Drawing limit reached. Undo or clear a stroke to keep drawing."
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
