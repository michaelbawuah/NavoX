"use client";
import { useEffect, useRef, useState } from "react";
import {
  routineRequest,
  type StudyPlan,
  type StudyPreferences,
  type StudyTask,
} from "../lib/student-planning";
import {
  studentClock,
  studentDateInZone,
  studentDayLabel,
} from "../lib/student-schedule";
import {
  readStudyPlacement,
  type StudyPlacement,
  type StudyWindowIntent,
} from "../lib/study-placement";
import { StudyBlockMove } from "./study-block-move";
import styles from "./today-study-actions.module.css";

export function StudyWindowPlacement({
  intent,
  tasks,
  plan,
  preferences,
  disabled,
  creating,
  onAddTask,
  onClose,
  onSaved,
  onReplan,
}: {
  intent: StudyWindowIntent;
  tasks: StudyTask[];
  plan: StudyPlan;
  preferences: StudyPreferences;
  disabled: boolean;
  creating: boolean;
  onAddTask: () => void;
  onClose: () => void;
  onSaved: (result?: StudyPlacement) => void;
  onReplan?: () => void;
}) {
  const [taskId, setTaskId] = useState(intent.task_id ?? "");
  const [start, setStart] = useState(() =>
    new Date(
      Math.ceil(
        Math.max(Date.parse(intent.start_at), Date.now() + 60000) / 300000,
      ) * 300000,
    ).toISOString(),
  );
  const [minutes, setMinutes] = useState(
    Math.min(
      60,
      preferences.max_study_minutes,
      Math.floor(
        ((Date.parse(intent.end_at) - Date.parse(start)) / 60000) * 0.85,
      ),
    ),
  );
  const [preview, setPreview] = useState<StudyPlacement | null>(null);
  const [moveOpen, setMoveOpen] = useState(false);
  const [recoveryAccepted, setRecoveryAccepted] = useState(false);
  const [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false),
    [notice, setNotice] = useState("");
  const request = useRef(crypto.randomUUID()),
    alive = useRef(true),
    operation = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const task = tasks.find(
    (row) => row.id === taskId && row.status === "active",
  );
  const eligible = tasks.filter(
    (row) =>
      row.status === "active" &&
      row.remaining_minutes > 0 &&
      !row.source_needs_review,
  );
  const gapMinutes = Math.floor(
    (Date.parse(intent.end_at) - Date.parse(start)) / 60000,
  );
  const existing =
    !plan.needs_review && task
      ? plan.blocks
          .filter(
            (row) =>
              row.task_id === task.id &&
              row.status === "planned" &&
              Date.parse(row.start_at) > Date.now() &&
              row.minutes <= gapMinutes &&
              (!task.due_at ||
                Date.parse(start) + row.minutes * 60000 <=
                  Date.parse(task.due_at)),
          )
          .sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at))[0]
      : undefined;
  const unallocated = task
    ? Math.max(
        0,
        task.remaining_minutes -
          plan.blocks
            .filter(
              (row) =>
                row.task_id === task.id &&
                ["planned", "needs_review"].includes(row.status),
            )
            .reduce((total, row) => total + row.minutes, 0),
      )
    : 0;
  const amount = Math.min(minutes, unallocated, preferences.max_study_minutes);
  const valid =
    intent.accountKey.length > 0 &&
    intent.timezone === preferences.timezone &&
    gapMinutes > 0;
  function change() {
    setPreview(null);
    setRecoveryAccepted(false);
    setNotice("");
    request.current = crypto.randomUUID();
  }
  async function submit(confirm: boolean) {
    if (!task || !valid || operation.current || disabled || amount < 1) return;
    if (
      confirm &&
      (!preview || (preview.recovery_required && !recoveryAccepted))
    )
      return;
    operation.current = true;
    setBusy(true);
    setNotice("");
    try {
      const value = await routineRequest(
        intent.accountKey,
        `/placements/${confirm ? "confirm" : "preview"}`,
        readStudyPlacement,
        "POST",
        {
          request_id: request.current,
          task_id: task.id,
          task_revision: task.revision,
          timezone: intent.timezone,
          start_at: start,
          window_end_at: intent.end_at,
          minutes: amount,
          ...(confirm && preview
            ? {
                preview_hash: preview.preview_hash,
                confirmed: true,
                recovery_acknowledged: recoveryAccepted,
              }
            : {}),
        },
      );
      if (!alive.current) return;
      if (
        value.after.task_id !== task.id ||
        Date.parse(value.after.start_at) !== Date.parse(start) ||
        value.after.minutes !== amount
      )
        throw Error("This review did not match the selected task and time.");
      if (confirm) onSaved(value);
      else {
        setPreview(value);
        setRecoveryAccepted(false);
      }
    } catch (error) {
      if (alive.current) {
        if (confirm) setUncertain(true);
        setNotice(
          confirm
            ? "Couldn’t confirm the saved block. Retry this approval to check the same request. Closing does not undo a block that may have saved."
            : error instanceof Error
              ? error.message
              : "This time could not be checked. Try again.",
        );
      }
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  return (
    <section
      className={styles.form}
      aria-label="Plan this free window"
      data-planner-review
    >
      <h3>Study in your selected free window</h3>
      <p>
        {studentDayLabel(studentDateInZone(intent.start_at, intent.timezone))} ·{" "}
        {studentClock(intent.start_at, intent.timezone)}–
        {studentClock(intent.end_at, intent.timezone)}
      </p>
      <p>
        The calendar, classes, routine, deadline and study buffer are checked
        again before approval.
      </p>
      {plan.needs_review && onReplan && (
        <p>
          Your earlier plan needs review. You can review the full updated plan
          while keeping this selected window.
          <button
            type="button"
            disabled={disabled || busy || moveOpen || !!preview || creating}
            onClick={onReplan}
          >
            Review full plan and keep this window
          </button>
        </p>
      )}
      {creating ? (
        <p>
          Add and approve the task below. This window will stay selected for the
          placement review.
        </p>
      ) : (
        <>
          <label>
            What would you like to work on?
            <select
              value={taskId}
              disabled={disabled || busy || moveOpen || !!preview}
              onChange={(event) => {
                change();
                setTaskId(event.target.value);
              }}
            >
              <option value="">Choose a task</option>
              {eligible.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.title}
                </option>
              ))}
            </select>
          </label>
          {!preview && (
            <button
              type="button"
              disabled={disabled || busy || moveOpen}
              onClick={onAddTask}
            >
              Add new work for this window
            </button>
          )}
          {valid && existing ? (
            <>
              <p>
                You already have a {existing.minutes}-minute block for this
                task. Review moving it to {studentClock(start, intent.timezone)}
                .
              </p>
              <StudyBlockMove
                key={`${existing.id}:${existing.revision}:${start}`}
                block={existing}
                accountKey={intent.accountKey}
                timezone={intent.timezone}
                now={Date.now()}
                suggestedStart={start}
                requireCalendar
                windowEndAt={intent.end_at}
                onReviewChange={setMoveOpen}
                onSaved={() => onSaved()}
              />
            </>
          ) : (
            task && (
              <>
                <p>
                  {unallocated} minutes of this task are not yet allocated to a
                  study block.
                </p>
                {!preview ? (
                  <>
                    <p>Start at {studentClock(start, intent.timezone)}.</p>
                    <label>
                      Minutes in this block
                      <input
                        type="number"
                        min={Math.min(15, unallocated) || 1}
                        max={Math.min(
                          90,
                          unallocated,
                          preferences.max_study_minutes,
                          gapMinutes,
                        )}
                        value={amount}
                        disabled={disabled || busy}
                        onChange={(event) => {
                          change();
                          setMinutes(Number(event.target.value));
                        }}
                      />
                    </label>
                    <button
                      type="button"
                      disabled={disabled || busy || !valid || amount < 1}
                      onClick={() => void submit(false)}
                    >
                      Review placement
                    </button>
                    <button
                      type="button"
                      disabled={disabled || busy}
                      onClick={() => {
                        change();
                        setStart(
                          new Date(
                            Math.ceil(
                              Math.max(
                                Date.parse(intent.start_at),
                                Date.now() + 60000,
                              ) / 300000,
                            ) * 300000,
                          ).toISOString(),
                        );
                      }}
                    >
                      Update proposed start
                    </button>
                  </>
                ) : (
                  <>
                    <p>
                      Add {preview.after.title} ·{" "}
                      {studentClock(preview.after.start_at, intent.timezone)}–
                      {studentClock(preview.after.end_at, intent.timezone)} ·{" "}
                      {preview.after.minutes} minutes.
                    </p>
                    <p>
                      {preview.remaining_unplanned_minutes} minutes of this task
                      will still need a plan. Other study blocks, alarms and
                      calendar events stay as they are.
                    </p>
                    {preview.recovery_required && (
                      <div role="note">
                        <p>
                          The earlier plan was retired when your tasks or
                          routine changed. This starts a new plan with this
                          block and the preserved blocks listed below. Retired
                          blocks are not restored.
                        </p>
                        {preview.preserved_blocks.length ? (
                          <ul>
                            {preview.preserved_blocks.map((row) => (
                              <li key={row.id}>
                                {row.title} ·{" "}
                                {studentClock(row.start_at, intent.timezone)}–
                                {studentClock(row.end_at, intent.timezone)}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p>No other upcoming study blocks are preserved.</p>
                        )}
                        {preview.unplanned_tasks.length ? (
                          <>
                            <p>Work still needing a plan after approval:</p>
                            <ul>
                              {preview.unplanned_tasks.map((row) => (
                                <li key={row.task_id}>
                                  {row.title} · {row.minutes} minutes
                                </li>
                              ))}
                            </ul>
                          </>
                        ) : (
                          <p>
                            All current work will be allocated in the listed
                            blocks.
                          </p>
                        )}
                        <label>
                          <input
                            type="checkbox"
                            checked={recoveryAccepted}
                            disabled={busy || uncertain}
                            onChange={(event) =>
                              setRecoveryAccepted(event.target.checked)
                            }
                          />
                          I understand that any remaining tasks still need a
                          plan and want to start with these listed blocks.
                        </label>
                      </div>
                    )}
                    {preview.warnings.map((message) => (
                      <p key={message}>{message}</p>
                    ))}
                    <button
                      type="button"
                      disabled={
                        busy ||
                        disabled ||
                        (preview.recovery_required && !recoveryAccepted)
                      }
                      onClick={() => void submit(true)}
                    >
                      {busy
                        ? "Checking…"
                        : uncertain
                          ? "Retry this approval"
                          : preview.recovery_required
                            ? "Start this reviewed plan"
                            : "Approve placement"}
                    </button>
                  </>
                )}
              </>
            )
          )}
          {!valid && (
            <p role="alert">
              This window or timezone is no longer current. Choose a fresh free
              window from Today.
            </p>
          )}
        </>
      )}
      {notice && <p role="status">{notice}</p>}
      <button
        type="button"
        disabled={busy || disabled || moveOpen}
        onClick={onClose}
      >
        {uncertain ? "Close review" : "Cancel placement"}
      </button>
    </section>
  );
}
