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
import { createStudentCalendarApi } from "../lib/student-calendar-api";
import {
  goalsRequest,
  readStudentGoalsBoard,
  type StudentGoal,
} from "../lib/student-goals";
import {
  type AlarmOccurrence,
  readAlarmView,
  readStudyPlan,
  readStudyTasks,
  routineRequest,
  type StudyPlan,
  type StudyTask,
  studyMinutes,
  studyRequest,
} from "../lib/student-planning";
import {
  readStudentTimetable,
  type StudentTimetable,
  studentClock,
  studentRequest,
} from "../lib/student-schedule";
import { studentLocalTime } from "../lib/student-time";
import {
  type DayRow,
  dayRows,
  dueSoon,
  type HomeItem,
  readHomeItems,
  relativeTime,
  replyItems,
} from "../lib/today-home";
import { NoaArtwork } from "./noa-artwork";
import { noaVibes, useNoaChoice } from "./noa-settings";
import styles from "./today-home.module.css";
import { TodayNext } from "./today-next";
import { TodayEveningAlarm, TodayNightActions } from "./today-night";
import {
  type ProgressReceipt,
  TodayProgressReceipt,
} from "./today-progress-receipt";
import { TodaySetup } from "./today-setup";
import { TodayStudyActions } from "./today-study-actions";
import type { TodayWorkspaceProps } from "./today-workspace";
import { useStudyOpenings } from "./use-study-openings";
import { useVoxRequest } from "./use-vox-request";
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
  calendarConnected: boolean;
  alarms: AlarmOccurrence[];
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
  const [receipt, setReceipt] = useState<ProgressReceipt | null>(null);
  const [data, setData] = useState<Data | null>(null),
    [error, setError] = useState(false),
    [busy, setBusy] = useState(false);
  const [clock, setClock] = useState<number | null>(null),
    [dismissed, setDismissed] = useState(false),
    [triedVox, setTriedVox] = useState(false);
  const [setupStarted, setSetupStarted] = useState(false);
  const [showNightDay, setShowNightDay] = useState(false);
  const call = useVoxCall();
  const voxRequest = useVoxRequest();
  const openings = useStudyOpenings(accountKey, 1, data?.plan?.revision ?? 0);
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
      productFeatures.studentCalendarAvailability
        ? createStudentCalendarApi(accountKey).calendars(
            AbortSignal.timeout(12000),
          )
        : Promise.resolve(null),
      productFeatures.studentPlanning
        ? routineRequest(accountKey, "/alarm", readAlarmView).catch(() => null)
        : Promise.resolve(null),
    ] as const);
    if (turn !== epoch.current) return;
    const [timetable, plan, tasks, items, goals, boards, calendar, alarms] =
      results;
    const value: Data = {
      alarms:
        alarms.status === "fulfilled"
          ? (alarms.value?.alarm_occurrences ?? [])
          : [],
      timetable: timetable.status === "fulfilled" ? timetable.value : null,
      plan: plan.status === "fulfilled" ? plan.value : null,
      tasks: tasks.status === "fulfilled" ? tasks.value : [],
      items: items.status === "fulfilled" ? items.value : [],
      goals: goals.status === "fulfilled" ? (goals.value?.goals ?? []) : [],
      boards: boards.status === "fulfilled" ? (boards.value?.boards ?? []) : [],
      calendarConnected:
        calendar.status === "fulfilled" &&
        !!calendar.value?.grants.some((g) => g.status === "connected"),
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
    window.addEventListener("navox:study-plan-changed", visible);
    return () => {
      ++epoch.current;
      window.clearInterval(interval);
      window.clearInterval(tick);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("focus", visible);
      window.removeEventListener("navox:study-plan-changed", visible);
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
  const firstName =
    account.display_name?.trim().split(/\s+/)[0] || account.email.split("@")[0];
  const greeting = clock ? studentLocalTime(now, zone).greeting : "Hello";
  const rows = data
    ? dayRows(data.timetable, data.plan, data.tasks, data.items, now, zone)
    : [];
  const verifiedGaps =
    openings.data?.calendar_checked && openings.data.routine_confirmed
      ? openings.data.openings
      : [];
  const bestGap = [...verifiedGaps].sort((a, b) => b.minutes - a.minutes)[0];
  const displayRows: DayRow[] = [
    ...rows,
    ...verifiedGaps.slice(0, 3).map((gap) => ({
      id: `gap:${gap.start_at}`,
      title: `Free · ${studyMinutes(gap.minutes)}`,
      start: gap.start_at,
      end: gap.end_at,
      courseId: null,
      detail: "Plan some work here",
      href: "/planner#add-task",
      walkMinutes: null,
    })),
  ].sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
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
  // Legacy provider scopes do not prove consent for student calendar access.
  // Only the purpose-bound calendar API can mark this setup step complete.
  const hasCalendar = data?.calendarConnected === true;
  const hasEmail =
    !connectionsUnavailable &&
    connections.some(
      (c) =>
        c.status === "active" &&
        c.granted_scopes.some((s) =>
          [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
          ].includes(s),
        ),
    );
  const hasTask =
    !!data?.tasks.length ||
    !!data?.items.some((i) => ["task", "deadline"].includes(i.type));
  const steps = [hasClasses, !!hasCalendar, hasEmail, hasTask, triedVox],
    done = steps.filter(Boolean).length;
  const showSetup = !!data && setupStarted && !dismissed && done < 5;
  const welcoming = !!data && !hasClasses && !rows.length && !hasTask && !error;
  const noa = useNoaChoice(accountKey, data?.plan?.revision ?? 0);
  const local = clock ? studentLocalTime(now, zone) : null;
  const late = local?.period === "late-night" && !welcoming;
  const firstClass = rows.find(
    (r) => r.courseId && !r.study && Date.parse(r.start) > now,
  );
  const todayAlarm = data?.alarms.find((a) => a.day === local?.date);
  const classesLeft = rows.filter(
    (r) => r.courseId && !r.study && Date.parse(r.end || r.start) > now,
  ).length;
  const summary = !data?.timetable
    ? ""
    : `${classesLeft ? `${classesLeft} ${classesLeft === 1 ? "class" : "classes"} left today` : "No more classes on your timetable today"}${due.length ? `, and ${due.length} ${due.length === 1 ? "thing" : "things"} due soon` : ""}.`;
  const sticker = welcoming
    ? "hi :)"
    : local?.period === "late-night"
      ? "it’s 3am fr"
      : local?.period === "morning"
        ? "rise & grind"
        : local?.period === "afternoon"
          ? "chillin"
          : "winding down";
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
      night={late}
      accountName={account.display_name ?? account.email}
      accountKey={accountKey}
      onSignOut={onSignOut}
      timezone={zone}
      inboxCount={replies.length}
    >
      <div className={styles.home}>
        <header className={styles.todayHero}>
          <div className={styles.heroWords}>
            {clock && (
              <p className={styles.dayLabel}>
                {new Intl.DateTimeFormat("en-US", {
                  timeZone: zone,
                  weekday: "long",
                  month: "long",
                  day: "numeric",
                }).format(new Date(now))}
              </p>
            )}
            <h2 className={styles.greeting}>
              {welcoming
                ? `Hey ${firstName}, welcome to NavoX.`
                : `${greeting}, ${firstName}${local?.period === "late-night" ? "?" : "."}`}
            </h2>
            <p className={styles.heroSummary}>
              {welcoming
                ? "I’m Noa. Give me about 3 minutes and I’ll know your week: your classes, what’s due, and when you’re actually free."
                : late && firstClass
                  ? `Your first class is at ${studentClock(firstClass.start, zone)}. Let’s leave some room for sleep.`
                  : summary +
                    (bestGap
                      ? ` Best free window: ${studentClock(bestGap.start_at, zone)}–${studentClock(bestGap.end_at, zone)}.`
                      : "")}
            </p>
            {!welcoming && !late && data && (
              <div className={styles.chips}>
                <button
                  type="button"
                  className={styles.voxChip}
                  disabled={!voxRequest.available || voxRequest.busy}
                  onClick={() =>
                    void voxRequest.ask(
                      local?.hour !== undefined && local.hour < 17
                        ? "Plan my afternoon around my real classes, shifts and deadlines. Read back every proposed change and wait for my approval before saving it."
                        : "Help me plan tomorrow around my real classes, shifts and deadlines. Read back every proposed change and wait for my approval before saving it.",
                    )
                  }
                >
                  Ask Vox:{" "}
                  {local?.hour !== undefined && local.hour < 17
                    ? "plan my afternoon"
                    : "plan tomorrow"}
                </button>
                {!!noa.value?.study_streak && (
                  <span className={styles.progressChip}>
                    {noa.value.study_streak} day study streak
                  </span>
                )}
                {rows.some((row) => row.study) && (
                  <span className={styles.progressChip}>
                    {
                      rows.filter(
                        (row) =>
                          row.study &&
                          ["done", "took_longer"].includes(row.study.status),
                      ).length
                    }{" "}
                    of{" "}
                    {
                      rows.filter(
                        (row) =>
                          row.study &&
                          !["superseded", "skipped"].includes(row.study.status),
                      ).length
                    }{" "}
                    study blocks done today
                  </span>
                )}
                {voxRequest.notice && (
                  <span role="status">{voxRequest.notice}</span>
                )}
              </div>
            )}
            {welcoming && (
              <div className={styles.heroActions}>
                <Button
                  variant="primary"
                  disabled={!call?.available}
                  onClick={() => void openVox()}
                >
                  Set up by talking
                </Button>
                <Link className="nx-button" href="/classes">
                  Set up by typing
                </Link>
                <span>
                  Just say: “I have CS 2110 Monday, Wednesday, Friday at 10:10”
                </span>
              </div>
            )}
          </div>
          <div className={styles.heroNoa}>
            <span className={styles.noaSticker}>{sticker}</span>
            <NoaArtwork
              size={200}
              look={noa.value?.look}
              hoodie={noa.value ? noaVibes[noa.value.vibe] : undefined}
              mood={
                welcoming
                  ? "happy"
                  : local?.period === "late-night"
                    ? "sleepy"
                    : "idle"
              }
              mug={!welcoming && local?.period === "morning"}
            />
          </div>
        </header>
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
        {receipt && (
          <TodayProgressReceipt
            key={receipt.result.undo_request_id}
            receipt={receipt}
            timezone={zone}
            accountKey={accountKey}
            onClose={() => {
              setReceipt(null);
              void load();
            }}
          />
        )}
        {showSetup && (
          <TodaySetup
            completed={steps}
            canTalk={!!call?.available}
            openVox={() => void openVox()}
            onHide={() => {
              setDismissed(true);
              setFlag(`${setupKey}:dismissed`, true);
            }}
          />
        )}
        {late && <TodayNightActions alarm={todayAlarm} now={now} />}
        {local && local.hour >= 21 && (
          <TodayEveningAlarm
            alarm={data?.alarms.find((a) => a.day > local.date)}
            timezone={zone}
          />
        )}
        {late && firstClass ? (
          <TodayNext
            row={firstClass}
            now={now}
            timezone={zone}
            label="First thing today"
          />
        ) : (
          next && <TodayNext row={next} now={now} timezone={zone} />
        )}
        {late && (
          <>
            <div className={styles.chips}>
              {due.map((item) => (
                <Link
                  className={styles.progressChip}
                  href="/planner"
                  key={item.id}
                >
                  {item.title} · {relativeTime(item.due, now, zone)}
                </Link>
              ))}
            </div>
            <button
              type="button"
              className={styles.nightToggle}
              onClick={() => setShowNightDay(!showNightDay)}
            >
              {showNightDay ? "Keep the night quiet" : "See your full day"}
            </button>
          </>
        )}
        {data &&
          !showSetup &&
          hasClasses &&
          !rows.length &&
          !due.length &&
          !replies.length &&
          !goals.length &&
          !board &&
          !error &&
          !connectionsUnavailable && (
            <p className={styles.message}>Done for today</p>
          )}
        {(!late || showNightDay) &&
          (rows.length > 0 ||
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
                      <h2 id="today-plan">
                        Your day <small>{rows.length} things</small>
                      </h2>
                    </div>
                    <ol className={styles.list}>
                      {displayRows.map((row) => {
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
                            data-gap={row.id.startsWith("gap:")}
                            data-past={past}
                            data-current={current}
                            data-shift={row.detail === "Shift"}
                          >
                            <Link href={row.href} className={styles.rowTop}>
                              <time dateTime={row.start}>
                                {studentClock(row.start, zone)}
                              </time>
                              <span
                                className="nx-dot"
                                style={
                                  {
                                    "--nx-course": row.courseId
                                      ? courseColor(row.courseId)
                                      : row.detail === "Shift"
                                        ? "var(--nx-warning)"
                                        : undefined,
                                  } as CSSProperties
                                }
                              />
                              <span className={styles.rowText}>
                                <strong className={styles.rowTitle}>
                                  {row.title}
                                </strong>
                                {row.detail && (
                                  <span className={styles.rowDetail}>
                                    {row.detail}
                                    {row.end &&
                                      ` · ${studentClock(row.start, zone)} – ${studentClock(row.end, zone)}`}
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
                            {current &&
                              row.study?.status === "planned" &&
                              !data?.plan?.needs_review &&
                              (() => {
                                const block = row.study;
                                const task = data?.tasks.find(
                                  (task) => task.id === block?.task_id,
                                );
                                return (
                                  task &&
                                  block && (
                                    <TodayStudyActions
                                      onMoved={() => void load()}
                                      key={`${row.study.id}:${row.study.revision}:${task.revision}`}
                                      block={block}
                                      task={task}
                                      accountKey={accountKey}
                                      timezone={zone}
                                      now={now}
                                      onSaved={(result) => {
                                        setReceipt({
                                          result,
                                          task,
                                          block,
                                          savedAt: Date.now(),
                                        });
                                        void load();
                                      }}
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
                {openings.data && !openings.data.calendar_checked && (
                  <p className={styles.gapNotice}>
                    Connect your calendar to verify free-time suggestions.{" "}
                    <Link href="/connections">Connections</Link>
                  </p>
                )}
                {openings.data && !openings.data.routine_confirmed && (
                  <p className={styles.gapNotice}>
                    Review your sleep and routine before planning free time.{" "}
                    <Link href="/settings#routine-alarm">Review routine</Link>
                  </p>
                )}
                {openings.error && (
                  <p className={styles.gapNotice}>
                    Free time couldn’t be checked. Your saved schedule is still
                    shown.
                  </p>
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
                  <section className={`${styles.section} ${styles.sideCard}`}>
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
                              <small>
                                {
                                  data?.timetable?.courses.find(
                                    (c) => c.id === item.courseId,
                                  )?.code
                                }
                              </small>
                            </span>
                            <span
                              className={styles.dueChip}
                              data-urgent={
                                Date.parse(item.due) - now <= 2 * 86400000
                              }
                            >
                              {relativeTime(item.due, now, zone)}
                            </span>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                {replies.length > 0 && (
                  <section className={`${styles.section} ${styles.sideCard}`}>
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
                            <span className={styles.draftReply}>
                              Draft reply
                            </span>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
                {goals.length > 0 && (
                  <section className={styles.section}>
                    <div className={styles.heading}>
                      <h2>This week’s goals</h2>
                    </div>
                    <div className={styles.goalList}>
                      {goals.map((goal) => (
                        <Link
                          key={goal.id}
                          className={styles.goal}
                          href="/goals"
                        >
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
                          {goal.tracking === "count" &&
                            goal.progress.reported_count !== null &&
                            goal.progress.target !== null && (
                              <progress
                                aria-label={goal.title}
                                value={Math.min(
                                  goal.progress.reported_count,
                                  goal.progress.target,
                                )}
                                max={goal.progress.target}
                              />
                            )}
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
