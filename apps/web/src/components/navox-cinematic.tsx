"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";
import { ImmersiveScene } from "./immersive-scene";
import { MessageDemo } from "./message-demo";
import styles from "./navox-cinematic.module.css";

const chapters = [
  "The beginning",
  "Connect the context",
  "Find your focus",
  "Make your move",
];
const tasks = {
  Today: [
    {
      icon: "↗",
      title: "Send feedback on the project brief",
      meta: "Due Oct 2, 3:00 PM EDT · Alex · Email",
      detail:
        "Alex asked: “Could you send feedback on the project brief by Oct 2, 3:00 PM EDT?” The task keeps that exact deadline and the original conversation attached. Review the brief and decide what to send.",
    },
    {
      icon: "◷",
      title: "Prepare for the design review",
      meta: "Meeting · Calendar",
      detail:
        "Review the agenda and gather your questions about the project brief. The related conversation and your notes stay together.",
    },
    {
      icon: "✓",
      title: "Finish the presentation outline",
      meta: "Your commitment · Task",
      detail:
        "Turn your project feedback into an outline. Keep the source context alongside your next step.",
    },
  ],
  Upcoming: [
    {
      icon: "◷",
      title: "Project check-in",
      meta: "Tomorrow · Calendar",
      detail:
        "See what’s coming next, with time to prepare before the day begins.",
    },
    {
      icon: "✓",
      title: "Share the updated presentation",
      meta: "Friday · Your commitment",
      detail: "Keep the deadline and the promise that created it in one place.",
    },
  ],
  Waiting: [
    {
      icon: "↻",
      title: "Feedback on the revised outline",
      meta: "Waiting on a reply · Email",
      detail:
        "Keep track of the conversation. Review the original thread before following up.",
    },
  ],
};
type View = keyof typeof tasks;
type Panel = "workspace" | "search" | "account" | "about";
const allTasks = Object.entries(tasks).flatMap(([group, items]) =>
  items.map((item) => ({ ...item, group: group as View })),
);
const clamp = (x: number) => Math.max(0, Math.min(1, x));
function chapterOpacity(progress: number, index: number) {
  return clamp(1 - Math.max(0, Math.abs(progress - index) - 0.12) / 0.32);
}

export function NavoXCinematic({ children }: { children: ReactNode }) {
  const journey = useRef<HTMLElement>(null),
    dialog = useRef<HTMLDialogElement>(null),
    input = useRef<HTMLInputElement>(null),
    previousFocus = useRef<HTMLElement | null>(null);
  const [progress, setProgress] = useState(0),
    [paused, setPaused] = useState(false),
    [reduced, setReduced] = useState(false),
    [mode, setMode] = useState(0),
    [panel, setPanel] = useState<Panel>("workspace"),
    [view, setView] = useState<View>("Today"),
    [expanded, setExpanded] = useState<string | null>(null),
    [query, setQuery] = useState(""),
    [panelOpen, setPanelOpen] = useState(false);
  const current = Math.min(3, Math.round(progress));
  const results = allTasks.filter((t) =>
    `${t.title} ${t.meta}`.toLowerCase().includes(query.toLowerCase().trim()),
  );
  const openPanel = (next: Panel) => {
    previousFocus.current = document.activeElement as HTMLElement;
    setPanel(next);
    setPanelOpen(true);
    setQuery("");
    dialog.current?.showModal();
    if (next === "search") setTimeout(() => input.current?.focus(), 0);
  };
  const closePanel = () => {
    dialog.current?.close();
    setPanelOpen(false);
    previousFocus.current?.focus({ preventScroll: true });
  };
  const go = (index: number) => {
    const el = journey.current;
    if (!el) return;
    const available = el.offsetHeight - innerHeight;
    window.scrollTo({
      top: el.offsetTop + (available * index) / 3,
      behavior: reduced ? "instant" : "smooth",
    });
  };
  useEffect(() => {
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduced(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      const el = journey.current;
      if (!el) return;
      setProgress(
        clamp(
          -el.getBoundingClientRect().top /
            Math.max(1, el.offsetHeight - innerHeight),
        ) * 3,
      );
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    addEventListener("scroll", schedule, { passive: true });
    addEventListener("resize", schedule);
    update();
    return () => {
      cancelAnimationFrame(frame);
      removeEventListener("scroll", schedule);
      removeEventListener("resize", schedule);
    };
  }, []);
  useEffect(() => {
    if (panel === "search" && dialog.current?.open) input.current?.focus();
  }, [panel]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        previousFocus.current = document.activeElement as HTMLElement;
        setPanel("search");
        setPanelOpen(true);
        setQuery("");
        dialog.current?.showModal();
        setTimeout(() => input.current?.focus(), 0);
      }
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, []);
  const pick = (task: (typeof allTasks)[number]) => {
    setView(task.group);
    setExpanded(task.title);
    setPanel("workspace");
  };
  return (
    <main className={styles.page} data-paused={paused} data-reduced={reduced}>
      <a className={styles.skip} href="#story-heading">
        Skip to introduction
      </a>
      <section
        className={styles.journey}
        ref={journey}
        aria-label="Explore NavoX"
      >
        <div className={styles.stage}>
          <ImmersiveScene
            paused={paused || reduced || panelOpen}
            mode={mode}
            progress={progress}
          />
          <div className={styles.scrim} aria-hidden="true" />
          <header className={styles.header}>
            <button
              type="button"
              className={styles.brand}
              onClick={() => go(0)}
              aria-label="NavoX home"
            >
              NavoX<span>✳</span>
            </button>
            <nav aria-label="Main navigation">
              <button type="button" onClick={() => go(1)}>
                The idea
              </button>
              <button type="button" onClick={() => openPanel("workspace")}>
                Your space
              </button>
            </nav>
            <div className={styles.headerActions}>
              <button
                type="button"
                className={styles.searchTrigger}
                onClick={() => openPanel("search")}
                aria-label="Search example workspace"
              >
                ⌘ K
              </button>
              <button
                type="button"
                className={styles.enter}
                onClick={() => openPanel("account")}
              >
                Enter NavoX <span>↗</span>
              </button>
            </div>
          </header>
          <div className={styles.topNote} aria-hidden="true">
            <span>PERSONAL INTELLIGENCE</span>
            <span>IN YOUR ORBIT.</span>
          </div>
          <div className={styles.story}>
            {chapters.map((title, index) => {
              const opacity = chapterOpacity(progress, index);
              const active = current === index;
              return (
                <section
                  key={title}
                  className={`${styles.chapter} ${styles[`chapter${index}`]}`}
                  data-active={active}
                  aria-hidden={!active}
                  inert={!active}
                  style={{
                    opacity,
                    transform: `translateY(${reduced ? 0 : (index - progress) * 55}px)`,
                  }}
                >
                  <p className={styles.eyebrow}>
                    0{index + 1} /{" "}
                    {
                      [
                        "A CLEARER PERSPECTIVE",
                        "EVERYTHING, CONNECTED",
                        "ATTENTION, WITH INTENTION",
                        "YOUR NEXT CHAPTER",
                      ][index]
                    }
                  </p>
                  {index === 0 && (
                    <>
                      <h1 id="story-heading" tabIndex={-1}>
                        NavoX
                      </h1>
                      <h2>
                        Your day.
                        <br />
                        <em>Reimagined.</em>
                      </h2>
                      <p className={styles.body}>
                        Messages, meetings, and commitments.
                        <br />
                        One place to see your next move.
                      </p>
                      <button
                        type="button"
                        className={styles.textLink}
                        onClick={() => go(1)}
                      >
                        Step into your orbit <span>↓</span>
                      </button>
                    </>
                  )}
                  {index === 1 && (
                    <>
                      <h2>
                        Everything.
                        <br />
                        <em>Connected.</em>
                      </h2>
                      <p className={styles.body}>
                        The message. The meeting. The promise.
                        <br />
                        See how they fit together.
                      </p>
                      <div className={styles.contextList}>
                        <span>
                          01 <b>Messages</b>
                        </span>
                        <span>
                          02 <b>Meetings</b>
                        </span>
                        <span>
                          03 <b>Commitments</b>
                        </span>
                      </div>
                      <button
                        type="button"
                        className={styles.textLink}
                        onClick={() => openPanel("workspace")}
                      >
                        Explore the connections <span>↗</span>
                      </button>
                    </>
                  )}
                  {index === 2 && (
                    <>
                      <h2>
                        Less noise.
                        <br />
                        <em>More focus.</em>
                      </h2>
                      <p className={styles.body}>
                        Bring the work that needs you into view.
                        <br />
                        Keep the context close.
                      </p>
                      <MessageDemo
                        active={active}
                        still={paused || reduced}
                        onOpenTask={() => {
                          setView("Today");
                          setExpanded(tasks.Today[0].title);
                          openPanel("workspace");
                        }}
                      />
                    </>
                  )}
                  {index === 3 && (
                    <>
                      <h2>
                        Your day.
                        <br />
                        <em>Your way.</em>
                      </h2>
                      <p className={styles.body}>
                        Understand the suggestion. Shape the details.
                        <br />
                        Make the final call.
                      </p>
                      <button
                        type="button"
                        className={styles.primary}
                        onClick={() => openPanel("account")}
                      >
                        Enter NavoX <span>↗</span>
                      </button>
                      <button
                        type="button"
                        className={styles.textLink}
                        onClick={() => openPanel("workspace")}
                      >
                        Explore the example workspace <span>↗</span>
                      </button>
                    </>
                  )}
                </section>
              );
            })}
          </div>
          <div className={styles.sceneCaption} aria-hidden="true">
            <span>THE NavoX FIELD</span>
            <span>
              0{current + 1} —{" "}
              {["PERSPECTIVE", "CONNECTION", "CLARITY", "POSSIBILITY"][current]}
            </span>
          </div>
          <div className={styles.bottomBar}>
            <nav className={styles.chapterNav} aria-label="Story chapters">
              {chapters.map((title, index) => (
                <button
                  type="button"
                  key={title}
                  aria-label={title}
                  aria-current={current === index ? "step" : undefined}
                  onClick={() => go(index)}
                >
                  <span>0{index + 1}</span>
                  <i />
                </button>
              ))}
            </nav>
            <fieldset className={styles.modes} aria-label="Scene mood">
              {["Flow", "Focus", "Orbit"].map((name, index) => (
                <button
                  type="button"
                  key={name}
                  aria-pressed={mode === index}
                  onClick={() => setMode(index)}
                >
                  {name}
                </button>
              ))}
            </fieldset>
            <div className={styles.utilities}>
              <button type="button" onClick={() => openPanel("about")}>
                About
              </button>
              <button
                type="button"
                aria-pressed={paused}
                onClick={() => setPaused(!paused)}
              >
                {paused ? "Play motion" : "Pause motion"}{" "}
                <span aria-hidden="true">{paused ? "▷" : "Ⅱ"}</span>
              </button>
            </div>
          </div>
          <div className={styles.progress} aria-hidden="true">
            <i style={{ transform: `scaleX(${progress / 3})` }} />
          </div>
        </div>
      </section>
      <dialog
        ref={dialog}
        className={styles.dialog}
        aria-labelledby={panel === "account" ? "auth-heading" : "panel-title"}
        onCancel={() => {
          setPanelOpen(false);
          previousFocus.current?.focus({ preventScroll: true });
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            closePanel();
          }
        }}
      >
        <div className={styles.dialogTop}>
          <span className={styles.brand}>
            NavoX<span>✳</span>
          </span>
          <button type="button" onClick={closePanel} aria-label="Close panel">
            Close <span>×</span>
          </button>
        </div>
        <div className={styles.dialogBody}>
          {panel === "workspace" && (
            <>
              <p className={styles.eyebrow}>EXAMPLE WORKSPACE</p>
              <h2 id="panel-title">Your next move.</h2>
              <p className={styles.panelIntro}>A little clarity for today.</p>
              <fieldset className={styles.tabs} aria-label="Task view">
                {(Object.keys(tasks) as View[]).map((tab) => (
                  <button
                    type="button"
                    key={tab}
                    aria-pressed={view === tab}
                    onClick={() => {
                      setView(tab);
                      setExpanded(null);
                    }}
                  >
                    {tab}
                    <span>{tasks[tab].length}</span>
                  </button>
                ))}
              </fieldset>
              <div className={styles.tasks}>
                {tasks[view].map((task) => (
                  <article key={task.title}>
                    <button
                      type="button"
                      aria-expanded={expanded === task.title}
                      onClick={() =>
                        setExpanded(expanded === task.title ? null : task.title)
                      }
                    >
                      <span>{task.icon}</span>
                      <span>
                        <strong>{task.title}</strong>
                        <small>{task.meta}</small>
                      </span>
                      <b>{expanded === task.title ? "−" : "+"}</b>
                    </button>
                    {expanded === task.title && <p>{task.detail}</p>}
                  </article>
                ))}
              </div>
              <button
                type="button"
                className={styles.textLink}
                onClick={() => setPanel("search")}
              >
                Find something in your orbit <kbd>⌘ K</kbd>
              </button>
            </>
          )}
          {panel === "search" && (
            <>
              <p className={styles.eyebrow}>EXAMPLE WORKSPACE</p>
              <h2 id="panel-title">Find your signal.</h2>
              <label className={styles.searchLabel}>
                <span className="visually-hidden">Search example tasks</span>
                <input
                  ref={input}
                  type="search"
                  value={query}
                  placeholder="Search messages, meetings, tasks…"
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && results.length) {
                      e.preventDefault();
                      pick(results[0]);
                    }
                  }}
                />
              </label>
              <div className={styles.searchResults}>
                {results.length ? (
                  results.map((task) => (
                    <button
                      type="button"
                      key={task.title}
                      onClick={() => pick(task)}
                    >
                      <span>{task.icon}</span>
                      <span>
                        <strong>{task.title}</strong>
                        <small>
                          {task.group} · {task.meta}
                        </small>
                      </span>
                      <b>↗</b>
                    </button>
                  ))
                ) : (
                  <p>No matching examples. Try “feedback” or “review”.</p>
                )}
              </div>
            </>
          )}
          {panel === "account" && (
            <div className={styles.account}>{children}</div>
          )}
          {panel === "about" && (
            <>
              <p className={styles.eyebrow}>A FEW THINGS, EXPLAINED</p>
              <h2 id="panel-title">Good to know.</h2>
              <div className={styles.faq}>
                {[
                  [
                    "What is NavoX?",
                    "NavoX brings messages, meetings, and commitments into a personal operations workspace, so you can see what matters and decide what to do next.",
                  ],
                  [
                    "How do suggested actions work?",
                    "Review the context, edit the details, and approve the exact final action. An edited email draft needs fresh approval before it can be sent.",
                  ],
                  [
                    "Where do I start?",
                    "Explore the example workspace to see how your conversations and commitments come together.",
                  ],
                ].map(([q, a]) => (
                  <details key={q}>
                    <summary>
                      {q}
                      <span>+</span>
                    </summary>
                    <p>{a}</p>
                  </details>
                ))}
              </div>
              <p className={styles.copyright}>
                © {new Date().getFullYear()} NavoX
              </p>
            </>
          )}
        </div>
      </dialog>
    </main>
  );
}
