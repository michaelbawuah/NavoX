"use client";
import { courseColor } from "@navox/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type CSSProperties, useEffect, useState } from "react";
import { productFeatures } from "../lib/product-features";
import {
  goalsRequest,
  readStudentGoalsBoard,
  type StudentGoal,
} from "../lib/student-goals";
import {
  type StudyPlan,
  type StudyTask,
  studyDeadlineChoices,
  studyMinutes,
} from "../lib/student-planning";
import {
  addStudentDays,
  type StudentTimetable,
  studentClock,
  studentDateInZone,
} from "../lib/student-schedule";
import { studentLocalTime } from "../lib/student-time";
import { dayRows } from "../lib/today-home";
import { mondayOf } from "./classes-week";
import styles from "./planner-redesign.module.css";
import { TaskCompletionReview } from "./task-completion-review";
import {
  type ProgressReceipt,
  TodayProgressReceipt,
} from "./today-progress-receipt";
import { TodayStudyActions } from "./today-study-actions";
import { useStudyOpenings } from "./use-study-openings";

export function PlannerOverview({
  plan,
  tasks,
  timetable,
  accountKey,
  disabled,
  onAdd,
  onEdit,
  onReplan,
  onRoutine,
  onReload,
}: {
  plan: StudyPlan;
  tasks: StudyTask[];
  timetable: StudentTimetable;
  accountKey: string;
  disabled: boolean;
  onAdd: () => void;
  onEdit: (task: StudyTask) => void;
  onReplan: () => void;
  onRoutine: () => void;
  onReload: () => void;
}) {
  const router = useRouter();
  const openings = useStudyOpenings(accountKey, 7, plan.revision);
  const [view, setView] = useState<"today" | "tomorrow" | "week">("week");
  const [completing, setCompleting] = useState<StudyTask | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ProgressReceipt | null>(null);
  const [goals, setGoals] = useState<StudentGoal[]>([]);
  const [now, setNow] = useState(Date.parse(timetable.server_now));
  useEffect(() => {
    const server = Date.parse(timetable.server_now),
      received = Date.now();
    setNow(server);
    const timer = setInterval(
      () => setNow(server + Date.now() - received),
      30000,
    );
    return () => clearInterval(timer);
  }, [timetable.server_now]);
  useEffect(() => {
    const abort = new AbortController();
    setGoals([]);
    if (productFeatures.studentGoals)
      void goalsRequest(
        "",
        readStudentGoalsBoard,
        "GET",
        undefined,
        abort.signal,
        accountKey,
      )
        .then((data) => {
          if (!abort.signal.aborted)
            setGoals(
              data.goals.filter((g) => g.status === "active").slice(0, 3),
            );
        })
        .catch(() => {});
    return () => {
      abort.abort();
    };
  }, [accountKey]);
  const zone = timetable.timezone,
    today = studentDateInZone(new Date(now).toISOString(), zone);
  const first =
    view === "week"
      ? mondayOf(today)
      : view === "tomorrow"
        ? addStudentDays(today, 1)
        : today;
  const days = Array.from({ length: view === "week" ? 7 : 1 }, (_, i) =>
    addStudentDays(first, i),
  );
  const local = studentLocalTime(now, zone);
  const active = plan.blocks
    .filter((b) => b.status === "planned")
    .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
  const selectedBlock = active.find((b) => b.id === selected);
  const next = selectedBlock ?? active.find((b) => Date.parse(b.end_at) > now);
  const nextTask = tasks.find((t) => t.id === next?.task_id);
  const minutes = (date: string) => {
    const value = studentLocalTime(date, zone);
    return value.hour * 60 + value.minute;
  };
  const allRows = days.map((day) => ({
    day,
    rows: dayRows(
      timetable,
      plan,
      tasks,
      [],
      Date.parse(studyDeadlineChoices(`${day}T12:00`, zone)[0]),
      zone,
    ),
  }));
  const start = Math.min(
    480,
    ...allRows.flatMap((d) => d.rows.map((r) => minutes(r.start))),
  );
  const end = Math.max(
    1380,
    ...allRows.flatMap((d) =>
      d.rows.map((r) => (r.end ? minutes(r.end) : minutes(r.start) + 30)),
    ),
  );
  const width = end - start;
  const visibleBlocks = plan.blocks.filter(
    (b) =>
      days.includes(studentDateInZone(b.start_at, zone)) &&
      ["planned", "done", "took_longer"].includes(b.status),
  );
  const planned = visibleBlocks
    .filter((b) => b.status === "planned")
    .reduce((sum, b) => sum + b.minutes, 0);
  const recorded = visibleBlocks
    .filter((b) => ["done", "took_longer"].includes(b.status))
    .reduce((sum, b) => sum + b.actual_minutes, 0);
  const group = (task: StudyTask) =>
    task.due_at && studentDateInZone(task.due_at, zone) <= today
      ? "Today"
      : task.due_at &&
          studentDateInZone(task.due_at, zone) <=
            addStudentDays(mondayOf(today), 6)
        ? "This week"
        : "Later";
  return (
    <div
      className={styles.overview}
      data-planner-review={completing || receipt ? true : undefined}
    >
      <div className={styles.toolbar}>
        <nav aria-label="Planner time range">
          {(
            [
              ["today", "Today"],
              ["tomorrow", "Tomorrow"],
              ["week", "This week"],
            ] as const
          ).map(([key, label]) => (
            <button
              type="button"
              key={key}
              aria-pressed={view === key}
              onClick={() => setView(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        <p>
          <strong>
            {planned
              ? `${studyMinutes(planned)} of study planned.`
              : "No study blocks planned in this view."}
          </strong>{" "}
          {recorded > 0 && `${studyMinutes(recorded)} of study recorded. `}
          {plan.needs_review
            ? "Your plan needs a fresh review."
            : openings.data?.calendar_checked && openings.data.routine_confirmed
              ? `${studyMinutes(openings.data.openings.reduce((sum, g) => sum + g.minutes, 0))} free across the next 7 days, after your saved blocks and breaks. Review a replan before adding work.`
              : "Review a replan to check what fits."}
        </p>
        <button type="button" disabled={disabled} onClick={onReplan}>
          Replan week
        </button>
        <button
          type="button"
          className={styles.primary}
          disabled={disabled}
          onClick={onAdd}
        >
          Add task
        </button>
      </div>
      <div className={styles.planColumns}>
        <div>
          <section className={styles.schedule} aria-label="Study week">
            <div className={styles.scheduleScroll}>
              <div className={styles.scheduleInner}>
                <div className={styles.axis}>
                  <span />
                  {Array.from(
                    { length: Math.floor(width / 120) + 1 },
                    (_, i) => start + i * 120,
                  ).map((m) => (
                    <span
                      key={m}
                      style={{
                        left: `calc(72px + (100% - 72px) * ${(m - start) / width})`,
                      }}
                    >
                      {Math.floor(m / 60) % 12 || 12}
                      {m < 720 ? "a" : "p"}
                    </span>
                  ))}
                </div>
                {allRows.map(({ day, rows }) => (
                  <div
                    className={styles.dayRow}
                    key={day}
                    data-today={day === today}
                  >
                    <time dateTime={day}>
                      {new Intl.DateTimeFormat("en", {
                        weekday: "short",
                        day: "numeric",
                        timeZone: "UTC",
                      }).format(new Date(`${day}T12:00:00Z`))}
                    </time>
                    <div className={styles.track}>
                      {rows
                        .filter(
                          (r) =>
                            !r.study ||
                            !["skipped", "needs_review"].includes(
                              r.study.status,
                            ),
                        )
                        .map((row) => (
                          <button
                            key={row.id}
                            type="button"
                            title={`${row.title} · ${studentClock(row.start, zone)} – ${row.end ? studentClock(row.end, zone) : ""}`}
                            className={styles.planBlock}
                            data-study={!!row.study}
                            data-shift={row.detail === "Shift"}
                            data-done={
                              row.study
                                ? ["done", "took_longer"].includes(
                                    row.study.status,
                                  )
                                : !!row.end && Date.parse(row.end) < now
                            }
                            aria-pressed={row.study?.id === next?.id}
                            style={
                              {
                                left: `${((minutes(row.start) - start) / width) * 100}%`,
                                width: `${Math.max(1, (((row.end ? minutes(row.end) : minutes(row.start) + 30) - minutes(row.start)) / width) * 100)}%`,
                              } as CSSProperties
                            }
                            onClick={() => {
                              if (row.study) setSelected(row.study.id);
                              else router.push(row.href);
                            }}
                          >
                            {row.title}
                          </button>
                        ))}
                      {day === today &&
                        local.hour * 60 + local.minute >= start &&
                        local.hour * 60 + local.minute < end && (
                          <span
                            className={styles.now}
                            style={{
                              left: `${((local.hour * 60 + local.minute - start) / width) * 100}%`,
                            }}
                          />
                        )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className={styles.legend}>
              <span>▱ Study block</span>
              <span>■ Class</span>
              <span>▱ Shift</span>
              <span>✓ Done</span>
            </div>
          </section>
          {next && nextTask && (
            <section className={styles.nextStudy}>
              <p>
                {selectedBlock ? "Selected" : "Next"} study block ·{" "}
                {studentClock(next.start_at, zone)} –{" "}
                {studentClock(next.end_at, zone)}
              </p>
              <h3>{next.title}</h3>
              <p>
                {
                  timetable.courses.find((c) => c.id === nextTask.course_id)
                    ?.code
                }{" "}
                · {studyMinutes(next.minutes)}
                {nextTask.due_at &&
                  ` · due ${new Intl.DateTimeFormat("en", { weekday: "short", month: "short", day: "numeric", timeZone: zone }).format(new Date(nextTask.due_at))}`}
              </p>
              {!disabled && !plan.needs_review && (
                <TodayStudyActions
                  onMoved={onReload}
                  key={next.id + next.revision}
                  block={next}
                  task={nextTask}
                  now={now}
                  timezone={zone}
                  accountKey={accountKey}
                  onSaved={(result) => {
                    setReceipt({
                      result,
                      block: next,
                      task: nextTask,
                      savedAt: Date.now(),
                    });
                    onReload();
                  }}
                />
              )}
            </section>
          )}
          {!next && (
            <section className={styles.nextStudy}>
              <h3>No upcoming study block</h3>
              <p>
                Select a past block to record its outcome, or review a replan
                for your remaining work.
              </p>
            </section>
          )}
          {receipt && (
            <TodayProgressReceipt
              receipt={receipt}
              timezone={zone}
              accountKey={accountKey}
              onClose={() => {
                setReceipt(null);
                onReload();
              }}
            />
          )}
        </div>
        <section className={styles.tasks}>
          <h2>Tasks</h2>
          {completing && (
            <TaskCompletionReview
              key={completing.id + ":" + completing.revision}
              task={completing}
              accountKey={accountKey}
              timezone={zone}
              onClose={() => setCompleting(null)}
              onSaved={() => {
                setCompleting(null);
                onReload();
              }}
            />
          )}

          {["Today", "This week", "Later"].map((label) => {
            const grouped = tasks.filter(
              (t) => t.status === "active" && group(t) === label,
            );
            return grouped.length > 0 ? (
              <div key={label}>
                <h3>{label}</h3>
                {grouped.map((task) => (
                  <div key={task.id} className={styles.task}>
                    <label className={styles.taskCheck}>
                      <input
                        type="checkbox"
                        checked={false}
                        aria-label={"Mark " + task.title + " complete"}
                        className={styles.taskCircle}
                        disabled={disabled}
                        onChange={() => setCompleting(task)}
                      />
                    </label>
                    <button
                      type="button"
                      className={styles.taskDetails}
                      disabled={disabled}
                      onClick={() => onEdit(task)}
                    >
                      <span>
                        <strong>{task.title}</strong>
                        <small>
                          <i
                            style={{
                              background: task.course_id
                                ? courseColor(task.course_id)
                                : "var(--nx-accent)",
                            }}
                          />
                          {
                            timetable.courses.find(
                              (c) => c.id === task.course_id,
                            )?.code
                          }{" "}
                          · ~{studyMinutes(task.remaining_minutes)} ·{" "}
                          {active.filter((b) => b.task_id === task.id).length}{" "}
                          blocks planned
                        </small>
                      </span>
                      {task.due_at && (
                        <time
                          dateTime={task.due_at}
                          data-urgent={
                            Date.parse(task.due_at) - now <= 172800000
                          }
                        >
                          {new Intl.DateTimeFormat("en", {
                            weekday: "short",
                            month: "short",
                            day: "numeric",
                            timeZone: zone,
                          }).format(new Date(task.due_at))}
                        </time>
                      )}
                    </button>
                  </div>
                ))}
              </div>
            ) : null;
          })}
        </section>
      </div>
      <div className={styles.goals}>
        <strong>Goals</strong>
        {goals.map((goal) => (
          <Link key={goal.id} href="/goals">
            {goal.title}{" "}
            <b>
              {goal.tracking === "count" &&
              goal.progress.reported_count !== null
                ? `${goal.progress.reported_count}/${goal.progress.target}`
                : goal.progress.qualitative_latest === "yes"
                  ? "✓"
                  : "Check in"}
            </b>
          </Link>
        ))}
        <Link href="/goals">Manage goals</Link>
        <Link href="/recovery">Catch up on missed work</Link>
        <button type="button" onClick={onRoutine}>
          Your routine
        </button>
      </div>
    </div>
  );
}
