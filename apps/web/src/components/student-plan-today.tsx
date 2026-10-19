"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  readStudyPlan,
  readStudyTasks,
  type StudyPlan,
  type StudyTask,
  studyMinutes,
  studyRequest,
} from "../lib/student-planning";
import {
  readStudentLookup,
  type StudentLookup,
  studentClock,
  studentDateInZone,
  studentDayLabel,
  studentRequest,
} from "../lib/student-schedule";
import styles from "./student-plan-today.module.css";

interface SavedStudyDay {
  tasks: StudyTask[];
  plan: StudyPlan;
  clock: StudentLookup;
}

function SavedStudySummary({ tasks, plan, clock }: SavedStudyDay) {
  const active = tasks.filter((task) => task.status === "active");
  const remaining = active.reduce(
    (sum, task) => sum + task.remaining_minutes,
    0,
  );
  const now = Date.parse(clock.server_now);
  const today = studentDateInZone(clock.server_now, clock.timezone);
  const outcomes = plan.blocks.filter(
    (block) =>
      block.outcome_at !== null &&
      Date.parse(block.outcome_at) <= now &&
      studentDateInZone(block.outcome_at, clock.timezone) === today,
  );
  const recorded = outcomes
    .filter(
      (block) => block.status === "done" || block.status === "took_longer",
    )
    .reduce((sum, block) => sum + block.actual_minutes, 0);
  const completed = outcomes.filter((block) => block.status === "done").length;
  const skipped = outcomes.filter((block) => block.status === "skipped").length;
  const needsReview =
    plan.needs_review ||
    plan.blocks.some((block) => block.status === "needs_review") ||
    active.some((task) => task.source_needs_review);
  const next = plan.blocks
    .filter(
      (block) =>
        block.status === "planned" &&
        Date.parse(block.end_at) > now &&
        active.some((task) => task.id === block.task_id),
    )
    .sort(
      (left, right) => Date.parse(left.start_at) - Date.parse(right.start_at),
    )[0];
  const earlier = plan.blocks.some(
    (block) => block.status === "planned" && Date.parse(block.end_at) <= now,
  );

  if (!tasks.length && !plan.blocks.length) {
    return (
      <div className={styles.empty}>
        <h3>A clear place to start.</h3>
        <p>
          Add your first task, then preview study time around your day. Your
          plan changes only when you accept it.
        </p>
        <Link className={styles.primary} href="/planner">
          Add your first task <span aria-hidden="true">→</span>
        </Link>
      </div>
    );
  }

  return (
    <>
      <dl className={styles.metrics}>
        <div>
          <dt>Active tasks</dt>
          <dd>{active.length}</dd>
        </div>
        <div>
          <dt>Work remaining</dt>
          <dd>{studyMinutes(remaining)}</dd>
        </div>
        <div>
          <dt>Progress recorded today</dt>
          <dd>{studyMinutes(recorded)}</dd>
        </div>
      </dl>
      {needsReview ? (
        <div className={styles.notice}>
          <h3>Your saved plan needs a review.</h3>
          <p>Open your plan before relying on its study times.</p>
        </div>
      ) : next ? (
        <div className={styles.nextBlock}>
          <p className={styles.label}>
            {Date.parse(next.start_at) <= now
              ? "Accepted block · scheduled now"
              : "Next accepted block"}
          </p>
          <h3>{next.title}</h3>
          <p>
            {studentDayLabel(studentDateInZone(next.start_at, clock.timezone))}
            {" · "}
            <time dateTime={next.start_at}>
              {studentClock(next.start_at, clock.timezone)}
            </time>
            –
            <time dateTime={next.end_at}>
              {studentDateInZone(next.end_at, clock.timezone) !==
                studentDateInZone(next.start_at, clock.timezone) && (
                <>
                  {studentDayLabel(
                    studentDateInZone(next.end_at, clock.timezone),
                  )}
                  {" · "}
                </>
              )}
              {studentClock(next.end_at, clock.timezone)}
            </time>
            {" · "}
            {studyMinutes(next.minutes)} planned
          </p>
          <p className={styles.muted}>Planned time is not recorded progress.</p>
        </div>
      ) : (
        <div className={styles.empty}>
          <h3>No accepted study block ahead.</h3>
          <p>
            {active.length
              ? "Your tasks are saved. Open your plan to preview and accept study time."
              : "Add a task whenever you have something new to work on."}
          </p>
        </div>
      )}
      <p className={styles.progress}>
        Actual minutes from outcomes you saved today. {completed}{" "}
        {completed === 1 ? "block marked done" : "blocks marked done"}
        {skipped > 0 && ` · ${skipped} skipped`}
        {plan.has_more_history && ". Older history may not be included"}.
      </p>
      {earlier && (
        <p className={styles.muted}>
          Earlier blocks still need a progress update. They have not been
          counted as completed.
        </p>
      )}
      <Link className={styles.primary} href="/planner">
        Open my plan <span aria-hidden="true">→</span>
      </Link>
    </>
  );
}

function StudentPlanTodayAccount() {
  const titleId = useId();
  const [data, setData] = useState<SavedStudyDay | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const generation = useRef(0);
  const load = useCallback(async () => {
    const request = ++generation.current;
    setLoading(true);
    setError(false);
    try {
      const [tasks, plan, clock] = await Promise.all([
        studyRequest("/tasks", readStudyTasks),
        studyRequest("/plan", readStudyPlan),
        studentRequest("/lookup?mode=now", readStudentLookup),
      ]);
      if (request === generation.current) setData({ tasks, plan, clock });
    } catch {
      if (request === generation.current) {
        setData(null);
        setError(true);
      }
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const visible = () => {
      if (document.visibilityState === "visible") void load();
    };
    const interval = setInterval(visible, 60_000);
    document.addEventListener("visibilitychange", visible);
    return () => {
      ++generation.current;
      clearInterval(interval);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [load]);

  return (
    <section
      className={styles.card}
      aria-labelledby={titleId}
      aria-busy={loading}
    >
      <header className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Your study plan</p>
          <h2 id={titleId}>Your work, with time to do it.</h2>
          <p>Saved tasks, accepted study time and the progress you record.</p>
        </div>
        <Link className={styles.textLink} href="/planner">
          Planner <span aria-hidden="true">↗</span>
        </Link>
      </header>
      {error ? (
        <div className={styles.error} role="alert">
          <p>Your study plan could not be checked. Refresh to try again.</p>
          <Link className={styles.textLink} href="/planner">
            Open my plan
          </Link>
        </div>
      ) : data ? (
        <SavedStudySummary {...data} />
      ) : (
        <p className={styles.muted} role="status">
          Opening your saved tasks and study plan…
        </p>
      )}
      <footer className={styles.footer}>
        <span>
          {data ? (
            <>
              {data.clock.timezone.replaceAll("_", " ")} · checked at{" "}
              <time dateTime={data.clock.server_now}>
                {studentClock(data.clock.server_now, data.clock.timezone)}
              </time>
            </>
          ) : (
            "Your plan stays yours to review and change."
          )}
        </span>
        <button type="button" disabled={loading} onClick={() => void load()}>
          {loading ? "Checking study plan…" : "Refresh study plan"}
        </button>
      </footer>
    </section>
  );
}

export function StudentPlanToday({ accountId }: { accountId: string }) {
  return <StudentPlanTodayAccount key={accountId} />;
}
