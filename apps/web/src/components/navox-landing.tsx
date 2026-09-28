"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";
import { NavigationCore } from "./navigation-core";
import styles from "./navox-landing.module.css";

function Arrow({ diagonal = false }: { diagonal?: boolean }) {
  return <span aria-hidden="true">{diagonal ? "↗" : "→"}</span>;
}

function Mark() {
  return (
    <span aria-hidden="true" className={styles.mark}>
      N<span>↗</span>
    </span>
  );
}

const workflow = [
  {
    title: "Bring it together.",
    text: "Connect your inbox, calendar, and the places your work already happens.",
    label: "01 / CONNECT",
    cards: [
      [
        "EMAIL",
        "A reply worth your attention",
        "Project update · Reply requested",
      ],
      ["CALENDAR", "The conversation ahead", "Design review · Tomorrow"],
      [
        "FILES",
        "The details behind the decision",
        "Project brief · Ready to review",
      ],
    ],
  },
  {
    title: "Find the signal.",
    text: "See the requests, commitments, and deadlines that deserve your attention—with their original context.",
    label: "02 / UNDERSTAND",
    cards: [
      ["REQUEST", "Review the project brief", "Linked to the original message"],
      [
        "CONTEXT",
        "Bring your notes to the review",
        "Meeting and brief connected",
      ],
      ["DEADLINE", "Feedback before tomorrow", "A clear next step"],
    ],
  },
  {
    title: "Make your next move.",
    text: "Start with a clear priority. Review a suggested response, plan your next step, and make the final call.",
    label: "03 / MOVE FORWARD",
    cards: [
      ["PRIORITY", "Start with the project brief", "One focused next step"],
      [
        "PREPARE",
        "Your meeting, in perspective",
        "Context ready when you need it",
      ],
      [
        "REVIEW",
        "A thoughtful reply, ready to edit",
        "Your words. Your final approval.",
      ],
    ],
  },
] as const;

const previews = {
  Today: [
    {
      icon: "↗",
      title: "Send feedback on the project brief",
      meta: "Reply requested · Email",
      time: "10:00",
      kind: "REPLY",
      detail:
        "The project team asked for your feedback before tomorrow’s review. Open the original message, check the brief, and decide what to send.",
    },
    {
      icon: "◷",
      title: "Prepare for the design review",
      meta: "Meeting · Calendar",
      time: "14:30",
      kind: "PREPARE",
      detail:
        "Review the agenda and gather your questions about the project brief. NavoX keeps the meeting and its related work together.",
    },
    {
      icon: "✓",
      title: "Finish the presentation outline",
      meta: "Your commitment · Task",
      time: "16:00",
      kind: "FOCUS",
      detail:
        "Turn the project feedback into an outline for the next presentation. Keep the source context alongside your next step.",
    },
  ],
  Upcoming: [
    {
      icon: "◷",
      title: "Project check-in",
      meta: "Tomorrow · Calendar",
      time: "09:30",
      kind: "MEETING",
      detail:
        "A view of what’s coming next, with room to prepare before the day begins.",
    },
    {
      icon: "✓",
      title: "Share the updated presentation",
      meta: "Friday · Your commitment",
      time: "15:00",
      kind: "DEADLINE",
      detail: "Keep the deadline and the promise that created it in one place.",
    },
  ],
  Waiting: [
    {
      icon: "↻",
      title: "Feedback on the revised outline",
      meta: "Waiting on a reply · Email",
      time: "Pending",
      kind: "FOLLOW UP",
      detail:
        "Keep track of the conversation without losing it in your inbox. Review the original thread before following up.",
    },
  ],
};

export function NavoXLanding({
  children,
  onSignIn,
}: {
  children: ReactNode;
  onSignIn: () => void;
}) {
  const [paused, setPaused] = useState(false);
  const [step, setStep] = useState(0);
  const [view, setView] = useState<keyof typeof previews>("Today");
  const [expanded, setExpanded] = useState<string | null>(null);
  const root = useRef<HTMLElement>(null);
  const hero = useRef<HTMLElement>(null);

  useEffect(() => {
    const element = hero.current;
    if (!element) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    function update() {
      frame = 0;
      if (!element) return;
      const progress =
        paused || motion.matches
          ? 0
          : Math.min(
              1,
              Math.max(
                0,
                -element.getBoundingClientRect().top / element.offsetHeight,
              ),
            );
      element.style.setProperty("--travel", `${progress * 100}px`);
      element.style.setProperty("--turn", `${progress * 14}deg`);
    }
    function schedule() {
      if (!frame) frame = requestAnimationFrame(update);
    }
    window.addEventListener("scroll", schedule, { passive: true });
    motion.addEventListener("change", schedule);
    update();
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule);
      motion.removeEventListener("change", schedule);
    };
  }, [paused]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => {
      if (root.current)
        root.current.dataset.reducedMotion = String(media.matches);
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);

  return (
    <main ref={root} className={styles.page} data-paused={paused}>
      <a className={styles.skip} href="#overview">
        Skip to content
      </a>
      <header className={styles.header}>
        <a href="#overview" className={styles.brand} aria-label="NavoX home">
          <Mark />
          navo<span>x</span>
        </a>
        <nav aria-label="Main navigation" className={styles.nav}>
          <a href="#workflow">How it works</a>
          <a href="#workspace">The workspace</a>
          <a href="#questions">Questions</a>
        </nav>
        <button
          type="button"
          onClick={() => {
            onSignIn();
            document.getElementById("access-panel")?.scrollIntoView();
            document
              .getElementById("auth-heading")
              ?.focus({ preventScroll: true });
          }}
          className={styles.signIn}
        >
          Sign in <Arrow diagonal />
        </button>
      </header>

      <section
        ref={hero}
        id="overview"
        className={styles.hero}
        aria-labelledby="hero-heading"
      >
        <div className={styles.heroGrid} aria-hidden="true" />
        <div className={styles.heroCopy}>
          <p className={styles.kicker}>
            <span className={styles.statusDot} /> YOUR DAY. IN PERSPECTIVE.
          </p>
          <h1 id="hero-heading">
            A clearer view.
            <br />A better
            <br />
            <em>next move.</em>
          </h1>
          <p className={styles.heroDescription}>
            Bring your messages, meetings, and commitments into focus. Move
            through your day with a little more clarity.
          </p>
          <div className={styles.heroActions}>
            <a href="#access-panel" className={styles.button}>
              Find your focus <Arrow diagonal />
            </a>
            <a href="#workspace" className={styles.textLink}>
              Explore NavoX <span aria-hidden="true">↓</span>
            </a>
          </div>
        </div>
        <div className={styles.sculpture}>
          <NavigationCore paused={paused} />
          <span className={`${styles.orbitLabel} ${styles.orbitLabelOne}`}>
            01 — CONTEXT
          </span>
          <span className={`${styles.orbitLabel} ${styles.orbitLabelTwo}`}>
            02 — CLARITY
          </span>
          <span className={`${styles.orbitLabel} ${styles.orbitLabelThree}`}>
            03 — INTENTION
          </span>
          <span className={styles.crosshair} aria-hidden="true">
            +
          </span>
        </div>
        <div className={styles.heroBottom}>
          <span>LESS NOISE. MORE DIRECTION.</span>
          <a href="#workflow">
            SCROLL TO DISCOVER <span aria-hidden="true">↓</span>
          </a>
          <button
            type="button"
            aria-pressed={paused}
            onClick={() => setPaused(!paused)}
            className={styles.motionButton}
          >
            {paused ? "Resume motion" : "Pause motion"}
            <span aria-hidden="true">{paused ? "▷" : "Ⅱ"}</span>
          </button>
        </div>
        <div className={styles.wordmark} aria-hidden="true">
          NavoX<span>✳</span>
        </div>
      </section>

      <section
        id="workflow"
        className={styles.workflow}
        aria-labelledby="workflow-heading"
      >
        <div className={styles.sectionTop}>
          <span className={styles.kicker}>01 / A LITTLE LESS SCATTERED</span>
          <span className={styles.smallNote}>
            Made for the way your day unfolds.
          </span>
        </div>
        <h2 id="workflow-heading">
          Everything connected.
          <br />
          <span>One clear direction.</span>
        </h2>
        <div className={styles.workflowGrid}>
          <div className={styles.steps}>
            {workflow.map((item, index) => (
              <button
                key={item.title}
                type="button"
                aria-pressed={step === index}
                aria-controls="workflow-example"
                onClick={() => setStep(index)}
                className={step === index ? styles.activeStep : styles.step}
              >
                <span className={styles.stepNumber}>0{index + 1}</span>
                <span>
                  <strong>{item.title}</strong>
                  <span className={styles.stepDescription}>{item.text}</span>
                </span>
                <Arrow />
              </button>
            ))}
          </div>
          <div
            id="workflow-example"
            className={styles.flowIllustration}
            aria-live="polite"
          >
            <div className={styles.flowRings} aria-hidden="true">
              <i />
              <i />
              <i />
            </div>
            <div className={styles.flowHeading}>
              <Mark />
              <span>{workflow[step].label}</span>
              <span>EXAMPLE</span>
            </div>
            <div className={styles.flowCards} key={step}>
              {workflow[step].cards.map(
                ([label, title, description], index) => (
                  <div className={styles.flowCard} key={label}>
                    <span className={styles.flowIcon} aria-hidden="true">
                      {["↗", "◷", "≡"][index]}
                    </span>
                    <div>
                      <span className={styles.cardKicker}>{label}</span>
                      <strong>{title}</strong>
                      <p>{description}</p>
                    </div>
                    <span aria-hidden="true">+</span>
                  </div>
                ),
              )}
            </div>
            <p className={styles.flowFoot}>
              A clearer picture starts with the right connections.
            </p>
          </div>
        </div>
      </section>

      <section
        id="workspace"
        className={styles.workspace}
        aria-labelledby="workspace-heading"
      >
        <div className={styles.workspaceIntro}>
          <p className={styles.kicker}>02 / ROOM TO THINK</p>
          <h2 id="workspace-heading">
            Your day.
            <br />
            <em>With perspective.</em>
          </h2>
          <p>
            The important things, together. See what needs a reply, what’s
            coming up, and what you’re waiting on.
          </p>
          <a href="#access-panel" className={styles.textLink}>
            Make room for what matters <Arrow diagonal />
          </a>
          <div className={styles.workspaceIndex}>
            <span>Less searching.</span>
            <span>More moving forward.</span>
          </div>
        </div>
        <div className={styles.preview}>
          <div className={styles.previewHeader}>
            <span className={styles.previewBrand}>
              <Mark />
              NavoX
            </span>
            <span className={styles.demoBadge}>INTERACTIVE PREVIEW</span>
            <span className={styles.avatar}>J</span>
          </div>
          <div className={styles.previewBody}>
            <div className={styles.previewGreeting}>
              <div>
                <p>YOUR PERSONAL WORKSPACE</p>
                <h3>A little clarity for today.</h3>
              </div>
              <span aria-hidden="true">✳</span>
            </div>
            <fieldset
              className={styles.previewTabs}
              aria-label="Preview task view"
            >
              {(Object.keys(previews) as (keyof typeof previews)[]).map(
                (tab) => (
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
                    <span>{previews[tab].length}</span>
                  </button>
                ),
              )}
            </fieldset>
            <div className={styles.taskList} aria-live="polite">
              {previews[view].map((task) => (
                <div key={task.title} className={styles.task}>
                  <button
                    type="button"
                    className={styles.taskButton}
                    aria-expanded={expanded === task.title}
                    onClick={() =>
                      setExpanded(expanded === task.title ? null : task.title)
                    }
                  >
                    <span className={styles.taskIcon} aria-hidden="true">
                      {task.icon}
                    </span>
                    <span className={styles.taskName}>
                      <strong>{task.title}</strong>
                      <span>{task.meta}</span>
                    </span>
                    <span className={styles.taskTime}>{task.time}</span>
                    <span aria-hidden="true">
                      {expanded === task.title ? "−" : "+"}
                    </span>
                  </button>
                  {expanded === task.title && (
                    <p className={styles.taskDetail}>{task.detail}</p>
                  )}
                </div>
              ))}
            </div>
            <div className={styles.previewBottom}>
              <span>
                <span className={styles.greenDot} /> Context stays close.
              </span>
              <span>Illustrative tasks</span>
            </div>
          </div>
        </div>
      </section>

      <section className={styles.principles} aria-label="Designed around you">
        <article>
          <span>01 / ATTENTION</span>
          <h3>See the signal.</h3>
          <p>Bring action-worthy messages and commitments to the surface.</p>
        </article>
        <article>
          <span>02 / CONTEXT</span>
          <h3>Keep the whole picture.</h3>
          <p>
            Return to the original message, meeting, or document behind your
            next step.
          </p>
        </article>
        <article>
          <span>03 / AGENCY</span>
          <h3>Make the final call.</h3>
          <p>
            Review and edit a suggested action. Approve the exact details when
            you’re ready.
          </p>
        </article>
      </section>

      <section
        id="questions"
        className={styles.questions}
        aria-labelledby="questions-heading"
      >
        <div>
          <p className={styles.kicker}>03 / GOOD TO KNOW</p>
          <h2 id="questions-heading">
            A few things
            <br />
            you might wonder.
          </h2>
        </div>
        <div className={styles.answers}>
          <details>
            <summary>
              What is NavoX?<span aria-hidden="true">+</span>
            </summary>
            <p>
              NavoX brings messages, meetings, and commitments into a personal
              operations workspace so you can see what matters and decide what
              to do next.
            </p>
          </details>
          <details>
            <summary>
              Where do I start?<span aria-hidden="true">+</span>
            </summary>
            <p>
              Create an account, enter your workspace, and choose the
              connections you want to add. You can also start with a task of
              your own.
            </p>
          </details>
          <details>
            <summary>
              How do suggested actions work?<span aria-hidden="true">+</span>
            </summary>
            <p>
              You review the suggestion and its context, edit the details, and
              approve the exact final action. An edited email draft needs fresh
              approval before it can be sent.
            </p>
          </details>
          <details>
            <summary>
              Can I change what’s connected?<span aria-hidden="true">+</span>
            </summary>
            <p>
              Yes. Manage your connections and their permissions from your
              workspace, including pausing or disconnecting a source.
            </p>
          </details>
        </div>
      </section>

      <section className={styles.access} aria-label="Get started">
        <div className={styles.accessCopy}>
          <p className={styles.kicker}>YOUR NEXT CHAPTER</p>
          <h2>
            A little clarity.
            <br />
            <em>A lot of possibility.</em>
          </h2>
          <p>Make space for the work—and the life—ahead.</p>
          <a
            href="#overview"
            className={styles.accessMonogram}
            aria-label="Back to top"
          >
            N<span>↗</span>
          </a>
        </div>
        {children}
      </section>
      <footer className={styles.footer}>
        <a href="#overview" className={styles.brand}>
          <Mark />
          navox
        </a>
        <p>Your day. In perspective.</p>
        <span>© {new Date().getFullYear()} NavoX</span>
        <a href="#overview" aria-label="Back to top">
          ↑
        </a>
      </footer>
    </main>
  );
}
