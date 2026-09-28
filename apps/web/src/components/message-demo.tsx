"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./navox-cinematic.module.css";

/** A fixed, local product example. It never reads an inbox or invokes a model. */
export function MessageDemo({
  active,
  still,
  onOpenTask,
}: {
  active: boolean;
  still: boolean;
  onOpenTask: () => void;
}) {
  const [step, setStep] = useState<"message" | "connecting" | "task">(
    "message",
  );
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancel = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  useEffect(() => {
    if (step === "connecting" && (!active || still)) {
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
      setStep("task");
    }
  }, [active, still, step]);
  const demonstrate = () => {
    cancel();
    if (still) {
      setStep("task");
      return;
    }
    setStep("connecting");
    timer.current = setTimeout(() => {
      timer.current = null;
      setStep("task");
    }, 1350);
  };
  return (
    <div className={styles.demo} data-step={step}>
      <div className={styles.demoTop}>
        <span>NavoX in action</span>
        <small>EXAMPLE</small>
      </div>
      <div className={styles.demoJourney} aria-hidden="true">
        <span data-lit="true">Message</span>
        <i />
        <span data-lit={step !== "message"}>Context</span>
        <i />
        <span data-lit={step === "task"}>Task</span>
      </div>
      <div className={styles.demoContent} aria-live="polite" aria-atomic="true">
        {step === "message" ? (
          <>
            <span className={styles.demoMeta}>ALEX · PROJECT BRIEF</span>
            <p>
              “Could you send feedback on the project brief by{" "}
              <mark>Oct 2, 3:00 PM EDT</mark>?”
            </p>
          </>
        ) : step === "connecting" ? (
          <>
            <span className={styles.demoMeta}>CONNECTING THE DETAILS</span>
            <p>
              The request. The deadline.
              <br />
              The original conversation.
            </p>
          </>
        ) : (
          <>
            <span className={styles.demoMeta}>YOUR NEXT MOVE</span>
            <h3>Send feedback on the project brief</h3>
            <div className={styles.taskFacts}>
              <span>
                Due <b>Oct 2 · 3:00 PM EDT</b>
              </span>
              <span>
                Source <b>Alex · Project brief</b>
              </span>
            </div>
          </>
        )}
      </div>
      <div className={styles.demoActions}>
        {step === "task" ? (
          <>
            <button
              type="button"
              onClick={() => {
                cancel();
                setStep("message");
              }}
            >
              Read source
            </button>
            <button type="button" onClick={onOpenTask}>
              View task <span>↗</span>
            </button>
          </>
        ) : (
          <>
            <span>
              {step === "connecting"
                ? "Bringing it together…"
                : "One message. A clear next step."}
            </span>
            <button
              type="button"
              disabled={step === "connecting"}
              onClick={demonstrate}
            >
              {step === "connecting" ? "Connecting…" : "See the task"}
              <span>{step === "connecting" ? "· · ·" : "↗"}</span>
            </button>
          </>
        )}
      </div>
    </div>
  );
}
