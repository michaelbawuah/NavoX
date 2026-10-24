"use client";
import { Button, courseColor } from "@navox/ui";
import Link from "next/link";
import {
  type CSSProperties,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { productFeatures } from "../lib/product-features";
import {
  createSavedBoardsApi,
  type SavedBoardSummary,
} from "../lib/saved-boards";
import {
  goalsRequest,
  readStudentGoalsBoard,
  type StudentGoal,
} from "../lib/student-goals";
import {
  readStudyPlan,
  readStudyTasks,
  type StudyPlan,
  type StudyTask,
  studyRequest,
} from "../lib/student-planning";
import {
  readStudentTimetable,
  type StudentTimetable,
  studentClock,
  studentRequest,
} from "../lib/student-schedule";
import {
  dayRows,
  dueSoon,
  type HomeItem,
  readHomeItems,
  relativeTime,
  replyItems,
} from "../lib/today-home";
import styles from "./today-home.module.css";
import { TodayStudyActions } from "./today-study-actions";
import type { TodayWorkspaceProps } from "./today-workspace";
import { useVoxCall } from "./vox-call-provider";
import { WorkspaceShell } from "./workspace-shell";

const base =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
type Data = {
  timetable: StudentTimetable | null;
  plan: StudyPlan | null;
  tasks: StudyTask[];
  items: HomeItem[];
  goals: StudentGoal[];
  boards: SavedBoardSummary[];
  complete: boolean;
  receivedAt: number;
};
function savedFlag(key: string) {
  try {
    return localStorage.getItem(key) === "yes";
  } catch {
    return false;
  }
}
function setFlag(key: string, value: boolean) {
  try {
    if (value) localStorage.setItem(key, "yes");
    else localStorage.removeItem(key);
  } catch {
    /* Setup remains usable without browser storage. */
  }
}
export function TodayHome(props: TodayWorkspaceProps) {
  const accountKey = `${props.account.workspace.id}:${props.account.id}`;
  return (
    <TodayHomeAccount key={accountKey} {...props} accountKey={accountKey} />
  );
}
function TodayHomeAccount({
  account,
  connections,
  connectionsUnavailable,
  onSignOut,
  onConnectionsChanged,
  accountKey,
}: TodayWorkspaceProps & { accountKey: string }) {
  const [data, setData] = useState<Data | null>(null),
    [error, setError] = useState(false),
    [busy, setBusy] = useState(false);
  const [clock, setClock] = useState<number | null>(null),
    [dismissed, setDismissed] = useState(false),
    [triedVox, setTriedVox] = useState(false);
  const [setupStarted, setSetupStarted] = useState(false);
  const call = useVoxCall();
  const epoch = useRef(0);
  const setupKey = `navox:setup:${accountKey}`,
    voxKey = `navox:tried-vox:${accountKey}`;
  useEffect(() => {
    setDismissed(savedFlag(`${setupKey}:dismissed`));
    setSetupStarted(savedFlag(setupKey));
    setTriedVox(savedFlag(voxKey));
  }, [setupKey, voxKey]);
  useEffect(() => {
    if (
      call?.state.ownerVerified &&
      call.state.phase === "active" &&
      call.state.activity === "speaking"
    ) {
      setTriedVox(true);
      setFlag(voxKey, true);
    }
  }, [
    call?.state.ownerVerified,
    call?.state.phase,
    call?.state.activity,
    voxKey,
  ]);
  const load = useCallback(async () => {
    const turn = ++epoch.current;
    setBusy(true);
    const zone =
      account.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
    const controller = new AbortController();
    const results = await Promise.allSettled([
      studentRequest("/timetable?days=1", readStudentTimetable),
      productFeatures.studentPlanning
        ? studyRequest("/plan", readStudyPlan)
        : Promise.resolve(null),
      productFeatures.studentPlanning
        ? studyRequest("/tasks", readStudyTasks)
        : Promise.resolve([]),
      fetch(`${base}/today?timezone=${encodeURIComponent(zone)}`, {
        credentials: "include",
        cache: "no-store",
        headers: { "X-NavoX-Account": accountKey },
        signal: AbortSignal.timeout(12000),
      }).then(async (r) => {
        if (!r.ok) throw Error("Day unavailable");
        return readHomeItems(await r.json());
      }),
      productFeatures.studentGoals
        ? goalsRequest("", readStudentGoalsBoard)
        : Promise.resolve(null),
      productFeatures.studentWhiteboard
        ? createSavedBoardsApi(accountKey).list(controller.signal)
        : Promise.resolve(null),
    ] as const);
    if (turn !== epoch.current) return;
    const [timetable, plan, tasks, items, goals, boards] = results;
    const value: Data = {
      timetable: timetable.status === "fulfilled" ? timetable.value : null,
      plan: plan.status === "fulfilled" ? plan.value : null,
      tasks: tasks.status === "fulfilled" ? tasks.value : [],
      items: items.status === "fulfilled" ? items.value : [],
      goals: goals.status === "fulfilled" ? (goals.value?.goals ?? []) : [],
      boards: boards.status === "fulfilled" ? (boards.value?.boards ?? []) : [],
      complete: results.every((r) => r.status === "fulfilled"),
      receivedAt: Date.now(),
    };
    setData(value);
    setClock(Date.now());
    setBusy(false);
    setError(!value.complete);
    if (
      value.complete &&
      !value.timetable?.courses.some((c) => !c.archived) &&
      !value.timetable?.fixed_occurrences.length &&
      !value.goals.length &&
      !value.tasks.length &&
      !value.items.length &&
      !value.boards.length
    ) {
      setSetupStarted(true);
      setFlag(setupKey, true);
    }
  }, [account.timezone, accountKey, setupKey]);
  useEffect(() => {
    void load();
    const visible = () => {
      if (document.visibilityState === "visible") void load();
    };
    const interval = window.setInterval(visible, 60000),
      tick = window.setInterval(() => setClock(Date.now()), 15000);
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("focus", visible);
    return () => {
      ++epoch.current;
      window.clearInterval(interval);
      window.clearInterval(tick);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("focus", visible);
    };
  }, [load]);
  const zone =
    data?.timetable?.timezone ||
    account.timezone ||
    Intl.DateTimeFormat().resolvedOptions().timeZone;
  const now = data?.timetable
    ? Date.parse(data.timetable.server_now) +
      Math.max(0, (clock ?? data.receivedAt) - data.receivedAt)
    : (clock ?? 0);
  const hour = clock
    ? Number(
        new Intl.DateTimeFormat("en-US", {
          hour: "numeric",
          hourCycle: "h23",
          timeZone: zone,
        }).format(new Date(now)),
      )
    : null;
  const firstName =
    account.display_name?.trim().split(/\s+/)[0] || account.email.split("@")[0];
  const greeting =
    hour === null
      ? "Hello"
      : hour < 12
        ? "Good morning"
        : hour < 17
          ? "Good afternoon"
          : "Good evening";
  const rows = data
    ? dayRows(data.timetable, data.plan, data.tasks, data.items, now, zone)
    : [];
  const next = rows.find((r) => !r.study && Date.parse(r.start) > now);
  const due = data ? dueSoon(data.tasks, data.items, now) : [];
  const replies = replyItems(data?.items ?? [], connections);
  const goals = (data?.goals ?? [])
    .filter((g) => g.status === "active")
    .slice(0, 3);
  const board = (data?.boards ?? [])
    .filter((b) => !b.complete)
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))[0];
  const hasClasses = !!data?.timetable?.courses.some((c) => !c.archived);
  const hasCalendar =
    !connectionsUnavailable &&
    connections.some(
      (c) =>
        c.status === "active" &&
        c.granted_scopes.some((s) =>
          [
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar.events.readonly",
            "https://www.googleapis.com/auth/calendar.events",
          ].includes(s),
        ),
    );
  const hasTask =
    !!data?.tasks.length ||
    !!data?.items.some((i) => ["task", "deadline"].includes(i.type));
  const steps = [hasClasses, hasCalendar, hasTask, triedVox],
    done = steps.filter(Boolean).length;
  const showSetup = !!data && setupStarted && !dismissed && done < 4;
  async function openVox() {
    if (!call?.available || !call.state.ownerVerified) return;
    if (
      ["active", "connecting", "reconnecting", "standby"].includes(
        call.state.phase,
      )
    )
      call.controller.setMinimized(false);
    else await call.controller.start();
  }
  return (
    <WorkspaceShell
      current="Today"
      accountName={account.display_name ?? account.email}
      accountKey={accountKey}
      onSignOut={onSignOut}
      timezone={zone}
      inboxCount={replies.length}
    >
      <div className={styles.home}>
        <h2 className={styles.greeting}>
          {greeting}, {firstName}
        </h2>
        {(error || connectionsUnavailable) && (
          <div className={styles.failure} role="alert">
            <span>Some of your day couldn’t load.</span>
            <Button
              variant="quiet"
              disabled={busy}
              onClick={() => {
                void load();
                if (connectionsUnavailable)
                  void onConnectionsChanged().catch(() => {});
              }}
            >
              Try again
            </Button>
          </div>
        )}
        {!data && (
          <p className={styles.message} role="status">
            Loading your day…
          </p>
        )}
        {showSetup && (
          <section className={styles.setup} aria-label="Set up NavoX">
            <div className={styles.setupHeader}>
              <div>
                <h2>Let’s set up NavoX</h2>
                <small>{done} of 4 done</small>
              </div>
              <Button
                variant="quiet"
                aria-label="Close setup checklist"
                onClick={() => {
                  setDismissed(true);
                  setFlag(`${setupKey}:dismissed`, true);
                }}
              >
                ×
              </Button>
            </div>
            <ul>
              <li data-done={hasClasses}>
                <Link href="/classes">
                  <span className={styles.check}>{hasClasses ? "✓" : ""}</span>
                  <span>
                    Add your classes <small>or import a schedule file</small>
                  </span>
                  <span aria-hidden="true">→</span>
                </Link>
              </li>
              <li data-done={hasCalendar}>
                <Link href="/connections">
                  <span className={styles.check}>{hasCalendar ? "✓" : ""}</span>
                  <span>Connect your calendar</span>
                  <span aria-hidden="true">→</span>
                </Link>
              </li>
              <li data-done={hasTask}>
                <Link href="/planner#add-task">
                  <span className={styles.check}>{hasTask ? "✓" : ""}</span>
                  <span>Add one task or assignment</span>
                  <span aria-hidden="true">→</span>
                </Link>
              </li>
              <li data-done={triedVox}>
                <button
                  type="button"
                  disabled={!call?.available}
                  onClick={() => void openVox()}
                >
                  <span className={styles.check}>{triedVox ? "✓" : ""}</span>
                  <span>Try Vox: say “What’s next?”</span>
                  <span aria-hidden="true">→</span>
                </button>
              </li>
            </ul>
          </section>
        )}
        {next && (
          <details className={styles.next}>
            <summary>
              <strong>
                Next: {next.title}
                {next.detail && ` · ${next.detail}`}
              </strong>
              <span>
                {relativeTime(next.start, now, zone)}
                {next.walkMinutes !== null &&
                  ` · leave by ${studentClock(new Date(Date.parse(next.start) - next.walkMinutes * 60000).toISOString(), zone)}`}
              </span>
            </summary>
            <p>
              {new Intl.DateTimeFormat("en", {
                dateStyle: "medium",
                timeStyle: "short",
                timeZone: zone,
              }).format(new Date(next.start))}
              {next.end && ` – ${studentClock(next.end, zone)}`}
            </p>
            <Link href={next.href}>
              Open{" "}
              {next.study
                ? "Planner"
                : next.detail === "Meeting"
                  ? "Planner"
                  : "Classes"}{" "}
              →
            </Link>
          </details>
        )}
        {data &&
          !showSetup &&
          !rows.length &&
          !due.length &&
          !replies.length &&
          !goals.length &&
          !board &&
          !error &&
          !connectionsUnavailable && (
            <p className={styles.message}>Done for today</p>
          )}
        {(rows.length > 0 ||
          due.length > 0 ||
          replies.length > 0 ||
          goals.length > 0 ||
          board) && (
          <div className={styles.grid}>
            <div className={styles.column}>
              {rows.length > 0 && (
                <section
                  className={styles.section}
                  aria-labelledby="today-plan"
                >
                  <div className={styles.heading}>
                    <h2 id="today-plan">Today’s plan</h2>
                  </div>
                  <ol className={styles.list}>
                    {rows.map((row) => {
                      const past =
                          (row.end
                            ? Date.parse(row.end)
                            : Date.parse(row.start)) <= now,
                        current =
                          Date.parse(row.start) <= now &&
                          !!row.end &&
                          Date.parse(row.end) > now;
                      return (
                        <li
                          key={row.id}
                          className={styles.row}
                          data-past={past}
                          data-current={current}
                        >
                          <Link href={row.href} className={styles.rowTop}>
                            <span
                              className="nx-dot"
                              style={
                                {
                                  "--nx-course": row.courseId
                                    ? courseColor(row.courseId)
                                    : undefined,
                                } as CSSProperties
                              }
                            />
                            <time dateTime={row.start}>
                              {studentClock(row.start, zone)}
                            </time>
                            <span className={styles.rowText}>
                              <strong className={styles.rowTitle}>
                                {row.title}
                              </strong>
                              {row.detail && (
                                <span className={styles.rowDetail}>
                                  {row.detail}
                                </span>
                              )}
                            </span>
                            <span className={styles.status}>
                              {row.study && row.study.status !== "planned"
                                ? {
                                    done: "Done",
                                    took_longer: "Done",
                                    skipped: "Skipped",
                                    needs_review: "Review",
                                    superseded: "",
                                  }[row.study.status]
                                : current
                                  ? "Now"
                                  : next?.id === row.id
                                    ? "Next"
                                    : ""}
                            </span>
                          </Link>
                          {row.study?.status === "planned" &&
                            !data?.plan?.needs_review &&
                            (() => {
                              const task = data?.tasks.find(
                                (task) => task.id === row.study?.task_id,
                              );
                              return (
                                task && (
                                  <TodayStudyActions
                                    key={`${row.study.id}:${row.study.revision}:${task.revision}`}
                                    block={row.study}
                                    task={task}
                                    accountKey={accountKey}
                                    timezone={zone}
                                    now={now}
                                    onSaved={() => void load()}
                                  />
                                )
                              );
                            })()}
                        </li>
                      );
                    })}
                  </ol>
                </section>
              )}
              {board && (
                <section className={styles.section}>
                  <div className={styles.heading}>
                    <h2>Continue learning</h2>
                  </div>
                  <Link
                    className={styles.compact}
                    href={`/board#saved=${board.id}`}
                  >
                    <span>
                      <strong>{board.title}</strong>
                      <small>Pick up where you left off</small>
                    </span>
                    <span className={styles.status}>Resume →</span>
                  </Link>
                </section>
              )}
            </div>
            <div className={styles.column}>
              {due.length > 0 && (
                <section className={styles.section}>
                  <div className={styles.heading}>
                    <h2>Due soon</h2>
                    <Link href="/planner">See all</Link>
                  </div>
                  <ul className={styles.list}>
                    {due.map((item) => (
                      <li key={item.id}>
                        <Link className={styles.compact} href="/planner">
                          {item.courseId && (
                            <i
                              className="nx-dot"
                              style={
                                {
                                  "--nx-course": courseColor(item.courseId),
                                } as CSSProperties
                              }
                            />
                          )}
                          <span>
                            <strong>{item.title}</strong>
                            <small>{relativeTime(item.due, now, zone)}</small>
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {replies.length > 0 && (
                <section className={styles.section}>
                  <div className={styles.heading}>
                    <h2>Needs a reply</h2>
                  </div>
                  <ul className={styles.list}>
                    {replies.slice(0, 3).map((item) => (
                      <li key={item.id}>
                        <Link
                          className={styles.compact}
                          href="/inbox#needs-reply"
                        >
                          <span>
                            <strong>{item.title}</strong>
                            {item.due_at && (
                              <small>
                                {relativeTime(item.due_at, now, zone)}
                              </small>
                            )}
                          </span>
                          <span aria-hidden="true">→</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              {goals.length > 0 && (
                <section className={styles.section}>
                  <div className={styles.heading}>
                    <h2>Goals</h2>
                  </div>
                  <div className={styles.chips}>
                    {goals.map((goal) => (
                      <Link key={goal.id} className="nx-chip" href="/goals">
                        <span>{goal.title}</span>
                        <span>
                          {goal.tracking === "count"
                            ? goal.progress.reported_count === null
                              ? "No check-in"
                              : `${goal.progress.reported_count}/${goal.progress.target}`
                            : goal.progress.qualitative_latest === "unknown"
                              ? "No check-in"
                              : goal.progress.qualitative_latest === "yes"
                                ? "✓"
                                : "Not yet"}
                        </span>
                      </Link>
                    ))}
                  </div>
                </section>
              )}
            </div>
          </div>
        )}
        {dismissed && data && !rows.length && !hasClasses && (
          <Button
            variant="quiet"
            onClick={() => {
              setDismissed(false);
              setSetupStarted(true);
              setFlag(setupKey, true);
              setFlag(`${setupKey}:dismissed`, false);
            }}
          >
            Show setup checklist
          </Button>
        )}
      </div>
    </WorkspaceShell>
  );
}
