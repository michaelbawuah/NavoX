"use client";

import Link from "next/link";
import {
  type FormEvent,
  useEffect,
  useId,
  useState,
  useSyncExternalStore,
} from "react";
import {
  observeStudentEmailPrivacy,
  StudentEmailController,
  type StudentEmailCorrection,
  type StudentEmailHandle,
  type StudentEmailReview,
  type StudentEmailSource,
  studentEmailCategories,
  studentEmailDeadline,
  studentEmailReadyForTask,
} from "../lib/student-email";
import {
  studyDeadlineChoices,
  studyLocalDateTime,
} from "../lib/student-planning";
import {
  type StudentCourse,
  studentClock,
  studentDateInZone,
  studentDayLabel,
} from "../lib/student-schedule";
import styles from "./student-email-actions.module.css";

function when(value: string, timezone: string) {
  return `${studentDayLabel(studentDateInZone(value, timezone))} at ${studentClock(value, timezone)}`;
}
export function StudentEmailCard() {
  return (
    <section className={styles.entry}>
      <div>
        <p className={styles.eyebrow}>One message, one next step</p>
        <h2>Turn an email into a clear action</h2>
        <p>
          Select a saved email, review its evidence, then decide what to keep.
        </p>
      </div>
      <Link href="/email-actions">
        Review an email <span aria-hidden="true">→</span>
      </Link>
    </section>
  );
}
export function StudentEmailEvidence({
  review,
  timezone,
}: {
  review: StudentEmailReview;
  timezone: string;
}) {
  return (
    <section
      className={styles.card}
      aria-label="Suggested action and email evidence"
    >
      <p className={styles.eyebrow}>2 · Check the suggestion</p>
      <div className={styles.heading}>
        <h2>{studentEmailCategories[review.classification.category]}</h2>
        <span className={styles.badge}>Suggestion · review required</span>
      </div>
      <p className={styles.muted}>
        Visible wording supports this suggestion. It does not verify the sender
        or decide which course this belongs to.
      </p>
      {review.classification.reasons.length > 0 && (
        <ul>
          {review.classification.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {review.classification.evidence.length > 0 ? (
        <div className={styles.evidence}>
          {review.classification.evidence.map((item) => (
            <figure key={`${item.source}:${item.start}:${item.end}`}>
              <figcaption>
                Exact {item.source === "subject" ? "subject" : "message"}{" "}
                excerpt · characters {item.start + 1}–{item.end}
              </figcaption>
              <blockquote>{item.quote}</blockquote>
            </figure>
          ))}
        </div>
      ) : (
        <p>No exact supporting excerpt was saved.</p>
      )}
      {review.content_truncated && (
        <p className={styles.warning}>
          Only part of this message was reviewed. Check the original email; a
          deadline has not been inferred from an incomplete message.
        </p>
      )}
      {review.classification.ambiguities.length > 0 && (
        <div className={styles.warning}>
          <strong>Timing needs your review</strong>
          <ul>
            {review.classification.ambiguities.map((value) => (
              <li key={value}>{value}</li>
            ))}
          </ul>
        </div>
      )}
      <p>
        {review.classification.proposed_deadline ? (
          <>
            Suggested deadline:{" "}
            <strong>
              {when(review.classification.proposed_deadline, timezone)}
            </strong>{" "}
            · {timezone}. Confirm it against the original email.
          </>
        ) : (
          "No unambiguous deadline was extracted. Choose a verified date, or explicitly save without a deadline."
        )}
      </p>
      <p className={styles.hint}>
        Excerpts and corrections expire after 20 minutes. Clearing this screen
        removes its private text here; erase a saved review below to remove its
        server copy.
      </p>
    </section>
  );
}

export function StudentEmailCorrectionEditor({
  review,
  courses,
  timezone,
  disabled,
  onSave,
  onNotImportant,
}: {
  review: StudentEmailReview;
  courses: StudentCourse[];
  timezone: string;
  disabled: boolean;
  onSave: (value: StudentEmailCorrection) => Promise<void>;
  onNotImportant: () => void;
}) {
  const prefix = useId(),
    initial = review.correction;
  const [category, setCategory] = useState(initial.category),
    [title, setTitle] = useState(initial.title ?? ""),
    [course, setCourse] = useState(
      initial.course_id ?? (initial.confirmed_course ? "personal" : ""),
    ),
    [courseConfirmed, setCourseConfirmed] = useState(initial.confirmed_course),
    [deadlineMode, setDeadlineMode] = useState(
      initial.due_at ? "date" : initial.confirmed_deadline ? "none" : "",
    ),
    [local, setLocal] = useState(
      initial.due_at ? studyLocalDateTime(initial.due_at, timezone) : "",
    ),
    [deadlineChoice, setDeadlineChoice] = useState(""),
    [deadlineEdited, setDeadlineEdited] = useState(false),
    [deadlineConfirmed, setDeadlineConfirmed] = useState(
      initial.confirmed_deadline,
    ),
    [minutes, setMinutes] = useState(
      initial.estimated_minutes?.toString() ?? "",
    ),
    [error, setError] = useState("");
  let choices: string[] = [];
  try {
    if (local) choices = studyDeadlineChoices(local, timezone);
  } catch {}
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      const due =
        deadlineMode === "date"
          ? !deadlineEdited && initial.due_at
            ? initial.due_at
            : studentEmailDeadline(local, timezone, deadlineChoice)
          : null;
      const effort = minutes === "" ? null : Number(minutes);
      if (
        effort !== null &&
        (!Number.isInteger(effort) || effort < 1 || effort > 10080)
      )
        throw new Error("Enter 1–10,080 minutes of remaining work.");
      await onSave({
        category,
        not_important: category === "ignore",
        title: title.trim() || null,
        course_id: course && course !== "personal" ? course : null,
        due_at: due,
        estimated_minutes: effort,
        confirmed_course: !!course && courseConfirmed,
        confirmed_deadline: !!deadlineMode && deadlineConfirmed,
      });
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Check your reviewed details.",
      );
    }
  }
  return (
    <section className={styles.card}>
      <p className={styles.eyebrow}>3 · Make it yours</p>
      <div className={styles.heading}>
        <h2>Review the details</h2>
        <button type="button" disabled={disabled} onClick={onNotImportant}>
          Not important
        </button>
      </div>
      <p className={styles.muted}>
        Corrections affect this review only. “Not important” does not train a
        mail filter or change the original email.
      </p>
      <form onSubmit={save} autoComplete="off">
        <fieldset disabled={disabled} className={styles.fields}>
          <div className={styles.twoColumns}>
            <label htmlFor={`${prefix}-category`}>
              Action
              <select
                id={`${prefix}-category`}
                value={category}
                onChange={(event) =>
                  setCategory(
                    event.target.value as StudentEmailCorrection["category"],
                  )
                }
              >
                {Object.entries(studentEmailCategories).map(
                  ([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ),
                )}
              </select>
            </label>
            <label htmlFor={`${prefix}-title`}>
              Task title
              <input
                id={`${prefix}-title`}
                maxLength={200}
                placeholder="What do you need to do?"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
          </div>
          <label htmlFor={`${prefix}-course`}>
            Which course is this for?
            <select
              id={`${prefix}-course`}
              value={course}
              onChange={(event) => {
                setCourse(event.target.value);
                setCourseConfirmed(false);
              }}
            >
              <option value="">Choose after reviewing the email</option>
              <option value="personal">Personal / no course</option>
              {courses
                .filter((value) => !value.archived)
                .map((value) => (
                  <option key={value.id} value={value.id}>
                    {value.code ? `${value.code} · ` : ""}
                    {value.name}
                  </option>
                ))}
            </select>
          </label>
          <label className={styles.check}>
            <input
              type="checkbox"
              disabled={!course}
              checked={courseConfirmed}
              onChange={(event) => setCourseConfirmed(event.target.checked)}
            />
            I confirm this course choice, including Personal / no course.
          </label>
          <div className={styles.twoColumns}>
            <label htmlFor={`${prefix}-deadline-mode`}>
              Deadline
              <select
                id={`${prefix}-deadline-mode`}
                value={deadlineMode}
                onChange={(event) => {
                  setDeadlineMode(event.target.value);
                  setDeadlineConfirmed(false);
                }}
              >
                <option value="">Review the deadline</option>
                <option value="date">Save a verified date and time</option>
                <option value="none">Save without a deadline</option>
              </select>
            </label>
            <label htmlFor={`${prefix}-minutes`}>
              {review.existing_task
                ? "Remaining work (minutes)"
                : "Estimated work (minutes)"}
              <input
                id={`${prefix}-minutes`}
                type="number"
                min={1}
                max={10080}
                step={1}
                value={minutes}
                placeholder="Your estimate"
                onChange={(event) => setMinutes(event.target.value)}
              />
            </label>
          </div>
          {deadlineMode === "date" && (
            <>
              <label htmlFor={`${prefix}-deadline`}>
                Deadline · {timezone.replaceAll("_", " ")}
                <input
                  id={`${prefix}-deadline`}
                  type="datetime-local"
                  required
                  value={local}
                  onChange={(event) => {
                    setLocal(event.target.value);
                    setDeadlineEdited(true);
                    setDeadlineChoice("");
                    setDeadlineConfirmed(false);
                  }}
                />
              </label>
              {!deadlineEdited && initial.due_at && (
                <p className={styles.hint}>
                  Saved exact time: {initial.due_at}. This time is preserved
                  unless you edit the field.
                </p>
              )}
              {choices.length > 1 && deadlineEdited && (
                <label>
                  That clock time happens twice
                  <select
                    required
                    value={deadlineChoice}
                    onChange={(event) => {
                      setDeadlineChoice(event.target.value);
                      setDeadlineConfirmed(false);
                    }}
                  >
                    <option value="">Choose an occurrence</option>
                    {choices.map((value, index) => (
                      <option key={value} value={value}>
                        {index === 0 ? "Earlier" : "Later"} occurrence · {value}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {local && choices.length === 0 && (
                <p className={styles.warning}>
                  This local time may be skipped by a clock change. Choose
                  another time.
                </p>
              )}
            </>
          )}
          <label className={styles.check}>
            <input
              type="checkbox"
              disabled={!deadlineMode}
              checked={deadlineConfirmed}
              onChange={(event) => setDeadlineConfirmed(event.target.checked)}
            />
            {deadlineMode === "none"
              ? "I confirm saving this task without a deadline."
              : "I checked the date, time and time zone against the original email."}
          </label>
          {error && (
            <p role="alert" className={styles.warning}>
              {error}
            </p>
          )}
          <button type="submit" className={styles.primary}>
            Save reviewed details
          </button>
        </fieldset>
      </form>
    </section>
  );
}

export function StudentEmailTaskActions({
  review,
  timezone,
  disabled,
  planningEnabled,
  accepted,
  taskUnavailable = false,
  onAccept,
  onCheck,
}: {
  review: StudentEmailReview;
  timezone: string;
  disabled: boolean;
  planningEnabled: boolean;
  accepted: boolean;
  taskUnavailable?: boolean;
  onAccept: (confirmed: boolean, refresh: boolean) => void;
  onCheck: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const existing = review.existing_task;
  const unavailableExisting = !!existing && existing.status !== "active";
  const saved = !!review.task_id || accepted;
  return (
    <section className={styles.card}>
      <p className={styles.eyebrow}>4 · Choose your next step</p>
      <h2>
        {saved
          ? accepted
            ? "Task saved"
            : "Task previously accepted"
          : existing
            ? "Review the linked task"
            : "Keep an action you can use"}
      </h2>
      {saved ? (
        <p>
          {accepted
            ? "The accepted task is in Planner. No email or calendar event was sent."
            : "This review already accepted a task. Open Planner to check its current availability; a new analysis is required to review changed details."}
        </p>
      ) : (
        <>
          {existing && (
            <div className={styles.inset}>
              <strong>{existing.title}</strong>
              <p>
                {existing.status} · {existing.actual_minutes} minutes already
                recorded · {existing.remaining_minutes} minutes remaining.
              </p>
              <p className={styles.hint}>
                This task came from this exact email. Refreshing keeps its
                recorded work and replaces its remaining effort, title, course
                and deadline with your reviewed details.
              </p>
            </div>
          )}
          <p>
            {review.correction.title ? (
              <>
                Reviewed title: <strong>{review.correction.title}</strong>
              </>
            ) : (
              "Save a task title and effort estimate above first."
            )}
          </p>
          <p>
            {review.correction.due_at
              ? `Reviewed deadline: ${when(review.correction.due_at, timezone)} · ${timezone}`
              : "No deadline saved."}
          </p>
          {!planningEnabled ? (
            <p className={styles.warning}>
              Saving a study task is not enabled on this deployment. You can
              still review this email and use its existing Inbox reply flow.
            </p>
          ) : taskUnavailable ? (
            <p className={styles.warning}>
              That accepted task is no longer available. Check the saved review
              before making another change.
            </p>
          ) : unavailableExisting ? (
            <p className={styles.warning}>
              This linked task is {existing?.status}. Review it in Planner
              before creating or refreshing work from this email.
            </p>
          ) : (
            <>
              <label className={styles.check}>
                <input
                  type="checkbox"
                  disabled={disabled || !studentEmailReadyForTask(review)}
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                {existing
                  ? "I confirm refreshing this exact linked task with the saved details."
                  : "I confirm creating a task from the saved details above."}
              </label>
              <button
                type="button"
                className={styles.primary}
                disabled={
                  disabled || !confirmed || !studentEmailReadyForTask(review)
                }
                onClick={() => onAccept(confirmed, !!existing)}
              >
                {existing ? "Refresh linked task" : "Create reviewed task"}
              </button>
            </>
          )}
        </>
      )}
      <div className={styles.actions}>
        {planningEnabled && <Link href="/planner">Open Planner →</Link>}
        <button type="button" disabled={disabled} onClick={onCheck}>
          Check saved review
        </button>
      </div>
      {review.reply_handoff ? (
        <div className={styles.inset}>
          <h3>Need to reply?</h3>
          <p>{review.reply_handoff.notice}</p>
          <Link href={review.reply_handoff.path}>
            Open existing Inbox reply flow →
          </Link>
          <p className={styles.hint}>
            Review the recipient and exact draft there. This screen does not
            send messages.
          </p>
        </div>
      ) : (
        <p className={styles.hint}>
          No existing Inbox reply flow is available for this saved source.
        </p>
      )}
    </section>
  );
}
function EmailErasure({
  handles,
  disabled,
  onOpen,
  onErase,
  onForget,
}: {
  handles: StudentEmailHandle[];
  disabled: boolean;
  onOpen: (id: string) => void;
  onErase: (handle: StudentEmailHandle) => void;
  onForget: (source: string, confirmed: boolean) => void;
}) {
  const [confirmed, setConfirmed] = useState("");
  return (
    <details className={styles.card}>
      <summary>Saved reviews & privacy controls ({handles.length})</summary>
      <p className={styles.muted}>
        Only saved review references remain here after clearing. Reopening
        checks your current account and source access; it does not read Gmail
        again.
      </p>
      {handles.length === 0 ? (
        <p>
          No known saved review references in this session. Unknown requests may
          still expire on the server after 20 minutes.
        </p>
      ) : (
        handles.map((handle, index) => (
          <div className={styles.inset} key={handle.review_id}>
            <h3>Saved review {index + 1}</h3>
            <div className={styles.actions}>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onOpen(handle.review_id)}
              >
                Reopen checked review
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onErase(handle)}
              >
                Erase review only
              </button>
            </div>
            <p className={styles.hint}>
              Erasing a review leaves any accepted task in Planner.
            </p>
            <label className={styles.check}>
              <input
                type="checkbox"
                disabled={disabled}
                checked={confirmed === handle.source_id}
                onChange={(event) =>
                  setConfirmed(event.target.checked ? handle.source_id : "")
                }
              />
              Permanently erase every review, email-derived task and its
              recorded study blocks from this email. Keep the original email,
              saved Inbox item and unrelated tasks.
            </label>
            <button
              type="button"
              disabled={disabled || confirmed !== handle.source_id}
              onClick={() => {
                onForget(handle.source_id, true);
                setConfirmed("");
              }}
            >
              Erase review and task copies
            </button>
          </div>
        ))
      )}
    </details>
  );
}
export function StudentEmailActions({
  accountId,
  planningEnabled = false,
}: {
  accountId: string;
  planningEnabled?: boolean;
}) {
  return (
    <StudentEmailSession
      key={accountId}
      accountId={accountId}
      planningEnabled={planningEnabled}
    />
  );
}
function StudentEmailSession({
  accountId,
  planningEnabled,
}: {
  accountId: string;
  planningEnabled: boolean;
}) {
  const [controller] = useState(() => new StudentEmailController(accountId));
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
  const queryId = useId();
  useEffect(
    () =>
      observeStudentEmailPrivacy(() => controller.clear(), {
        window,
        document,
        channel:
          typeof BroadcastChannel === "undefined"
            ? null
            : new BroadcastChannel("navox-account-session"),
      }),
    [controller],
  );
  const disabled = state.busy || !!state.pending || state.blocked;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void controller.search();
  };
  return (
    <div className={styles.panel} aria-busy={state.busy}>
      <header className={`${styles.card} ${styles.hero}`}>
        <div>
          <p className={styles.eyebrow}>Email → action</p>
          <h1>A useful next step, on your terms.</h1>
          <p>
            Select one saved email. Check its words, correct the details, and
            decide whether it becomes a task.
          </p>
        </div>
        <button type="button" onClick={() => controller.clear()}>
          Clear private text
        </button>
      </header>
      <p role="status" className={styles.notice}>
        {state.busy ? "Checking your selected email action…" : state.notice}
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
            A response was lost. Check the same request with the same details
            before making another change. This does not start another analysis.
          </p>
          <button
            type="button"
            disabled={state.busy || state.blocked}
            onClick={() => void controller.checkPending()}
          >
            Check this exact request
          </button>
          <p className={styles.hint}>
            Clearing discards the pending details from this screen. A saved
            review may still exist until it expires.
          </p>
        </section>
      ) : (
        <>
          <section className={styles.card}>
            <p className={styles.eyebrow}>1 · Choose one email</p>
            <h2>Search saved email labels</h2>
            <p className={styles.muted}>
              Search the email labels already saved in NavoX. This search does
              not open messages or scan your mailbox.
            </p>
            <form
              className={styles.search}
              onSubmit={submit}
              autoComplete="off"
            >
              <label htmlFor={queryId}>
                Saved subject, sender or Inbox item label
                <input
                  id={queryId}
                  type="search"
                  minLength={2}
                  maxLength={120}
                  required
                  disabled={disabled}
                  value={state.query}
                  onChange={(event) => controller.setQuery(event.target.value)}
                  placeholder="Words you recognize"
                  autoComplete="off"
                />
              </label>
              <button type="submit" disabled={disabled}>
                Search saved items
              </button>
            </form>
            {state.sources && (
              <div className={styles.results}>
                {state.sources.sources.length === 0 ? (
                  <p>
                    No matching authorized saved email labels. Check{" "}
                    <Link href="/inbox">Inbox</Link>, your{" "}
                    <Link href="/connections">connections</Link> and whether
                    NavoX is paused.
                  </p>
                ) : (
                  state.sources.sources.map((source) => (
                    <EmailSource
                      key={source.source_id}
                      source={source}
                      disabled={disabled}
                      onSelect={() => controller.select(source)}
                    />
                  ))
                )}
                {state.sources.limit_reached && (
                  <p className={styles.hint}>
                    More saved items may match. Refine your search.
                  </p>
                )}
              </div>
            )}
            {state.selected && (
              <div className={styles.inset}>
                <strong>
                  {state.selected.title ??
                    state.selected.display_label ??
                    "Saved email (subject unavailable)"}
                </strong>
                {!state.selected.title && state.selected.display_label && (
                  <p className={styles.hint}>
                    Saved Inbox item label · original subject unavailable
                  </p>
                )}
                <p>
                  Analyze reads this one selected message through your existing
                  Gmail connection. It reads no attachments and uses no language
                  model. A few evidence excerpts and your corrections are kept
                  encrypted for up to 20 minutes.
                </p>
                <label className={styles.check}>
                  <input
                    type="checkbox"
                    disabled={disabled}
                    checked={state.readConsent}
                    onChange={(event) =>
                      controller.consent(event.target.checked)
                    }
                  />
                  I allow NavoX to read this selected message for this review.
                </label>
                <button
                  type="button"
                  className={styles.primary}
                  disabled={disabled || !state.readConsent}
                  onClick={() => void controller.analyze()}
                >
                  Analyze selected message
                </button>
              </div>
            )}
          </section>
          {state.review && state.context && (
            <div
              className={styles.review}
              key={`${state.review.review_id}:${state.review.revision}:${state.epoch}`}
            >
              <p className={styles.hint}>
                Your saved time zone:{" "}
                {state.context.timezone.replaceAll("_", " ")} · review expires{" "}
                {when(state.review.expires_at, state.context.timezone)}
              </p>
              <StudentEmailEvidence
                review={state.review}
                timezone={state.context.timezone}
              />
              {!state.review.task_id && !state.receipt && (
                <StudentEmailCorrectionEditor
                  review={state.review}
                  courses={state.context.courses}
                  timezone={state.context.timezone}
                  disabled={disabled}
                  onSave={controller.correct}
                  onNotImportant={() => void controller.notImportant()}
                />
              )}
              <StudentEmailTaskActions
                review={state.review}
                timezone={state.context.timezone}
                disabled={disabled}
                planningEnabled={planningEnabled}
                accepted={!!state.receipt?.available}
                taskUnavailable={!!state.receipt && !state.receipt.available}
                onAccept={(confirmed, refresh) =>
                  void controller.accept(confirmed, refresh)
                }
                onCheck={() =>
                  void controller.reopen(state.review?.review_id ?? "")
                }
              />
              {state.receipt && !state.receipt.available && (
                <p className={styles.warning}>
                  The saved task is unavailable. Check this review before
                  attempting another change.
                </p>
              )}
            </div>
          )}
        </>
      )}
      <EmailErasure
        key={state.epoch}
        handles={state.handles}
        disabled={disabled}
        onOpen={(id) => void controller.reopen(id)}
        onErase={(handle) => void controller.erase(handle)}
        onForget={(source, confirmed) =>
          void controller.forget(source, confirmed)
        }
      />
    </div>
  );
}
function EmailSource({
  source,
  disabled,
  onSelect,
}: {
  source: StudentEmailSource;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <div className={styles.result}>
      <div>
        <strong>
          {source.title ??
            source.display_label ??
            "Saved email (subject unavailable)"}
        </strong>
        <p className={styles.hint}>
          {source.sender_label ?? "Sender label unavailable"}
          {!source.title && source.display_label
            ? " · Saved Inbox item label; original subject unavailable"
            : ""}
        </p>
      </div>
      <button type="button" disabled={disabled} onClick={onSelect}>
        Select for review
      </button>
    </div>
  );
}
