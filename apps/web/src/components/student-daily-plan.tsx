"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  type ProposedStudyBlock,
  type RoutinePreview,
  readRoutinePreview,
  readStudyPlan,
  readStudyPreferences,
  readStudyPreview,
  readStudyTask,
  readStudyTasks,
  routineRequest,
  type StudyAssignmentDraft,
  type StudyBlock,
  type StudyPlan,
  type StudyPlanPreview,
  type StudyPlanResult,
  type StudyPreferences,
  type StudyTask,
  type StudyTaskFields,
  type StudyTaskSave,
  type StudyWindow,
  studyDeadlineChoices,
  studyLocalDateTime,
  studyMinutes,
  studyPreferencesDraft,
  studyRequest,
  studyTaskDraft,
  studyTaskSourceAcknowledgement,
  unfittedStudyReasons,
} from "../lib/student-planning";
import {
  addStudentDays,
  readStudentTimetable,
  type StudentCourse,
  type StudentTimetable,
  studentClock,
  studentDateInZone,
  studentDayLabel,
  studentError,
  studentLocation,
  studentRequest,
  studentWeekdays,
  studentWriteUncertain,
} from "../lib/student-schedule";
import { parseStudyCapture } from "../lib/study-capture";
import { mondayOf } from "./classes-week";
import { PlannerOverview } from "./planner-overview";
import { PlannerWelcome } from "./planner-welcome";
import { RoutineChangeReview } from "./routine-change-review";
import styles from "./student-daily-plan.module.css";

type OutcomeKind = "done" | "took_longer" | "skipped";
export function StudyUnfittedWork({
  result,
  timezone,
}: {
  result: StudyPlanResult;
  timezone: string;
}) {
  return (
    <>
      {result.warnings.map((warning) => (
        <p key={warning} className={styles.warning}>
          {warning}
        </p>
      ))}
      {result.unfitted_work.length > 0 && (
        <div className={styles.warning}>
          <h3>Some work needs another plan.</h3>
          <p>
            These minutes don’t fit in the available time. NavoX hasn’t pushed
            work into your sleep hours.
          </p>
          {result.unfitted_work.map((row) => (
            <p key={row.task_id}>
              <strong>
                {row.title} · {studyMinutes(row.minutes)} left
              </strong>
              <br />
              {unfittedStudyReasons[row.reason]}
              {row.due_at &&
                ` · due ${studentDayLabel(studentDateInZone(row.due_at, timezone))} at ${studentClock(row.due_at, timezone)}`}
            </p>
          ))}
          <p>
            Adjust the effort estimate, protect a different time, or review the
            deadline with your instructor.
          </p>
        </div>
      )}
    </>
  );
}

export function StudyProposedBlocks({
  blocks,
  timezone,
}: {
  blocks: ProposedStudyBlock[];
  timezone: string;
}) {
  const days = [
    ...new Set(blocks.map((row) => studentDateInZone(row.start_at, timezone))),
  ].sort();
  return (
    <>
      {days.map((day) => (
        <section key={day}>
          <h3 className={styles.dayTitle}>{studentDayLabel(day)}</h3>
          <div className={styles.timeline}>
            {blocks
              .filter(
                (row) => studentDateInZone(row.start_at, timezone) === day,
              )
              .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at))
              .map((row) => (
                <article
                  className={styles.block}
                  key={`${row.task_id}:${row.start_at}`}
                >
                  <time dateTime={row.start_at}>
                    {studentClock(row.start_at, timezone)}
                    <span>{studentClock(row.end_at, timezone)}</span>
                  </time>
                  <div>
                    <h4>{row.title}</h4>
                    <p>
                      {studyMinutes(row.minutes)}
                      {row.focus_window && " · In your focus hours"}
                    </p>
                  </div>
                </article>
              ))}
          </div>
        </section>
      ))}
    </>
  );
}

export function StudentStudyPlanPreview({
  preview,
  timezone,
  busy,
  confirmed,
  onConfirm,
  onAccept,
  onClose,
}: {
  preview: StudyPlanPreview;
  timezone: string;
  busy: boolean;
  confirmed: boolean;
  onConfirm: (value: boolean) => void;
  onAccept: () => void;
  onClose: () => void;
}) {
  const result = preview.result;
  const planned = result.blocks.reduce((sum, row) => sum + row.minutes, 0),
    buffer = result.capacities.reduce(
      (sum, row) => sum + row.buffer_minutes,
      0,
    ),
    unfitted = result.unfitted_work.reduce((sum, row) => sum + row.minutes, 0);
  return (
    <section className={styles.card} aria-labelledby="student-study-preview">
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Preview · nothing changed yet</p>
          <h2 id="student-study-preview">Make the day feel possible.</h2>
          <p className={styles.muted}>
            Study time fits around your saved classes, shifts, walking
            estimates, sleep, and protected hours.
          </p>
        </div>
        <button
          type="button"
          disabled={busy}
          className={styles.quiet}
          onClick={onClose}
        >
          Close preview
        </button>
      </div>
      <div className={styles.metrics}>
        <div className={styles.metric}>
          <strong>{studyMinutes(planned)}</strong>Proposed study time
        </div>
        <div className={styles.metric}>
          <strong>{studyMinutes(buffer)}</strong>Unscheduled buffer
        </div>
        <div className={styles.metric}>
          <strong>{studyMinutes(unfitted)}</strong>Work that doesn’t fit
        </div>
      </div>
      <StudyUnfittedWork result={result} timezone={timezone} />
      {result.blocks.length ? (
        <StudyProposedBlocks blocks={result.blocks} timezone={timezone} />
      ) : (
        <p className={styles.empty}>
          No study blocks are proposed. Accepting this preview will clear future
          study blocks in this plan.
        </p>
      )}
      <details className={styles.disclosure}>
        <summary>Where the time goes</summary>
        <div className={styles.capacityList}>
          {result.capacities.map((row) => (
            <div key={row.day}>
              <span>{studentDayLabel(row.day)}</span>
              <span>
                {studyMinutes(row.free_minutes)} free ·{" "}
                {studyMinutes(row.planned_minutes)} planned ·{" "}
                {studyMinutes(row.buffer_minutes)} buffer
              </span>
            </div>
          ))}
        </div>
        <p className={styles.hint}>
          A 15% buffer stays free. Breaks between study blocks are additional.
        </p>
      </details>
      <div className={styles.form}>
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={confirmed}
            disabled={busy}
            onChange={(event) => onConfirm(event.target.checked)}
          />
          I reviewed this plan and want to replace my future study blocks in
          NavoX.
        </label>
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.primary}
            disabled={busy || !confirmed}
            onClick={onAccept}
          >
            {busy ? "Saving plan…" : "Use this plan"}
          </button>
          <span className={styles.hint}>
            No school or Google Calendar events are changed.
          </span>
        </div>
      </div>
    </section>
  );
}

export function StudentStudyAgenda({
  plan,
  tasks,
  timetable,
  disabled,
  onOutcome,
}: {
  plan: StudyPlan;
  tasks: StudyTask[];
  timetable: StudentTimetable;
  disabled: boolean;
  onOutcome: (block: StudyBlock, outcome: OutcomeKind) => void;
}) {
  const active = plan.blocks.filter(
    (row) =>
      (row.status === "planned" || row.status === "needs_review") &&
      studentDateInZone(row.start_at, timetable.timezone) >= timetable.date,
  );
  const earlier = plan.blocks.filter(
    (row) =>
      row.status === "planned" &&
      studentDateInZone(row.start_at, timetable.timezone) < timetable.date,
  );
  const days = [
    ...new Set([
      ...active.map((row) =>
        studentDateInZone(row.start_at, timetable.timezone),
      ),
      ...timetable.occurrences
        .filter((row) => !row.cancelled)
        .map((row) => studentDateInZone(row.start_at, timetable.timezone)),
      ...timetable.fixed_occurrences.map((row) =>
        studentDateInZone(row.start_at, timetable.timezone),
      ),
    ]),
  ].sort();
  if (!days.length && !earlier.length)
    return (
      <p className={styles.empty}>
        No study blocks or fixed commitments are saved for this window. Add a
        task and preview a plan when you’re ready.
      </p>
    );
  return (
    <>
      {days.map((day) => {
        const blocks = active.filter(
          (row) => studentDateInZone(row.start_at, timetable.timezone) === day,
        );
        const classes = timetable.occurrences.filter(
          (row) =>
            !row.cancelled &&
            studentDateInZone(row.start_at, timetable.timezone) === day,
        );
        const shifts = timetable.fixed_occurrences.filter(
          (row) => studentDateInZone(row.start_at, timetable.timezone) === day,
        );
        const events = [
          ...blocks.map((row) => ({
            id: row.id,
            start: row.start_at,
            end: row.end_at,
            title: row.title,
            subtitle: `${studyMinutes(row.minutes)}${row.focus_window ? " · Focus hours" : ""}`,
            kind: "study",
            block: row,
          })),
          ...classes.map((row) => ({
            id: row.id,
            start: row.start_at,
            end: row.end_at,
            title: row.course_code || row.course_name,
            subtitle: `${row.kind} · ${studentLocation(row)}`,
            kind: "class",
            block: null,
          })),
          ...shifts.map((row) => ({
            id: row.id,
            start: row.start_at,
            end: row.end_at,
            title: row.title,
            subtitle: "Work shift / fixed commitment",
            kind: "shift",
            block: null,
          })),
        ].sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
        return (
          <section key={day}>
            <h3 className={styles.dayTitle}>{studentDayLabel(day)}</h3>
            {day > addStudentDays(timetable.date, timetable.days - 1) && (
              <p className={styles.hint}>
                Only study blocks are shown beyond this timetable window. Open
                Classes to review fixed commitments for this date.
              </p>
            )}

            <div className={styles.timeline}>
              {events.map((event) => (
                <article
                  key={event.id}
                  className={[
                    styles.block,
                    event.kind !== "study" ? styles.fixed : "",
                    event.kind === "shift" ? styles.shift : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                >
                  <time dateTime={event.start}>
                    {studentClock(event.start, timetable.timezone)}
                    <span>{studentClock(event.end, timetable.timezone)}</span>
                  </time>
                  <div>
                    <h4>{event.title}</h4>
                    {event.block?.status === "needs_review" && (
                      <p className={styles.warning}>
                        Review needed — this saved block may not fit your
                        updated schedule.
                      </p>
                    )}
                    <p>{event.subtitle}</p>
                    {event.block && (
                      <>
                        <div className={styles.actions}>
                          <button
                            type="button"
                            disabled={
                              disabled ||
                              plan.needs_review ||
                              !tasks.some(
                                (task) => task.id === event.block?.task_id,
                              ) ||
                              Date.parse(event.start) >
                                Date.parse(timetable.server_now)
                            }
                            onClick={() =>
                              event.block && onOutcome(event.block, "done")
                            }
                          >
                            Done
                          </button>
                          <button
                            type="button"
                            disabled={
                              disabled ||
                              plan.needs_review ||
                              !tasks.some(
                                (task) => task.id === event.block?.task_id,
                              ) ||
                              Date.parse(event.start) >
                                Date.parse(timetable.server_now)
                            }
                            onClick={() =>
                              event.block &&
                              onOutcome(event.block, "took_longer")
                            }
                          >
                            Took longer
                          </button>
                          <button
                            type="button"
                            disabled={
                              disabled ||
                              plan.needs_review ||
                              !tasks.some(
                                (task) => task.id === event.block?.task_id,
                              )
                            }
                            onClick={() =>
                              event.block && onOutcome(event.block, "skipped")
                            }
                          >
                            Skip
                          </button>
                        </div>
                        {Date.parse(event.start) >
                          Date.parse(timetable.server_now) && (
                          <p className={styles.hint}>
                            Record study progress after this block starts.
                            Refresh to check the current time.
                          </p>
                        )}
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </section>
        );
      })}
      {earlier.length > 0 && (
        <details className={styles.disclosure}>
          <summary>Earlier blocks to check ({earlier.length})</summary>
          <p className={styles.hint}>
            These blocks have no recorded outcome. Review what happened; NavoX
            hasn’t marked them complete.
          </p>
          {earlier
            .slice(-20)
            .reverse()
            .map((block) => (
              <article className={styles.task} key={block.id}>
                <div>
                  <h4>{block.title}</h4>
                  <p>
                    {studentDayLabel(
                      studentDateInZone(block.start_at, timetable.timezone),
                    )}{" "}
                    · {studyMinutes(block.minutes)} planned
                  </p>
                </div>
                <div className={styles.actions}>
                  {(["done", "took_longer", "skipped"] as const).map((kind) => (
                    <button
                      key={kind}
                      type="button"
                      disabled={
                        disabled ||
                        plan.needs_review ||
                        !tasks.some((task) => task.id === block.task_id)
                      }
                      onClick={() => onOutcome(block, kind)}
                    >
                      {kind === "done"
                        ? "Done"
                        : kind === "took_longer"
                          ? "Took longer"
                          : "Skip"}
                    </button>
                  ))}
                </div>
              </article>
            ))}
          {earlier.length > 20 && (
            <p className={styles.hint}>
              Showing the 20 most recent unchecked blocks.
            </p>
          )}
        </details>
      )}
    </>
  );
}

export function StudentStudyTaskForm({
  task,
  assignment,
  captureTitle,
  captureNow,
  timezone,
  courses,
  disabled,
  onSave,
  onClose,
}: {
  task?: StudyTask;
  assignment?: StudyAssignmentDraft;
  captureTitle?: string;
  captureNow?: number;
  timezone: string;
  courses: StudentCourse[];
  disabled: boolean;
  onSave: (value: StudyTaskSave) => Promise<boolean>;
  onClose: () => void;
}) {
  const id = useId();
  const [capture] = useState(() =>
    !task && !assignment && captureTitle
      ? parseStudyCapture(captureTitle, courses, timezone, captureNow)
      : null,
  );
  const [value, setValue] = useState<StudyTaskFields>(
    task
      ? studyTaskDraft(task)
      : {
          title: assignment?.title ?? capture?.title ?? captureTitle ?? "",
          course_id: assignment?.course_id ?? capture?.course_id ?? null,
          due_at: assignment?.due_at ?? null,
          estimated_minutes: capture?.estimated_minutes ?? 60,
          remaining_minutes: capture?.estimated_minutes ?? 60,
          difficulty: 3,
          priority: 3,
          course_weight: 1,
          status: "active",
        },
  );
  const [deadline, setDeadline] = useState(
    value.due_at
      ? studyLocalDateTime(value.due_at, timezone)
      : (capture?.deadlineLocal ?? ""),
  );
  const [deadlineChoice, setDeadlineChoice] = useState(
    value.due_at ? new Date(value.due_at).toISOString() : "",
  );
  const [error, setError] = useState("");
  const [reviewedSource, setReviewedSource] = useState(false);
  let choices: string[] = [];
  try {
    if (deadline) choices = studyDeadlineChoices(deadline, timezone);
  } catch {}
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      let due: string | null = null;
      if (deadline) {
        const options = studyDeadlineChoices(deadline, timezone);
        if (!options.length)
          throw new Error(
            "That local time is skipped by a clock change. Choose another deadline time.",
          );
        if (options.length > 1 && !options.includes(deadlineChoice))
          throw new Error(
            "That time occurs twice during a clock change. Choose which occurrence you mean.",
          );
        due = options.length === 1 ? options[0] : deadlineChoice;
      }
      if (value.status === "active" && value.remaining_minutes === 0)
        throw new Error("Choose Completed when no work remains.");
      if (value.status === "completed" && value.remaining_minutes !== 0)
        throw new Error(
          "Set remaining work to zero before marking the task complete.",
        );
      if (
        await onSave({
          ...value,
          title: value.title.trim(),
          due_at: due,
          ...studyTaskSourceAcknowledgement(task, reviewedSource),
        })
      )
        onClose();
    } catch (cause) {
      setError(studentError(cause));
    }
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.heading}>
        <div>
          <h3>
            {assignment
              ? "Review this assignment as a study task"
              : task
                ? "Edit study task"
                : "Add something to work on"}
          </h3>
          <p className={styles.hint}>
            Your estimate is a starting point. Change it whenever you learn
            more.
          </p>
        </div>
        <button
          type="button"
          disabled={disabled}
          className={styles.quiet}
          onClick={onClose}
        >
          Cancel
        </button>
      </div>
      {capture && (
        <section className={styles.notice} aria-label="Review captured task">
          <strong>Check what NavoX understood</strong>
          <p>From: {captureTitle}</p>
          {capture.notes.map((note) => (
            <p key={note}>{note}</p>
          ))}
          <p>Nothing is saved until you confirm the details below.</p>
        </section>
      )}
      {task?.source_needs_review && (
        <div className={styles.notice}>
          <h4>Review the school calendar update</h4>
          <p>
            {task.source_review_notice ??
              "The assignment behind this task has changed. Review it before planning more work."}
          </p>
          {task.source_reviewable && task.source_current_revision ? (
            <>
              <p>
                Current title: {task.source_current_title ?? "Not supplied"}.{" "}
                {task.source_current_due_at
                  ? `Current deadline: ${studyLocalDateTime(task.source_current_due_at, timezone).replace("T", " ")} · ${timezone}`
                  : "No current deadline supplied."}
              </p>
              <button
                type="button"
                disabled={disabled}
                onClick={() => {
                  setValue({
                    ...value,
                    title: task.source_current_title ?? value.title,
                    due_at: task.source_current_due_at,
                  });
                  setDeadline(
                    task.source_current_due_at
                      ? studyLocalDateTime(task.source_current_due_at, timezone)
                      : "",
                  );
                  setDeadlineChoice(
                    task.source_current_due_at
                      ? new Date(task.source_current_due_at).toISOString()
                      : "",
                  );
                }}
              >
                Use latest title and deadline
              </button>
              <label>
                <input
                  type="checkbox"
                  disabled={disabled}
                  checked={reviewedSource}
                  onChange={(event) => setReviewedSource(event.target.checked)}
                />{" "}
                I reviewed this update. Save my task details with this version
                of the assignment.
              </label>
            </>
          ) : (
            <p>
              Reconnect or review the calendar in Connections, or archive this
              task. You can still edit your own details; planning waits for a
              current source.
            </p>
          )}
        </div>
      )}
      <fieldset className={styles.fields} disabled={disabled}>
        <label className={styles.wide} htmlFor={`${id}-title`}>
          Task
          <input
            id={`${id}-title`}
            required
            maxLength={200}
            value={value.title}
            onChange={(event) =>
              setValue({ ...value, title: event.target.value })
            }
          />
        </label>
        <label htmlFor={`${id}-course`}>
          Course (optional)
          <select
            id={`${id}-course`}
            value={value.course_id ?? ""}
            onChange={(event) =>
              setValue({ ...value, course_id: event.target.value || null })
            }
          >
            <option value="">Personal / no course</option>
            {value.course_id &&
              !courses.some((course) => course.id === value.course_id) && (
                <option value={value.course_id} disabled>
                  Previously linked course — choose an active course
                </option>
              )}
            {courses.map((course) => (
              <option value={course.id} key={course.id}>
                {course.code ? `${course.code} · ` : ""}
                {course.name}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${id}-due`}>
          Deadline (optional) · {timezone.replaceAll("_", " ")}
          <input
            id={`${id}-due`}
            type="datetime-local"
            value={deadline}
            onChange={(event) => {
              setDeadline(event.target.value);
              setDeadlineChoice("");
            }}
          />
        </label>
        {choices.length > 1 && (
          <label className={styles.wide} htmlFor={`${id}-clock`}>
            This clock time happens twice
            <select
              id={`${id}-clock`}
              value={deadlineChoice}
              onChange={(event) => setDeadlineChoice(event.target.value)}
              required
            >
              <option value="">Choose the intended time</option>
              {choices.map((choice, index) => (
                <option key={choice} value={choice}>
                  {index === 0 ? "Earlier" : "Later"} occurrence · {choice}
                </option>
              ))}
            </select>
          </label>
        )}
        <label htmlFor={`${id}-estimate`}>
          Original estimate (minutes)
          <input
            id={`${id}-estimate`}
            type="number"
            min={1}
            max={10080}
            required
            value={value.estimated_minutes}
            onChange={(event) =>
              setValue({
                ...value,
                estimated_minutes: Number(event.target.value),
                ...(!task
                  ? { remaining_minutes: Number(event.target.value) }
                  : {}),
              })
            }
          />
        </label>
        <label htmlFor={`${id}-remaining`}>
          Work left (minutes)
          <input
            id={`${id}-remaining`}
            type="number"
            min={0}
            max={10080}
            required
            value={value.remaining_minutes}
            onChange={(event) =>
              setValue({
                ...value,
                remaining_minutes: Number(event.target.value),
              })
            }
          />
        </label>
        <label htmlFor={`${id}-difficulty`}>
          How much focus does it need?
          <select
            id={`${id}-difficulty`}
            value={value.difficulty}
            onChange={(event) =>
              setValue({ ...value, difficulty: Number(event.target.value) })
            }
          >
            <option value={1}>Light work</option>
            <option value={2}>A little focus</option>
            <option value={3}>Some focus</option>
            <option value={4}>Deep focus</option>
            <option value={5}>My hardest work</option>
          </select>
        </label>
        <label htmlFor={`${id}-priority`}>
          Priority
          <select
            id={`${id}-priority`}
            value={value.priority}
            onChange={(event) =>
              setValue({ ...value, priority: Number(event.target.value) })
            }
          >
            {[1, 2, 3, 4, 5].map((priority) => (
              <option key={priority} value={priority}>
                {["Low", "Lower", "Normal", "High", "Highest"][priority - 1]}
              </option>
            ))}
          </select>
        </label>
        {task && (
          <label htmlFor={`${id}-status`}>
            Status
            <select
              id={`${id}-status`}
              value={value.status}
              onChange={(event) =>
                setValue({
                  ...value,
                  status: event.target.value as StudyTaskFields["status"],
                })
              }
            >
              <option value="active">Active</option>
              <option value="completed">Completed</option>
              <option value="archived">Archived</option>
            </select>
          </label>
        )}
      </fieldset>
      <details className={styles.disclosure}>
        <summary>Course importance</summary>
        <label className={styles.hint} htmlFor={`${id}-weight`}>
          Weight from 1 to 10 (used alongside urgency and remaining work)
          <input
            id={`${id}-weight`}
            type="number"
            disabled={disabled}
            min={1}
            max={10}
            value={value.course_weight}
            onChange={(event) =>
              setValue({ ...value, course_weight: Number(event.target.value) })
            }
          />
        </label>
      </details>
      {assignment && (
        <p className={styles.notice}>
          This creates a separate, editable study task. It won’t submit work or
          change the original assignment.
        </p>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <div className={styles.actions}>
        <button className={styles.primary} type="submit" disabled={disabled}>
          {disabled
            ? "Saving…"
            : assignment
              ? "Add reviewed study task"
              : "Save task"}
        </button>
      </div>
    </form>
  );
}

function StudyWindows({
  label,
  values,
  onChange,
  disabled,
}: {
  label: string;
  values: { key: string; window: StudyWindow }[];
  onChange: (rows: { key: string; window: StudyWindow }[]) => void;
  disabled: boolean;
}) {
  const id = useId();
  const change = (key: string, window: StudyWindow) =>
    onChange(values.map((row) => (row.key === key ? { ...row, window } : row)));
  return (
    <section>
      <div className={styles.heading}>
        <h3>{label}</h3>
        <button
          type="button"
          disabled={disabled || values.length >= 14}
          onClick={() =>
            onChange([
              ...values,
              {
                key: crypto.randomUUID(),
                window: { weekdays: [], start_time: "", end_time: "" },
              },
            ])
          }
        >
          ＋ Add hours
        </button>
      </div>
      {values.map((row) => (
        <div className={styles.window} key={row.key}>
          <fieldset className={styles.days} disabled={disabled}>
            <legend>Days</legend>
            {studentWeekdays.map((day, index) => (
              <label key={day}>
                <input
                  type="checkbox"
                  checked={row.window.weekdays.includes(index)}
                  onChange={(event) =>
                    change(row.key, {
                      ...row.window,
                      weekdays: event.target.checked
                        ? [...row.window.weekdays, index].sort()
                        : row.window.weekdays.filter(
                            (value) => value !== index,
                          ),
                    })
                  }
                />
                {day}
              </label>
            ))}
          </fieldset>
          <fieldset className={styles.fields} disabled={disabled}>
            <label htmlFor={`${id}-${row.key}-start`}>
              From
              <input
                id={`${id}-${row.key}-start`}
                type="time"
                required
                value={row.window.start_time}
                onChange={(event) =>
                  change(row.key, {
                    ...row.window,
                    start_time: event.target.value,
                  })
                }
              />
            </label>
            <label htmlFor={`${id}-${row.key}-end`}>
              Until
              <input
                id={`${id}-${row.key}-end`}
                type="time"
                required
                value={row.window.end_time}
                onChange={(event) =>
                  change(row.key, {
                    ...row.window,
                    end_time: event.target.value,
                  })
                }
              />
            </label>
          </fieldset>
          <div>
            <button
              type="button"
              className={styles.quiet}
              disabled={disabled}
              onClick={() =>
                onChange(values.filter((item) => item.key !== row.key))
              }
            >
              Remove these hours
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}

function StudyPreferencesForm({
  preferences,
  disabled,
  onSave,
}: {
  preferences: StudyPreferences;
  disabled: boolean;
  onSave: (value: ReturnType<typeof studyPreferencesDraft>) => Promise<boolean>;
}) {
  const id = useId();
  const [value, setValue] = useState(studyPreferencesDraft(preferences));
  const [focus, setFocus] = useState(() =>
    preferences.focus_windows.map((window) => ({
      key: crypto.randomUUID(),
      window,
    })),
  );
  const [protectedHours, setProtectedHours] = useState(() =>
    preferences.never_schedule.map((window) => ({
      key: crypto.randomUUID(),
      window,
    })),
  );
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      value.wake_time === value.sleep_time ||
      [...focus, ...protectedHours].some(
        (row) =>
          !row.window.weekdays.length ||
          row.window.start_time === row.window.end_time,
      )
    ) {
      setError(
        "Choose different start and end times, and at least one day for each set of hours.",
      );
      return;
    }
    setError("");
    await onSave({
      ...value,
      focus_windows: focus.map((row) => row.window),
      never_schedule: protectedHours.map((row) => row.window),
    });
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <fieldset className={styles.fields} disabled={disabled}>
        <label htmlFor={`${id}-wake`}>
          My day starts
          <input
            id={`${id}-wake`}
            type="time"
            required
            value={value.wake_time}
            onChange={(event) =>
              setValue({ ...value, wake_time: event.target.value })
            }
          />
        </label>
        <label htmlFor={`${id}-sleep`}>
          Protect sleep from
          <input
            id={`${id}-sleep`}
            type="time"
            required
            value={value.sleep_time}
            onChange={(event) =>
              setValue({ ...value, sleep_time: event.target.value })
            }
          />
        </label>
        <label htmlFor={`${id}-max`}>
          Longest study block (minutes)
          <input
            id={`${id}-max`}
            type="number"
            min={15}
            max={90}
            required
            value={value.max_study_minutes}
            onChange={(event) =>
              setValue({
                ...value,
                max_study_minutes: Number(event.target.value),
              })
            }
          />
        </label>
        <label htmlFor={`${id}-break`}>
          Break between blocks (minutes)
          <input
            id={`${id}-break`}
            type="number"
            min={5}
            max={60}
            required
            value={value.break_minutes}
            onChange={(event) =>
              setValue({ ...value, break_minutes: Number(event.target.value) })
            }
          />
        </label>
      </fieldset>
      <p className={styles.hint}>
        {preferences.timezone.replaceAll("_", " ")} · Overnight hours are
        supported. These are planning preferences, not a phone alarm.
      </p>
      <StudyWindows
        label="My best focus hours"
        values={focus}
        onChange={setFocus}
        disabled={disabled}
      />
      <StudyWindows
        label="Never schedule study here"
        values={protectedHours}
        onChange={setProtectedHours}
        disabled={disabled}
      />
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <div className={styles.actions}>
        <button className={styles.primary} disabled={disabled} type="submit">
          Save my rhythm
        </button>
      </div>
    </form>
  );
}

function StudentDailyPlanAccount({ accountKey }: { accountKey: string }) {
  const [captureTitle, setCaptureTitle] = useState("");
  const [routineReview, setRoutineReview] = useState<{
    body: ReturnType<typeof studyPreferencesDraft> & { request_id: string };
    preview: RoutinePreview;
  } | null>(null);
  const [tasks, setTasks] = useState<StudyTask[]>([]),
    [preferences, setPreferences] = useState<StudyPreferences | null>(null),
    [plan, setPlan] = useState<StudyPlan | null>(null),
    [timetable, setTimetable] = useState<StudentTimetable | null>(null);
  const [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [tab, setTab] = useState<"plan" | "tasks" | "rhythm">("plan"),
    [taskEditor, setTaskEditor] = useState<StudyTask | "new" | null>(null),
    [preview, setPreview] = useState<StudyPlanPreview | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [days] = useState(7),
    [undo, setUndo] = useState<{ outcomeId: string; revision: number } | null>(
      null,
    ),
    [lastResult, setLastResult] = useState<StudyPlanResult | null>(null),
    [epoch, setEpoch] = useState(0);
  const generation = useRef(0),
    alive = useRef(true),
    operation = useRef(false),
    acceptId = useRef("");
  const load = useCallback(async (reset = false) => {
    const request = ++generation.current;
    setLoading(true);
    try {
      const [nextTasks, nextPreferences, nextPlan, nextTimetable] =
        await Promise.all([
          studyRequest("/tasks", readStudyTasks),
          studyRequest("/preferences", readStudyPreferences),
          studyRequest("/plan", readStudyPlan),
          studentRequest("/timetable?days=14", readStudentTimetable),
        ]);
      if (request !== generation.current) return false;
      setTasks(nextTasks);
      setPreferences(nextPreferences);
      setPlan(nextPlan);
      const aligned = mondayOf(nextTimetable.date);
      const week =
        nextTimetable.date === aligned
          ? nextTimetable
          : await studentRequest(
              `/timetable?days=14&date=${aligned}`,
              readStudentTimetable,
            );
      if (request !== generation.current) return false;
      setTimetable(week);
      // A Today link opens a review form, never a write from a URL.
      const intent = new URLSearchParams(window.location.hash.slice(1));
      const block = nextPlan.blocks.find(
        (item) => item.id === intent.get("block") && item.status === "planned",
      );
      const kind = intent.get("outcome");
      if (
        !reset &&
        block &&
        !nextPlan.needs_review &&
        (kind === "done" || kind === "took_longer")
      ) {
        setTab("plan");
        setNotice(
          `Choose ${kind === "done" ? "Done" : "Took longer"} below to review every affected change.`,
        );
        window.history.replaceState(
          window.history.state,
          "",
          window.location.pathname + window.location.search,
        );
      } else if (!reset && block && kind === "skipped") {
        setTab("plan");
        setNotice(`Review ${block.title} below, then choose Skip to confirm.`);
        window.history.replaceState(
          window.history.state,
          "",
          window.location.pathname + window.location.search,
        );
      } else if (!reset && window.location.hash === "#add-task") {
        setTab("tasks");
        setTaskEditor("new");
        window.history.replaceState(
          window.history.state,
          "",
          window.location.pathname + window.location.search,
        );
      }
      setError("");
      setUndo((current) =>
        !nextPlan.needs_review && current?.revision === nextPlan.revision
          ? current
          : null,
      );
      if (nextPlan.needs_review) setLastResult(null);
      if (reset) {
        setUncertain(false);
        setTaskEditor(null);
        setPreview(null);
        setConfirmed(false);
        setLastResult(null);
        setUndo(null);
        setEpoch((value) => value + 1);
        setNotice(
          "Saved tasks and plan reloaded. Review them before making another change.",
        );
      }
      return true;
    } catch (cause) {
      if (request === generation.current) setError(studentError(cause));
      return false;
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
      ++generation.current;
    };
  }, [load]);
  async function mutate<T>(
    path: string,
    parse: (value: unknown) => T,
    method: string,
    body: unknown,
  ): Promise<T | null> {
    if (operation.current || uncertain) return null;
    operation.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    setPreview(null);
    setConfirmed(false);
    setUndo(null);
    setLastResult(null);
    try {
      const result = await studyRequest(path, parse, method, body);
      if (!alive.current) return null;
      if (!(await load())) {
        setUncertain(true);
        setNotice(
          "The change was saved, but the latest plan could not be checked. Reload before editing again.",
        );
      }
      return result;
    } catch (cause) {
      if (alive.current) {
        setError(studentError(cause));
        if (studentWriteUncertain(cause)) setUncertain(true);
      }
      return null;
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const locked = busy || loading || uncertain || !!routineReview;
  async function reviewRoutine(
    value: ReturnType<typeof studyPreferencesDraft>,
  ): Promise<boolean> {
    if (operation.current || locked) return false;
    operation.current = true;
    setBusy(true);
    setError("");
    const body = { ...value, request_id: crypto.randomUUID() };
    try {
      const preview = await routineRequest(
        accountKey,
        "/preferences/preview",
        readRoutinePreview,
        "POST",
        body,
      );
      if (alive.current) setRoutineReview({ body, preview });
    } catch (cause) {
      if (alive.current) setError(studentError(cause));
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
    return false;
  }
  async function confirmRoutine() {
    if (operation.current || !routineReview) return;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      await routineRequest(
        accountKey,
        "/preferences/confirm",
        readStudyPreferences,
        "POST",
        {
          ...routineReview.body,
          preview_hash: routineReview.preview.preview_hash,
        },
      );
      if (!alive.current) return;
      setRoutineReview(null);
      if (!(await load())) setUncertain(true);
      setNotice(
        "Routine saved. Review a new study plan if your blocks changed.",
      );
    } catch (cause) {
      if (alive.current) setError(studentError(cause));
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function previewPlan() {
    if (operation.current || locked) return;
    operation.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    setPreview(null);
    setConfirmed(false);
    try {
      const value = await studyRequest("/preview", readStudyPreview, "POST", {
        days,
      });
      if (alive.current) {
        setPreview(value);
        acceptId.current = crypto.randomUUID();
      }
    } catch (cause) {
      if (alive.current) setError(studentError(cause));
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  return (
    <div className={tab === "plan" ? styles.planRoot : styles.panel}>
      {error && (
        <p className={styles.error} role="alert">
          {error}
          {!uncertain && (
            <button
              type="button"
              disabled={busy || loading}
              onClick={() => void load()}
            >
              Reload
            </button>
          )}
        </p>
      )}
      {uncertain && (
        <div className={styles.warning} role="alert">
          <strong>Check what saved before trying again.</strong>
          <p>
            Changes are paused while their result is uncertain. Reload saved
            tasks and blocks to avoid applying the same progress twice.
          </p>
          <button
            type="button"
            disabled={busy || loading}
            onClick={() => void load(true)}
          >
            Reload saved plan
          </button>
        </div>
      )}
      {notice && (
        <div className={styles.notice} role="status">
          {notice}
          {undo && !plan?.needs_review && plan?.revision === undo.revision && (
            <div className={styles.actions}>
              <button
                type="button"
                disabled={locked}
                onClick={async () => {
                  const result = await mutate("/undo", readStudyPlan, "POST", {
                    request_id: crypto.randomUUID(),
                    outcome_request_id: undo.outcomeId,
                    revision: undo.revision,
                  });
                  if (result) {
                    setLastResult(null);
                    setNotice(
                      "The last progress update and its replan were undone.",
                    );
                  }
                }}
              >
                Undo this update
              </button>
              <span className={styles.hint}>
                Available for 5 minutes if your plan hasn’t changed.
              </span>
            </div>
          )}
        </div>
      )}
      {!preferences || !plan || !timetable ? (
        <section className={styles.card} aria-busy={loading}>
          <p className={styles.muted}>
            {loading
              ? "Opening your saved tasks and study plan…"
              : "The study plan could not be opened. Your existing timetable is still available in Classes."}
          </p>
        </section>
      ) : (
        <>
          <section
            className={tab === "plan" ? styles.planCanvas : styles.card}
            aria-busy={loading}
          >
            {tab !== "plan" && (
              <nav className={styles.tabs} aria-label="Study planning views">
                {[
                  ["plan", "My plan"],
                  [
                    "tasks",
                    `Tasks · ${tasks.filter((task) => task.status === "active").length}`,
                  ],
                  ["rhythm", "My rhythm"],
                ].map(([value, label]) => (
                  <button
                    type="button"
                    key={value}
                    aria-pressed={tab === value}
                    onClick={() => setTab(value as typeof tab)}
                  >
                    {label}
                  </button>
                ))}
              </nav>
            )}
            {tab === "plan" &&
              (tasks.length === 0 ? (
                <PlannerWelcome
                  key={preferences.revision}
                  preferences={preferences}
                  hasClasses={timetable.courses.some((c) => !c.archived)}
                  disabled={locked}
                  onCapture={(text) => {
                    setCaptureTitle(text);
                    setTaskEditor("new");
                    setTab("tasks");
                  }}
                  onRoutine={reviewRoutine}
                />
              ) : (
                <PlannerOverview
                  plan={plan}
                  tasks={tasks}
                  timetable={timetable}
                  accountKey={accountKey}
                  disabled={locked}
                  onAdd={() => {
                    setCaptureTitle("");
                    setTaskEditor("new");
                    setTab("tasks");
                  }}
                  onEdit={(task) => {
                    setTaskEditor(task);
                    setTab("tasks");
                  }}
                  onRoutine={() => setTab("rhythm")}
                  onReplan={() => void previewPlan()}
                  onReload={() => void load()}
                />
              ))}
            {tab === "tasks" && (
              <>
                <div className={styles.heading} style={{ marginTop: "1.2rem" }}>
                  <div>
                    <h3>What’s on your mind?</h3>
                    <p className={styles.muted}>
                      Add the work and a rough effort estimate. The plan will
                      show what fits.
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={locked}
                    onClick={() => setTaskEditor("new")}
                  >
                    ＋ Add task
                  </button>
                </div>
                {taskEditor && (
                  <StudentStudyTaskForm
                    key={`${taskEditor === "new" ? "new" : taskEditor.id}-${epoch}`}
                    task={taskEditor === "new" ? undefined : taskEditor}
                    captureTitle={captureTitle}
                    captureNow={Date.parse(timetable.server_now)}
                    timezone={preferences.timezone}
                    courses={timetable.courses.filter(
                      (course) => !course.archived,
                    )}
                    disabled={locked}
                    onClose={() => setTaskEditor(null)}
                    onSave={async (value) => {
                      const result = await mutate(
                        taskEditor !== "new"
                          ? `/tasks/${taskEditor.id}`
                          : "/tasks",
                        readStudyTask,
                        taskEditor !== "new" ? "PATCH" : "POST",
                        {
                          ...value,
                          request_id: crypto.randomUUID(),
                          ...(taskEditor !== "new"
                            ? { revision: taskEditor.revision }
                            : {}),
                        },
                      );
                      if (result)
                        setNotice(
                          "Study task saved. Preview a plan to arrange your next steps.",
                        );
                      return !!result;
                    }}
                  />
                )}
                <div className={styles.taskList}>
                  {tasks
                    .filter((task) => task.status !== "archived")
                    .map((task) => (
                      <article className={styles.task} key={task.id}>
                        <div>
                          <h4>
                            {task.title}
                            {task.status === "completed" && (
                              <span className={styles.badge}>Completed</span>
                            )}
                          </h4>
                          <p>
                            {studyMinutes(task.remaining_minutes)} left ·{" "}
                            {studyMinutes(task.actual_minutes)} studied
                            {task.course_id &&
                              ` · ${timetable.courses.find((course) => course.id === task.course_id)?.code || "Linked course"}`}
                          </p>
                          <p>
                            {task.due_at
                              ? `Due ${studentDayLabel(studentDateInZone(task.due_at, preferences.timezone))} at ${studentClock(task.due_at, preferences.timezone)}`
                              : "No deadline saved"}
                            {task.source_item_id &&
                              " · Reviewed from a school calendar"}
                          </p>
                          {task.source_needs_review && (
                            <p className={styles.error}>
                              {task.source_review_notice ??
                                "School calendar update needs review. Edit this task to continue planning."}
                            </p>
                          )}
                        </div>
                        <div className={styles.actions}>
                          <button
                            type="button"
                            disabled={locked}
                            onClick={() => setTaskEditor(task)}
                          >
                            Edit
                          </button>
                          <button
                            type="button"
                            disabled={locked}
                            className={styles.quiet}
                            onClick={async () => {
                              const result = await mutate(
                                `/tasks/${task.id}`,
                                readStudyTask,
                                "PATCH",
                                {
                                  ...studyTaskDraft(task),
                                  status: "archived",
                                  request_id: crypto.randomUUID(),
                                  revision: task.revision,
                                },
                              );
                              if (result) setNotice("Task archived.");
                            }}
                          >
                            Archive
                          </button>
                        </div>
                      </article>
                    ))}
                </div>
                {tasks.length === 0 && (
                  <p className={styles.empty}>
                    Start with one task. Your next lab, reading, or errand is
                    enough.
                  </p>
                )}
                <details className={styles.disclosure}>
                  <summary>
                    Archived tasks (
                    {tasks.filter((task) => task.status === "archived").length})
                  </summary>
                  {tasks
                    .filter((task) => task.status === "archived")
                    .map((task) => (
                      <div className={styles.task} key={task.id}>
                        <h4>{task.title}</h4>
                        <div className={styles.actions}>
                          <button
                            type="button"
                            disabled={locked}
                            onClick={() => setTaskEditor(task)}
                          >
                            Edit / restore
                          </button>
                          <button
                            type="button"
                            className={styles.danger}
                            disabled={locked}
                            onClick={async () => {
                              if (operation.current || uncertain) return;
                              operation.current = true;
                              setBusy(true);
                              setError("");
                              setUndo(null);
                              try {
                                await studyRequest(
                                  `/tasks/${task.id}?revision=${task.revision}&request_id=${crypto.randomUUID()}`,
                                  () => null,
                                  "DELETE",
                                );
                                setTasks((current) =>
                                  current.filter((row) => row.id !== task.id),
                                );
                                setPlan((current) =>
                                  current
                                    ? {
                                        ...current,
                                        blocks: current.blocks.filter(
                                          (block) => block.task_id !== task.id,
                                        ),
                                      }
                                    : current,
                                );
                                setTaskEditor(null);
                                setPreview(null);
                                setLastResult(null);

                                if (!(await load())) {
                                  setUncertain(true);
                                } else
                                  setNotice(
                                    "Task and its saved plan copies forgotten.",
                                  );
                              } catch (cause) {
                                setError(studentError(cause));
                                if (studentWriteUncertain(cause))
                                  setUncertain(true);
                              } finally {
                                operation.current = false;
                                setBusy(false);
                              }
                            }}
                          >
                            Forget task
                          </button>
                        </div>
                      </div>
                    ))}
                  <p className={styles.hint}>
                    Forgetting permanently removes this task and its study-block
                    copies.
                  </p>
                </details>
              </>
            )}
            {tab === "rhythm" && (
              <>
                <div style={{ marginTop: "1.2rem" }}>
                  <h3>Make room for your own rhythm.</h3>
                  <p className={styles.muted}>
                    Protect sleep, meals, and time off. Give your hardest work
                    the hours you focus best.
                  </p>
                </div>
                <StudyPreferencesForm
                  key={`${preferences.revision}-${epoch}`}
                  preferences={preferences}
                  disabled={locked}
                  onSave={reviewRoutine}
                />
              </>
            )}
          </section>
          {routineReview && (
            <RoutineChangeReview
              preview={routineReview.preview}
              draft={{ ...routineReview.body, timezone: preferences.timezone }}
              busy={busy || loading}
              onApprove={() => void confirmRoutine()}
              onCancel={() => setRoutineReview(null)}
            />
          )}
          {preview && (
            <StudentStudyPlanPreview
              preview={preview}
              timezone={preferences.timezone}
              busy={locked}
              confirmed={confirmed}
              onConfirm={setConfirmed}
              onClose={() => {
                setPreview(null);
                setConfirmed(false);
              }}
              onAccept={async () => {
                if (!confirmed) return;
                const result = await mutate("/accept", readStudyPlan, "POST", {
                  preview_id: preview.preview_id,
                  preview_hash: preview.preview_hash,
                  request_id: acceptId.current,
                  confirmed: true,
                });
                if (result) {
                  setLastResult(preview.result);
                  setNotice(
                    "Your study plan is saved. Start with the next small step.",
                  );
                }
              }}
            />
          )}{" "}
          {lastResult && !preview && (
            <section className={styles.card}>
              <StudyUnfittedWork
                result={lastResult}
                timezone={preferences.timezone}
              />
              <p className={styles.hint}>
                Based on the latest saved plan update. New tasks or changed
                deadlines need a fresh preview.
              </p>
            </section>
          )}
        </>
      )}
    </div>
  );
}
export function StudentDailyPlan({ accountId }: { accountId: string }) {
  return <StudentDailyPlanAccount key={accountId} accountKey={accountId} />;
}
