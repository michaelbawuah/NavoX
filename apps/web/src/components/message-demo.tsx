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
        <span data-lit="true">Question</span>
        <i />
        <span data-lit={step !== "message"}>Timetable</span>
        <i />
        <span data-lit={step === "task"}>Next class</span>
      </div>
      <div className={styles.demoContent} aria-live="polite" aria-atomic="true">
        {step === "message" ? (
          <>
            <span className={styles.demoMeta}>SAMPLE MONDAY · 9:40 AM</span>
            <p>
              “What’s my <mark>next class</mark>, and where do I go?”
            </p>
          </>
        ) : step === "connecting" ? (
          <>
            <span className={styles.demoMeta}>CONFIRMED SAMPLE TIMETABLE</span>
            <p>
              CS101 · Monday 10:00–10:50 AM
              <br />
              Science Hall 204
            </p>
          </>
        ) : (
          <>
            <span className={styles.demoMeta}>FROM THE SAMPLE TIMETABLE</span>
            <h3>Next class: CS101 lecture</h3>
            <div className={styles.taskFacts}>
              <span>
                Sample time <b>Monday · 10:00 AM</b>
              </span>
              <span>
                Room <b>Science Hall 204</b>
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
              Ask again
            </button>
            <button type="button" onClick={onOpenTask}>
              View sample class <span>↗</span>
            </button>
          </>
        ) : (
          <>
            <span>
              {step === "connecting"
                ? "Reading the sample timetable…"
                : "A question. A saved timetable."}
            </span>
            <button
              type="button"
              disabled={step === "connecting"}
              onClick={demonstrate}
            >
              {step === "connecting" ? "Reading…" : "See next class"}
              <span>{step === "connecting" ? "· · ·" : "↗"}</span>
            </button>
          </>
        )}
      </div>
    </div>
  );
}
