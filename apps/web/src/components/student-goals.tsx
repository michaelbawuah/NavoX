"use client";

import Link from "next/link";
import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useId,
  useState,
  useSyncExternalStore,
} from "react";
import {
  type GoalCategory,
  type GoalDefinition,
  type GoalHandle,
  type GoalReportDraft,
  type GoalStatus,
  type GoalsPlannerMetrics,
  goalShiftDay,
  goalToday,
  observeGoalsPrivacy,
  type StudentGoal,
  StudentGoalsController,
} from "../lib/student-goals";
import { studentDayLabel } from "../lib/student-schedule";
import styles from "./student-goals.module.css";

const categories: Record<GoalCategory, string> = {
  academic: "Academic",
  habit: "Habit",
  personal: "Personal",
};
function period(start: string, end: string) {
  return start === end
    ? studentDayLabel(start)
    : `${studentDayLabel(start)} – ${studentDayLabel(end)}`;
}
export function StudentGoalsCard() {
  return (
    <section className={styles.entry}>
      <p className={styles.eyebrow}>Make room for what matters</p>
      <h2>Your goals, at your pace</h2>
      <p>
        Keep up to three active goals. Check in when you choose; an unreported
        day stays unknown.
      </p>
      <Link href="/goals">Review my goals →</Link>
    </section>
  );
}
export function GoalProgressSummary({ goal }: { goal: StudentGoal }) {
  const progress = goal.progress;
  return (
    <div className={styles.progress}>
      <p className={styles.hint}>
        {goal.cadence === "daily"
          ? "Daily period"
          : goal.cadence === "weekly"
            ? "Weekly period"
            : "Goal period"}{" "}
        · {period(progress.period_start, progress.period_end)} · both dates
        included
      </p>
      {goal.tracking === "count" ? (
        <>
          <p className={styles.stat}>
            {progress.reported_count === null ? (
              "No count reported"
            ) : (
              <>
                <strong>{progress.reported_count}</strong> of {progress.target}{" "}
                {goal.unit}
              </>
            )}
          </p>
          <p>
            {progress.reported_count === null
              ? "There are no reports for this period. Progress is unknown."
              : progress.target_met_from_reports
                ? "Your reported counts meet this period’s target."
                : `${progress.remaining_count ?? "Unknown"} ${goal.unit} remain toward this period’s target, based on your reports.`}
          </p>
        </>
      ) : (
        <>
          <p className={styles.stat}>
            {progress.qualitative_latest === "yes"
              ? "Latest report: Yes"
              : progress.qualitative_latest === "no"
                ? "Latest report: No"
                : "No report in this period"}
          </p>
          <p>
            {progress.qualitative_latest === "unknown"
              ? "Progress is unknown until you check in."
              : progress.latest_reported_on
                ? `Reported for ${studentDayLabel(progress.latest_reported_on)}. A yes or no describes only the date you chose.`
                : "Your own report; no activity was automatically observed."}
          </p>
        </>
      )}
      <p className={styles.hint}>
        {progress.days_reported} {progress.days_reported === 1 ? "day" : "days"}{" "}
        reported · Self-reported · {progress.timezone.replaceAll("_", " ")}
        {goal.cadence === "weekly" ? " · Weeks start Monday" : ""}
      </p>
    </div>
  );
}

export function GoalDefinitionForm({
  goal,
  today,
  disabled,
  onSave,
  onCancel,
}: {
  goal?: StudentGoal;
  today: string;
  disabled: boolean;
  onSave: (definition: GoalDefinition, status: GoalStatus) => Promise<void>;
  onCancel: () => void;
}) {
  const prefix = useId();
  const [title, setTitle] = useState(goal?.title ?? ""),
    [description, setDescription] = useState(goal?.description ?? ""),
    [category, setCategory] = useState<GoalCategory>(
      goal?.category ?? "academic",
    ),
    [tracking, setTracking] = useState(goal?.tracking ?? "qualitative"),
    [cadence, setCadence] = useState(goal?.cadence ?? "weekly"),
    [target, setTarget] = useState(goal?.target?.toString() ?? ""),
    [unit, setUnit] = useState(goal?.unit ?? ""),
    [start, setStart] = useState(goal?.starts_on ?? today),
    [end, setEnd] = useState(goal?.ends_on ?? ""),
    [status, setStatus] = useState<GoalStatus>(goal?.status ?? "active"),
    [error, setError] = useState("");
  const locked = goal?.tracking_definition_locked ?? false;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      const count = tracking === "count" ? Number(target) : null;
      if (
        count !== null &&
        (!Number.isInteger(count) || count < 1 || count > 100000)
      )
        throw new Error("Choose a whole-number target from 1 to 100,000.");
      if (end && (end < start || end > goalShiftDay(start, 366)))
        throw new Error(
          "Choose an end date from the start date through 366 days later.",
        );
      await onSave(
        {
          title: title.trim(),
          description: description.trim(),
          category,
          tracking,
          cadence,
          target: count,
          unit: tracking === "count" ? unit.trim() : null,
          starts_on: start,
          ends_on: end || null,
        },
        status,
      );
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Check the goal details.",
      );
    }
  }
  return (
    <section
      className={styles.card}
      aria-label={goal ? "Edit saved goal" : "Create a goal"}
    >
      <div className={styles.heading}>
        <h2>{goal ? "Edit your goal" : "A goal worth keeping in view"}</h2>
        <button type="button" onClick={onCancel}>
          Close editor
        </button>
      </div>
      <form onSubmit={submit} autoComplete="off">
        <fieldset disabled={disabled} className={styles.fields}>
          <label htmlFor={`${prefix}-title`}>
            What matters to you?
            <input
              id={`${prefix}-title`}
              value={title}
              maxLength={160}
              required
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Write a goal in your own words"
            />
          </label>
          <label htmlFor={`${prefix}-description`}>
            A little context (optional)
            <textarea
              id={`${prefix}-description`}
              rows={3}
              value={description}
              maxLength={1000}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <div className={styles.twoColumns}>
            <label>
              Category
              <select
                value={category}
                onChange={(event) =>
                  setCategory(event.target.value as GoalCategory)
                }
              >
                {Object.entries(categories).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            {goal && (
              <label>
                State you choose
                <select
                  value={status}
                  onChange={(event) =>
                    setStatus(event.target.value as GoalStatus)
                  }
                >
                  <option value="active">Active</option>
                  <option value="paused">Paused</option>
                  <option value="completed">Completed</option>
                </select>
              </label>
            )}
          </div>
          {locked && (
            <p className={styles.inset}>
              This goal has check-in history. Its measurement, target, frequency
              and dates stay fixed so older reports keep their meaning. You can
              edit its wording, category or state.
            </p>
          )}
          <fieldset disabled={locked} className={styles.fields}>
            <legend>How you want to check in</legend>
            <div className={styles.twoColumns}>
              <label>
                Measurement
                <select
                  value={tracking}
                  onChange={(event) =>
                    setTracking(event.target.value as StudentGoal["tracking"])
                  }
                >
                  <option value="qualitative">
                    A yes or no for a chosen date
                  </option>
                  <option value="count">A count for a chosen date</option>
                </select>
              </label>
              <label>
                Target period
                <select
                  value={cadence}
                  onChange={(event) =>
                    setCadence(event.target.value as StudentGoal["cadence"])
                  }
                >
                  <option value="daily">Each day</option>
                  <option value="weekly">Each week · Monday–Sunday</option>
                  <option value="total">Across the goal period</option>
                </select>
              </label>
            </div>
            {tracking === "count" && (
              <div className={styles.twoColumns}>
                <label>
                  Target for that period
                  <input
                    type="number"
                    min={1}
                    max={100000}
                    step={1}
                    required
                    value={target}
                    onChange={(event) => setTarget(event.target.value)}
                  />
                </label>
                <label>
                  What you count
                  <input
                    maxLength={40}
                    required
                    value={unit}
                    onChange={(event) => setUnit(event.target.value)}
                    placeholder="Choose your own unit"
                  />
                </label>
              </div>
            )}
            <div className={styles.twoColumns}>
              <label>
                Start date · included
                <input
                  type="date"
                  value={start}
                  required
                  onChange={(event) => setStart(event.target.value)}
                />
              </label>
              <label>
                End date · included (optional)
                <input
                  type="date"
                  value={end}
                  min={start}
                  max={start ? goalShiftDay(start, 366) : undefined}
                  onChange={(event) => setEnd(event.target.value)}
                />
              </label>
            </div>
          </fieldset>
          {error && (
            <p role="alert" className={styles.warning}>
              {error}
            </p>
          )}
          <p className={styles.hint}>
            Your goal does not change your timetable or study plan. All
            check-ins are your reports.
          </p>
          <button type="submit" className={styles.primary}>
            {goal ? "Save goal changes" : "Create my goal"}
          </button>
        </fieldset>
      </form>
    </section>
  );
}

export function GoalReportEditor({
  goal,
  report,
  today,
  disabled,
  onSave,
  onErase,
}: {
  goal: StudentGoal;
  report: GoalReportDraft;
  today: string;
  disabled: boolean;
  onSave: (value: {
    count: number | null;
    completed: boolean | null;
    note: string;
  }) => Promise<void>;
  onErase: (confirmed: boolean) => void;
}) {
  const entry = report.entry;
  const [count, setCount] = useState(entry?.count?.toString() ?? ""),
    [answer, setAnswer] = useState(
      entry?.completed === true
        ? "yes"
        : entry?.completed === false
          ? "no"
          : "",
    ),
    [note, setNote] = useState(entry?.note ?? ""),
    [erase, setErase] = useState(false),
    [error, setError] = useState("");
  const writable =
    goal.status === "active" &&
    report.on_date <= today &&
    report.on_date >= goalShiftDay(today, -31) &&
    report.on_date >= goal.starts_on &&
    (!goal.ends_on || report.on_date <= goal.ends_on);
  const radioId = useId();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      if (!writable) return;
      if (goal.tracking === "qualitative" && !answer)
        throw new Error("Choose Yes or No for this date.");
      if (goal.tracking === "count" && count === "")
        throw new Error(
          "Enter the count for this date, including zero if that is your report.",
        );
      await onSave({
        count: goal.tracking === "count" ? Number(count) : null,
        completed: goal.tracking === "qualitative" ? answer === "yes" : null,
        note,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Check your report.");
    }
  }
  return (
    <section className={styles.card} aria-label="Review a dated self-report">
      <p className={styles.eyebrow}>
        Your report · {studentDayLabel(report.on_date)}
      </p>
      <h2>{goal.title}</h2>
      <p>
        Local date: <strong>{report.on_date}</strong> · Current saved time zone:{" "}
        <strong>{report.timezone.replaceAll("_", " ")}</strong>
      </p>
      {entry && (
        <p className={styles.warning}>
          A report already exists for this date. Saving replaces its count or
          yes/no and note; it does not add a second report.
        </p>
      )}
      {entry && entry.reported_timezone !== report.timezone && (
        <p className={styles.hint}>
          The original report used {entry.reported_timezone}. An edited report
          is stamped with your current saved time zone above.
        </p>
      )}
      {!writable && (
        <p className={styles.inset}>
          This report is read-only. New or replacement reports require an active
          goal and a date within its period, from today back through 31 days
          ago. You can still erase a saved report.
        </p>
      )}
      <form onSubmit={submit} autoComplete="off">
        <fieldset disabled={disabled || !writable} className={styles.fields}>
          {goal.tracking === "count" ? (
            <label>
              Absolute count for this local day · {goal.unit}
              <input
                type="number"
                min={0}
                max={100000}
                step={1}
                required
                value={count}
                onChange={(event) => setCount(event.target.value)}
              />
              <span className={styles.hint}>
                Enter the whole day’s count, including zero. This is not an
                amount to add.
              </span>
            </label>
          ) : (
            <fieldset className={styles.answers}>
              <legend>For this date, did you meet your goal?</legend>
              <label>
                <input
                  type="radio"
                  name={radioId}
                  required
                  value="yes"
                  checked={answer === "yes"}
                  onChange={() => setAnswer("yes")}
                />
                Yes
              </label>
              <label>
                <input
                  type="radio"
                  name={radioId}
                  required
                  value="no"
                  checked={answer === "no"}
                  onChange={() => setAnswer("no")}
                />
                No
              </label>
            </fieldset>
          )}
          <label>
            A private note (optional)
            <textarea
              rows={3}
              maxLength={1000}
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          {error && (
            <p role="alert" className={styles.warning}>
              {error}
            </p>
          )}
          <p className={styles.hint}>
            This records what you report. NavoX has not observed attendance,
            sleep, grades or other activity.
          </p>
          <button className={styles.primary} type="submit">
            {entry ? "Replace this day’s report" : "Save this day’s report"}
          </button>
        </fieldset>
      </form>
      {entry && (
        <details className={styles.privacy}>
          <summary>Erase this report</summary>
          <label className={styles.check}>
            <input
              type="checkbox"
              disabled={disabled}
              checked={erase}
              onChange={(event) => setErase(event.target.checked)}
            />
            Permanently erase this date’s report and private note. It will
            become unknown, not a “No”.
          </label>
          <button
            type="button"
            disabled={disabled || !erase}
            onClick={() => onErase(erase)}
          >
            Erase this check-in
          </button>
        </details>
      )}
    </section>
  );
}

export function GoalRecordedStudy({
  metrics,
}: {
  metrics: GoalsPlannerMetrics;
}) {
  const recorded =
    metrics.completed_blocks +
      metrics.skipped_blocks +
      metrics.recorded_minutes >
    0;
  return (
    <section
      className={styles.card}
      aria-label="Separately recorded study outcomes"
    >
      <p className={styles.eyebrow}>A separate record</p>
      <h2>Study outcomes you recorded</h2>
      <p className={styles.hint}>
        {period(metrics.period_start, metrics.period_end)} · dates included ·{" "}
        {metrics.timezone.replaceAll("_", " ")}
      </p>
      {recorded ? (
        <dl className={styles.metrics}>
          <div>
            <dt>Completed study blocks</dt>
            <dd>{metrics.completed_blocks}</dd>
          </div>
          <div>
            <dt>Skipped study blocks</dt>
            <dd>{metrics.skipped_blocks}</dd>
          </div>
          <div>
            <dt>Recorded study minutes</dt>
            <dd>{metrics.recorded_minutes}</dd>
          </div>
        </dl>
      ) : (
        <p>No study outcomes recorded for this period.</p>
      )}
      <p className={styles.muted}>
        These are recorded Planner outcomes, separate from your goal reports.
        They do not establish attendance, sleep, grades or completion of a
        personal goal.
      </p>
    </section>
  );
}
function GoalCard({
  goal,
  today,
  disabled,
  onEdit,
  onDay,
  onErase,
}: {
  goal: StudentGoal;
  today: string;
  disabled: boolean;
  onEdit: () => void;
  onDay: (date: string) => void;
  onErase: (confirmed: boolean) => void;
}) {
  const [date, setDate] = useState(today),
    [forget, setForget] = useState(false);
  return (
    <article className={styles.card}>
      <div className={styles.heading}>
        <p className={styles.eyebrow}>{categories[goal.category]}</p>
        <span className={styles.badge}>{goal.status} · chosen by you</span>
      </div>
      <h2>{goal.title}</h2>
      {goal.description && (
        <p className={styles.description}>{goal.description}</p>
      )}
      <GoalProgressSummary goal={goal} />
      <div className={styles.actions}>
        <button type="button" disabled={disabled} onClick={onEdit}>
          Edit goal
        </button>
        {goal.status === "active" && (
          <button
            className={styles.primary}
            type="button"
            disabled={
              disabled ||
              today < goal.starts_on ||
              (!!goal.ends_on && today > goal.ends_on)
            }
            onClick={() => onDay(today)}
          >
            Check in for today
          </button>
        )}
      </div>
      <details className={styles.history}>
        <summary>Reports & another date</summary>
        <p className={styles.hint}>
          The latest {goal.recent_checkins.length} saved check-ins are shown
          here. Choose a date to check older history. Saving a report replaces
          that date’s existing report.
        </p>
        {goal.recent_checkins.length === 0 ? (
          <p>No check-ins saved. Unreported days are unknown.</p>
        ) : (
          <ul className={styles.reports}>
            {goal.recent_checkins.map((entry) => (
              <li key={entry.id}>
                <div>
                  <strong>{studentDayLabel(entry.on_date)}</strong>
                  <span>
                    {entry.count !== null
                      ? `${entry.count} ${goal.unit}`
                      : entry.completed
                        ? "Yes"
                        : "No"}{" "}
                    · Self-reported ·{" "}
                    {entry.reported_timezone.replaceAll("_", " ")}
                  </span>
                </div>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onDay(entry.on_date)}
                >
                  Review
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className={styles.dateReview}>
          <label>
            Local date to review
            <input
              type="date"
              required
              value={date}
              min={goal.starts_on}
              max={goal.ends_on && goal.ends_on < today ? goal.ends_on : today}
              disabled={disabled}
              onChange={(event) => setDate(event.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={
              disabled ||
              !date ||
              date < goal.starts_on ||
              date > today ||
              (!!goal.ends_on && date > goal.ends_on)
            }
            onClick={() => onDay(date)}
          >
            Check this date
          </button>
        </div>
      </details>
      <details className={styles.privacy}>
        <summary>Forget this goal</summary>
        <label className={styles.check}>
          <input
            type="checkbox"
            disabled={disabled}
            checked={forget}
            onChange={(event) => setForget(event.target.checked)}
          />
          Permanently erase this goal, every check-in and their private notes.
          Recorded Planner outcomes stay separate.
        </label>
        <button
          type="button"
          disabled={disabled || !forget}
          onClick={() => onErase(forget)}
        >
          Erase goal and check-ins
        </button>
      </details>
    </article>
  );
}
function GoalPrivacy({
  handles,
  revision,
  disabled,
  onErase,
  onReset,
}: {
  handles: GoalHandle[];
  revision: number | null;
  disabled: boolean;
  onErase: (id: string, confirmed: boolean) => void;
  onReset: (confirmed: boolean) => void;
}) {
  const [selected, setSelected] = useState(""),
    [all, setAll] = useState(false);
  return (
    <details className={styles.card}>
      <summary>Saved goals & erasure controls</summary>
      <p className={styles.muted}>
        After clearing, only saved goal references remain here. Reload your
        goals to see their wording. Erasure is permanent.
      </p>
      {handles.length > 0 && (
        <>
          <label>
            Saved goal reference
            <select
              disabled={disabled}
              value={selected}
              onChange={(event) => setSelected(event.target.value)}
            >
              <option value="">Choose a reference to erase</option>
              {handles.map((goal, index) => (
                <option value={goal.id} key={goal.id}>
                  Saved goal {index + 1}
                </option>
              ))}
            </select>
          </label>
          <p className={styles.hint}>
            Erasing the selected goal removes all its check-ins and notes.
          </p>
          <button
            type="button"
            disabled={disabled || !selected}
            onClick={() => {
              onErase(selected, true);
              setSelected("");
            }}
          >
            Permanently erase selected goal
          </button>
        </>
      )}
      {revision !== null ? (
        <>
          <label className={styles.check}>
            <input
              type="checkbox"
              disabled={disabled}
              checked={all}
              onChange={(event) => setAll(event.target.checked)}
            />
            Permanently erase all my goals, check-ins and their private notes.
            Keep unrelated NavoX data and recorded study outcomes.
          </label>
          <button
            type="button"
            disabled={disabled || !all}
            onClick={() => {
              onReset(all);
              setAll(false);
            }}
          >
            Erase all goal data
          </button>
        </>
      ) : (
        <p>Reload saved goals before erasing all goal data.</p>
      )}
    </details>
  );
}
/** Keep the same mounted form tree while its account is being rechecked. */
export function GoalPrivateSurface({
  masked,
  children,
}: {
  masked: boolean;
  children?: ReactNode;
}) {
  return (
    <div className={styles.privateSurface} hidden={masked} inert={masked}>
      {children}
    </div>
  );
}
export function StudentGoals({ accountId }: { accountId: string }) {
  return <StudentGoalsSession key={accountId} accountId={accountId} />;
}
function StudentGoalsSession({ accountId }: { accountId: string }) {
  const [controller] = useState(() => new StudentGoalsController(accountId)),
    [editing, setEditing] = useState<string | null>(null),
    [creating, setCreating] = useState(false);
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
  useEffect(() => {
    const stop = observeGoalsPrivacy(
      () => {
        controller.clear();
        setEditing(null);
        setCreating(false);
      },
      {
        window,
        document,
        channel:
          typeof BroadcastChannel === "undefined"
            ? null
            : new BroadcastChannel("navox-account-session"),
      },
      { suspend: controller.suspend, resume: () => void controller.resume() },
    );
    void controller.load();
    return stop;
  }, [controller]);
  const board = state.board,
    disabled = state.busy || state.masked || !!state.pending || state.blocked,
    today = board ? goalToday(board) : "",
    active = board?.goals.filter((goal) => goal.status === "active") ?? [],
    other = board?.goals.filter((goal) => goal.status !== "active") ?? [],
    editGoal = board?.goals.find((goal) => goal.id === editing),
    reportGoal = board?.goals.find((goal) => goal.id === state.report?.goal_id);
  const cards = (goals: StudentGoal[]) =>
    goals.map((goal) => (
      <GoalCard
        key={`${goal.id}:${goal.revision}:${state.epoch}`}
        goal={goal}
        today={today}
        disabled={disabled}
        onEdit={() => {
          setEditing(goal.id);
          setCreating(false);
        }}
        onDay={(date) => {
          setEditing(null);
          setCreating(false);
          void controller.loadDay(goal.id, date);
        }}
        onErase={(confirmed) => void controller.eraseGoal(goal.id, confirmed)}
      />
    ));
  return (
    <div className={styles.panel} aria-busy={state.busy}>
      <header className={`${styles.card} ${styles.hero}`}>
        <div>
          <p className={styles.eyebrow}>Your goals, at your pace</p>
          <h1>Keep what matters in view.</h1>
          <p>
            Academic, habit or personal. Keep up to three active goals, or none
            at all.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            controller.clear();
            setEditing(null);
            setCreating(false);
          }}
        >
          Clear private text
        </button>
      </header>
      {state.masked && (
        <section className={styles.card} role="status">
          <h2>Private goals are hidden</h2>
          <p>
            Your unsaved wording stays in this tab’s memory while your account
            is checked. It is cleared if account access changes.
          </p>
          <button
            type="button"
            disabled={state.busy || state.blocked}
            onClick={() => void controller.resume()}
          >
            {state.busy ? "Checking your account…" : "Check account and return"}
          </button>
        </section>
      )}
      <GoalPrivateSurface masked={state.masked}>
        <p role="status" className={styles.notice}>
          {state.busy ? "Checking your saved goals…" : state.notice}
        </p>
        {state.error && (
          <p role="alert" className={styles.warning}>
            {state.error}
          </p>
        )}
        {state.pending ? (
          <section className={styles.card}>
            <h2>This change needs a result check</h2>
            <p>
              A response was lost. Check the same change with the same details
              before making another. Clearing this screen discards the pending
              details here; a saved change may remain on the server.
            </p>
            <button
              type="button"
              disabled={state.busy || state.blocked}
              onClick={() => void controller.checkPending()}
            >
              Check this same change
            </button>
          </section>
        ) : !board ? (
          <section className={styles.card}>
            <h2>Open your private goals</h2>
            <p>No reports are inferred while this screen is closed.</p>
            <button
              type="button"
              disabled={disabled}
              onClick={() => void controller.load()}
            >
              Reload saved goals
            </button>
          </section>
        ) : (
          <>
            <div className={styles.heading}>
              <p className={styles.muted}>
                {active.length} of 3 active · Today {studentDayLabel(today)} ·{" "}
                {board.timezone.replaceAll("_", " ")}
              </p>
              <button
                type="button"
                className={styles.primary}
                disabled={
                  disabled || active.length >= 3 || board.goals.length >= 30
                }
                onClick={() => {
                  setCreating(true);
                  setEditing(null);
                }}
              >
                Add a goal
              </button>
            </div>
            {active.length >= 3 && (
              <p className={styles.hint}>
                Three is the active limit. Pause or complete a goal before
                making another active.
              </p>
            )}
            {board.goals.length >= 30 && (
              <p className={styles.warning}>
                You have 30 saved goals. Erase one you no longer need before
                adding another.
              </p>
            )}
            {(creating || editGoal) && (
              <GoalDefinitionForm
                key={`${editing ?? "new"}:${state.epoch}`}
                goal={editGoal}
                today={today}
                disabled={disabled}
                onCancel={() => {
                  setCreating(false);
                  setEditing(null);
                }}
                onSave={(definition, status) =>
                  editGoal
                    ? controller.updateGoal(editGoal.id, definition, status)
                    : controller.create(definition)
                }
              />
            )}
            <section className={styles.goalGrid} aria-label="Active goals">
              {active.length ? (
                cards(active)
              ) : (
                <div className={styles.card}>
                  <h2>A little space is okay.</h2>
                  <p>
                    No active goals. Add one when it helps, or keep this space
                    empty.
                  </p>
                </div>
              )}
            </section>
            {state.report && reportGoal && (
              <GoalReportEditor
                key={`${state.report.goal_id}:${state.report.on_date}:${state.epoch}`}
                goal={reportGoal}
                report={state.report}
                today={today}
                disabled={disabled}
                onSave={controller.checkin}
                onErase={(confirmed) => void controller.eraseCheckin(confirmed)}
              />
            )}
            <p className={styles.hint}>
              Date ranges include their start and end. Weekly periods run
              Monday–Sunday. Reports can only fall within a goal’s start and end
              dates. Missing reports stay unknown.
            </p>
            {other.length > 0 && (
              <details className={styles.card}>
                <summary>Paused & completed goals ({other.length})</summary>
                <p className={styles.muted}>
                  State is chosen by you; target counts do not automatically
                  mark a goal completed.
                </p>
                <div className={styles.goalGrid}>{cards(other)}</div>
              </details>
            )}
            <GoalRecordedStudy metrics={board.planner_metrics} />
            {board.notices.length > 0 && (
              <details className={styles.card}>
                <summary>About these records</summary>
                {board.notices.map((notice) => (
                  <p key={notice}>{notice}</p>
                ))}
              </details>
            )}
          </>
        )}
        <GoalPrivacy
          key={state.epoch}
          handles={state.handles}
          revision={state.board_revision}
          disabled={disabled}
          onErase={(id, confirmed) => void controller.eraseGoal(id, confirmed)}
          onReset={(confirmed) => void controller.reset(confirmed)}
        />
      </GoalPrivateSurface>
    </div>
  );
}
