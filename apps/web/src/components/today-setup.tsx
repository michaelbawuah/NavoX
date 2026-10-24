import Link from "next/link";
import styles from "./today-home.module.css";

type Props = {
  completed: readonly boolean[];
  canTalk: boolean;
  openVox: () => void;
  onHide: () => void;
};
const steps = [
  {
    title: "Add your classes",
    help: "Type them, import your schedule file, or snap a photo of it.",
    action: "Add classes",
    href: "/classes",
    icon: "M5 3h14v18H5zM8 7h8M8 11h6",
  },
  {
    title: "Connect your calendar",
    help: "Choose an available calendar in Connections so its events can shape your plan.",
    action: "Connect",
    href: "/connections",
    icon: "M4 6h16v15H4zM8 3v6M16 3v6M4 11h16",
  },
  {
    title: "Connect your email",
    help: "Connect an available mailbox. I’ll flag emails that need a reply and help you draft them. You review and approve each send.",
    action: "Connect",
    href: "/connections",
    icon: "M3 5h18v14H3zM3 5l9 7 9-7",
  },
  {
    title: "Add one thing that’s due",
    help: "An assignment, a problem set, anything. I’ll find time for it.",
    action: "Add task",
    href: "/planner#add-task",
    icon: "M9 3H5v18h14V9M9 12l3 3 9-11",
  },
  {
    title: "Try Vox",
    help: "Ask what’s next, or tell me about your week.",
    action: "Try Vox",
    href: null,
    icon: "M5 10v4M10 5v14M15 8v8M20 11v2",
  },
];
export function TodaySetup({ completed, canTalk, openVox, onHide }: Props) {
  const done = completed.filter(Boolean).length;
  return (
    <div className={styles.setupGrid}>
      <section className={styles.setup} aria-label="Set up NavoX">
        <div className={styles.setupHeader}>
          <div>
            <h2>Get your week set up</h2>
            <small>{done} of 5 done · every step is optional</small>
          </div>
          <button
            className={styles.hideSetup}
            type="button"
            aria-label="Close setup checklist"
            onClick={onHide}
          >
            Hide for now
          </button>
        </div>
        <progress
          className={styles.setupProgress}
          aria-label="Setup progress"
          max={5}
          value={done}
        />
        <ol className={styles.setupSteps}>
          {steps.map((step, index) => (
            <li key={step.title} data-done={completed[index]}>
              <span className={styles.stepIcon} aria-hidden="true">
                {completed[index] ? (
                  "✓"
                ) : (
                  <svg
                    aria-hidden="true"
                    width="22"
                    height="22"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d={step.icon} />
                  </svg>
                )}
              </span>
              <div className={styles.stepWords}>
                <strong>{step.title}</strong>
                <p>{step.help}</p>
              </div>
              {completed[index] ? (
                <span className={styles.stepDone}>Done</span>
              ) : step.href ? (
                <Link
                  className={`nx-button ${index === 0 ? styles.setupPrimary : styles.setupSecondary}`}
                  href={step.href}
                >
                  {step.action}
                </Link>
              ) : (
                <button
                  className={`nx-button ${styles.setupSecondary}`}
                  type="button"
                  disabled={!canTalk}
                  onClick={openVox}
                >
                  {step.action}
                </button>
              )}
            </li>
          ))}
        </ol>
      </section>
      <aside className={styles.setupPreview} aria-label="Preview of Today">
        <h2>What you’ll see here</h2>
        <div className={styles.previewDrawing} aria-hidden="true">
          <div className={styles.previewNext}>
            <i />
            <span>
              <b />
              <b />
            </span>
          </div>
          <div className={styles.previewLine} />
          <div className={styles.previewLine} />
          <div className={styles.previewLine} />
        </div>
        <ul>
          <li>Your next class with a countdown and when to leave</li>
          <li>Your whole day in order, with free time found for you</li>
          <li>What’s due soon and emails that need a reply</li>
        </ul>
      </aside>
    </div>
  );
}
