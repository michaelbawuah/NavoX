"use client";

import Link from "next/link";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import {
  type CourseMaterial,
  type CourseMaterialDetail,
  type CourseQuote,
  type CourseStaff,
  coursePolicyQuote,
  courseRequest,
  readCourseMaterialDetail,
  readCourseMaterials,
  readCourseOverview,
} from "../lib/student-coursework";
import {
  readStudyTasks,
  type StudyTask,
  studyDeadlineChoices,
  studyMinutes,
  studyRequest,
} from "../lib/student-planning";
import {
  RecoveryClipboardError,
  type RecoveryEvidence,
  type RecoveryExtension,
  type RecoveryExtensionInput,
  type RecoveryHandle,
  type RecoveryInput,
  type RecoveryPolicy,
  type RecoveryReceipt,
  RecoveryRequestError,
  type RecoveryView,
  type RecoveryWork,
  type RecoveryWorkChoice,
  readRecoveryExtension,
  readRecoveryView,
  recoveryAccount,
  recoveryCopyExtension,
  recoveryErasedHandles,
  recoveryReceipt,
  recoveryRecipientSource,
  recoveryRequest,
  recoveryReviewMilliseconds,
  recoveryTarget,
  recoveryUnfittedReason,
} from "../lib/student-recovery";
import {
  readStudentTimetable,
  type StudentCourse,
  type StudentTimetable,
  studentClock,
  studentDateInZone,
  studentDayLabel,
  studentError,
  studentRequest,
} from "../lib/student-schedule";
import styles from "./student-recovery.module.css";

function when(value: string, zone: string) {
  return `${studentDayLabel(studentDateInZone(value, zone))} at ${studentClock(value, zone)}`;
}
export function RecoveryTargetField({
  label,
  value,
  choice,
  timezone,
  disabled,
  onChange,
  onChoice,
}: {
  label: string;
  value: string;
  choice: string;
  timezone: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onChoice: (value: string) => void;
}) {
  const id = useId();
  let options: string[] = [];
  try {
    if (value) options = studyDeadlineChoices(value, timezone);
  } catch {}
  return (
    <>
      <label htmlFor={id}>
        {label} · {timezone.replaceAll("_", " ")}
        <input
          id={id}
          type="datetime-local"
          required
          disabled={disabled}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
      {options.length > 1 && (
        <label>
          This clock time occurs twice
          <select
            disabled={disabled}
            required
            value={choice}
            onChange={(event) => onChoice(event.target.value)}
          >
            <option value="">Choose which occurrence</option>
            {options.map((instant, index) => (
              <option key={instant} value={instant}>
                {index === 0 ? "Earlier" : "Later"} occurrence · {instant}
              </option>
            ))}
          </select>
        </label>
      )}
      {value && options.length === 0 && (
        <p className={styles.warning}>
          Check this date and time. A clock change may skip it in your saved
          time zone.
        </p>
      )}
    </>
  );
}

export function RecoveryDeadlineCard({
  work,
  timezone,
}: {
  work: RecoveryWork;
  timezone: string;
}) {
  return (
    <article className={styles.workCard}>
      <div className={styles.heading}>
        <h3>{work.title}</h3>
        <span
          className={
            work.official_deadline_status === "overdue"
              ? styles.late
              : styles.badge
          }
        >
          {work.official_deadline_status === "overdue"
            ? "Still overdue"
            : work.official_deadline_status === "upcoming"
              ? "Deadline ahead"
              : "Deadline unknown"}
        </span>
      </div>
      <div className={styles.deadlines}>
        <div>
          <p className={styles.eyebrow}>
            {work.deadline_source === "school_assignment"
              ? "School deadline"
              : work.deadline_source === "student_recorded"
                ? "Deadline you recorded"
                : "Official deadline unknown"}
          </p>
          <strong>
            {work.official_due_at
              ? when(work.official_due_at, timezone)
              : "No deadline source saved"}
          </strong>
          {work.deadline_source === "student_recorded" && (
            <p className={styles.hint}>
              Verify this date with your school; this task’s deadline was
              entered by you.
            </p>
          )}
        </div>
        <div>
          <p className={styles.eyebrow}>Your personal catch-up target</p>
          <strong>{when(work.personal_catchup_target, timezone)}</strong>
          <p className={styles.hint}>
            A planning target you chose, separate from the course deadline.
          </p>
        </div>
      </div>
      <p>
        {studyMinutes(work.remaining_minutes)} to catch up · priority{" "}
        {work.priority} of 5
      </p>
      <p className={styles.hint}>{work.deadline_notice}</p>
    </article>
  );
}

export function RecoveryEvidenceList({
  evidence,
  courses,
  tasks,
  timezone,
}: {
  evidence: RecoveryEvidence[];
  courses: StudentCourse[];
  tasks: StudyTask[];
  timezone: string;
}) {
  return (
    <section>
      <h3>What this proposal is based on</h3>
      {!evidence.length && (
        <p className={styles.empty}>
          No active scheduled meetings matched the report. That does not
          establish which material you missed.
        </p>
      )}
      <ul className={styles.list}>
        {evidence.map((row) =>
          row.kind === "student_reported_absence" ? (
            <li key={`${row.course_id}:${row.scheduled_start_at}`}>
              <strong>
                {courses.find((course) => course.id === row.course_id)?.name ??
                  "Selected course"}
              </strong>{" "}
              · scheduled {when(row.scheduled_start_at, timezone)}
              <p className={styles.hint}>
                You reported an absence. Attendance has not been verified.
              </p>
            </li>
          ) : row.kind === "saved_overdue_task" ? (
            <li key={`${row.task_id}:${row.revision}`}>
              <strong>
                {tasks.find((task) => task.id === row.task_id)?.title ??
                  "Selected saved task"}
              </strong>{" "}
              · recorded deadline {when(row.saved_due_at, timezone)}
              <p className={styles.hint}>
                {studyMinutes(row.remaining_minutes)} remained in the saved
                task.
              </p>
            </li>
          ) : (
            <li key={row.block_id}>
              Recorded skipped study block · {when(row.outcome_at, timezone)}
              <p className={styles.hint}>
                An actual saved Skip outcome, not an attendance judgment.
              </p>
            </li>
          ),
        )}
      </ul>
    </section>
  );
}

export function RecoveryProposal({
  preview,
  timezone,
  courses,
  tasks,
  disabled,
  onExtension,
  onCheck,
  onForget,
}: {
  preview: RecoveryView;
  timezone: string;
  courses: StudentCourse[];
  tasks: StudyTask[];
  disabled: boolean;
  onExtension: (work: RecoveryWork) => void;
  onCheck: () => void;
  onForget: () => void;
}) {
  return (
    <section className={styles.card}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Catch-up proposal · review only</p>
          <h2>A manageable next step</h2>
          <p className={styles.muted}>
            This hasn’t changed your deadlines, added tasks, or replaced your
            saved study plan.
          </p>
        </div>
        <button disabled={disabled} type="button" onClick={onCheck}>
          Check sources again
        </button>
      </div>
      <p className={styles.hint}>
        Checked {when(preview.server_now, timezone)} · preview expires{" "}
        {when(preview.expires_at, timezone)} · {timezone.replaceAll("_", " ")}
      </p>
      {preview.work.map((work) => (
        <div key={work.id}>
          <RecoveryDeadlineCard work={work} timezone={timezone} />
          {work.task_id && work.course_id && work.official_due_at && (
            <button
              disabled={disabled}
              type="button"
              onClick={() => onExtension(work)}
            >
              Review an extension request for this work
            </button>
          )}
        </div>
      ))}
      <h3>Proposed catch-up time</h3>
      <p className={styles.hint}>
        Review your sleep, protected hours, and breaks in Planner. Recovery uses
        that routine, including its starting defaults until you customize it.
      </p>
      {preview.result.blocks.length ? (
        <ol className={styles.timeline}>
          {preview.result.blocks.map((block) => (
            <li key={`${block.task_id}:${block.start_at}`}>
              <time dateTime={block.start_at}>
                {when(block.start_at, timezone)}–
                {studentClock(block.end_at, timezone)}
              </time>
              <strong>{block.title}</strong>
              <span>
                {studyMinutes(block.minutes)}
                {block.focus_window ? " · focus hours" : ""}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className={styles.empty}>
          No catch-up blocks fit in this proposal. Review the available time and
          effort estimates before trying again.
        </p>
      )}
      {preview.result.unfitted_work.length > 0 && (
        <div className={styles.warning}>
          <h3>Some work does not fit</h3>
          <p>
            Those minutes stay visible. NavoX has not moved them into your sleep
            hours.
          </p>
          {preview.result.unfitted_work.map((row) => (
            <p key={row.task_id}>
              <strong>
                {row.title} · {studyMinutes(row.minutes)} not placed
              </strong>
              <br />
              {recoveryUnfittedReason(row.reason)}
            </p>
          ))}
        </div>
      )}
      <details className={styles.details}>
        <summary>Available time and breathing room</summary>
        {preview.result.capacities.map((day) => (
          <p key={day.day}>
            {studentDayLabel(day.day)} · {studyMinutes(day.planned_minutes)}{" "}
            proposed, {studyMinutes(day.buffer_minutes)} left as buffer,{" "}
            {studyMinutes(day.free_minutes)} free before buffer
          </p>
        ))}
      </details>
      {preview.result.warnings.map((warning) => (
        <p className={styles.notice} key={warning}>
          {warning}
        </p>
      ))}
      <RecoveryEvidenceList
        evidence={preview.evidence}
        courses={courses}
        tasks={tasks}
        timezone={timezone}
      />
      <section className={styles.notice}>
        <h3>What is still unknown</h3>
        <ul>
          {preview.unknowns.map((unknown) => (
            <li key={unknown}>{unknown}</li>
          ))}
        </ul>
      </section>
      <section>
        <h3>Saved material available to review</h3>
        <p className={styles.hint}>
          These files are available in the selected courses. Their titles do not
          prove what a missed lecture covered.
        </p>
        {preview.available_materials.length ? (
          <ul className={styles.list}>
            {preview.available_materials.map((material) => (
              <li key={material.id}>
                <Link href={`/classes/${material.course_id}`}>
                  {material.title}
                </Link>{" "}
                · {material.kind} · version {material.revision}
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.empty}>
            No saved course materials are available for these courses.
          </p>
        )}
      </section>
      <section>
        <h3>Late-work and extension policy passages</h3>
        {preview.policies.length ? (
          preview.policies.map((policy) => (
            <article
              className={styles.item}
              key={`${policy.course_id}:${policy.kind}:${policy.source.material_id}:${policy.source.page}`}
            >
              <strong>
                {policy.kind === "late_work"
                  ? "Late-work policy passage"
                  : "Extension policy passage"}
              </strong>
              <blockquote>{policy.source.quote}</blockquote>
              <p className={styles.hint}>
                Saved syllabus · version {policy.source.revision} · page{" "}
                {policy.source.page}. This quote does not grant permission or
                automatically drop a quiz.
              </p>
            </article>
          ))
        ) : (
          <p className={styles.empty}>
            No policy passage selected. Ask your instructor about late work and
            extensions.
          </p>
        )}
      </section>
      <div className={styles.actions}>
        <Link href="/planner">Review your saved plan in Planner</Link>
        <button
          disabled={disabled}
          type="button"
          className={styles.danger}
          onClick={onForget}
        >
          Forget preview and edit my choices
        </button>
      </div>
    </section>
  );
}

export function RecoveryPolicyPicker({
  courses,
  disabled,
  onMaterials,
  onMaterial,
  onAdd,
  onClose,
}: {
  courses: StudentCourse[];
  disabled: boolean;
  onMaterials: (courseId: string) => Promise<CourseMaterial[]>;
  onMaterial: (courseId: string, id: string) => Promise<CourseMaterialDetail>;
  onAdd: (policy: RecoveryPolicy) => void;
  onClose: () => void;
}) {
  const [courseId, setCourseId] = useState(""),
    [kind, setKind] = useState<RecoveryPolicy["kind"]>("late_work"),
    [materials, setMaterials] = useState<CourseMaterial[]>([]),
    [material, setMaterial] = useState<CourseMaterialDetail | null>(null),
    [page, setPage] = useState(1),
    [quote, setQuote] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(
    () => () => {
      ++generation.current;
    },
    [],
  );
  async function chooseCourse(id: string) {
    const request = ++generation.current;
    setCourseId(id);
    setMaterials([]);
    setMaterial(null);
    setQuote("");
    setError("");
    if (!id) return;
    setBusy(true);
    try {
      const values = await onMaterials(id);
      if (request === generation.current)
        setMaterials(values.filter((row) => row.kind === "syllabus"));
    } catch (cause) {
      if (request === generation.current) setError(studentError(cause));
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  async function chooseMaterial(id: string) {
    const request = ++generation.current;
    setMaterial(null);
    setQuote("");
    setPage(1);
    setError("");
    if (!id) return;
    setBusy(true);
    try {
      const value = await onMaterial(courseId, id);
      if (request === generation.current) setMaterial(value);
    } catch (cause) {
      if (request === generation.current) setError(studentError(cause));
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      if (!material) throw new Error("Choose a saved syllabus first.");
      onAdd({
        course_id: courseId,
        kind,
        source: coursePolicyQuote(material, page, quote),
      });
      onClose();
    } catch (cause) {
      setError(studentError(cause));
    }
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.heading}>
        <h3>Choose an exact policy passage</h3>
        <button disabled={disabled} type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
      <fieldset disabled={disabled || busy}>
        <label>
          Course
          <select
            required
            value={courseId}
            onChange={(event) => void chooseCourse(event.target.value)}
          >
            <option value="">Choose a recovery course</option>
            {courses.map((course) => (
              <option key={course.id} value={course.id}>
                {course.code ? `${course.code} · ` : ""}
                {course.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          What this passage covers
          <select
            value={kind}
            onChange={(event) =>
              setKind(event.target.value as RecoveryPolicy["kind"])
            }
          >
            <option value="late_work">Late work</option>
            <option value="extension">Extension requests</option>
          </select>
        </label>
        <label>
          Saved syllabus
          <select
            required
            value={material?.id ?? ""}
            onChange={(event) => void chooseMaterial(event.target.value)}
          >
            <option value="">Choose a syllabus</option>
            {materials.map((row) => (
              <option key={row.id} value={row.id}>
                {row.title} · version {row.revision}
              </option>
            ))}
          </select>
        </label>
        {courseId && !busy && materials.length === 0 && (
          <p className={styles.hint}>
            No saved syllabus in this course. Add one in its course workspace,
            or continue with the policy marked unknown.
          </p>
        )}
        {material && (
          <>
            <label>
              Page
              <select
                value={page}
                onChange={(event) => {
                  setPage(Number(event.target.value));
                  setQuote("");
                }}
              >
                {Array.from(
                  { length: material.page_count },
                  (_, index) => index + 1,
                ).map((number) => (
                  <option key={number} value={number}>
                    {number}
                  </option>
                ))}
              </select>
            </label>
            <pre className={styles.sourceText}>{material.pages[page - 1]}</pre>
            <label>
              Copy the exact passage
              <textarea
                required
                maxLength={1000}
                rows={4}
                value={quote}
                onChange={(event) => setQuote(event.target.value)}
              />
            </label>
          </>
        )}
      </fieldset>
      {busy && <p role="status">Opening the saved syllabus…</p>}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      <p className={styles.hint}>
        The passage stays verbatim with its page and version. NavoX does not
        infer an extension, a dropped assessment, or permission to submit late.
      </p>
      <button
        className={styles.primary}
        type="submit"
        disabled={disabled || busy || !material}
      >
        Add reviewed policy passage
      </button>
    </form>
  );
}

export function RecoveryExtensionForm({
  preview,
  work,
  timezone,
  disabled,
  contacts,
  onSubmit,
  onClose,
}: {
  preview: RecoveryView;
  work: RecoveryWork;
  timezone: string;
  disabled: boolean;
  contacts: CourseStaff[];
  onSubmit: (value: RecoveryExtensionInput) => Promise<void>;
  onClose: () => void;
}) {
  const [name, setName] = useState(""),
    [email, setEmail] = useState(""),
    [source, setSource] = useState<CourseQuote | null>(null),
    [reason, setReason] = useState(""),
    [target, setTarget] = useState(""),
    [choice, setChoice] = useState(""),
    [policy, setPolicy] = useState(""),
    [confirmed, setConfirmed] = useState(false),
    [error, setError] = useState("");
  const policies = preview.policies.filter(
    (row) => row.kind === "extension" && row.course_id === work.course_id,
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      if (!work.course_id || !confirmed || !reason.trim())
        throw new Error(
          "Choose and confirm the recipient, and supply your own reason.",
        );
      const requested = recoveryTarget(target, timezone, choice);
      if (
        Date.parse(requested) <=
        Math.max(
          Date.parse(preview.server_now),
          Date.parse(work.official_due_at ?? ""),
        )
      )
        throw new Error(
          "Choose a requested extension after the recorded deadline and the current time.",
        );
      await onSubmit({
        request_id: crypto.randomUUID(),
        preview_hash: preview.preview_hash,
        work_id: work.id,
        recipient: {
          course_id: work.course_id,
          name: name.trim(),
          email: email.trim(),
          confirmed: true,
          source,
        },
        student_reason: reason,
        requested_extension_until: requested,
        policy: policy
          ? (policies.find(
              (row) =>
                `${row.source.material_id}:${row.source.page}` === policy,
            ) ?? null)
          : null,
      });
    } catch (cause) {
      setError(studentError(cause));
    }
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Draft only · nothing sent</p>
          <h3>Request an extension for {work.title}</h3>
        </div>
        <button type="button" disabled={disabled} onClick={onClose}>
          Cancel
        </button>
      </div>
      <p>
        {work.deadline_source === "school_assignment"
          ? "School deadline"
          : "Deadline recorded by you"}
        :{" "}
        <strong>
          {work.official_due_at
            ? when(work.official_due_at, timezone)
            : "Unknown"}
        </strong>
        . Asking for a later date does not change it.
      </p>
      <fieldset disabled={disabled}>
        {contacts.some((contact) => contact.email) && (
          <label>
            Use a saved course contact (optional)
            <select
              defaultValue=""
              onChange={(event) => {
                const contact = contacts.find(
                  (row) => row.email === event.target.value,
                );
                if (contact?.email) {
                  setName(contact.name);
                  setEmail(contact.email);
                  setSource(
                    recoveryRecipientSource(contact.email, contact.source),
                  );
                  setConfirmed(false);
                }
              }}
            >
              <option value="">Choose a saved contact</option>
              {contacts
                .filter((row) => row.email)
                .map((contact) => (
                  <option
                    key={`${contact.name}:${contact.email}`}
                    value={contact.email ?? ""}
                  >
                    {contact.name} · {contact.email}
                  </option>
                ))}
            </select>
          </label>
        )}
        <div className={styles.fields}>
          <label>
            Recipient’s name
            <input
              required
              maxLength={120}
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setSource(null);
                setConfirmed(false);
              }}
            />
          </label>
          <label>
            Recipient’s email
            <input
              required
              type="email"
              maxLength={320}
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
                setSource(null);
                setConfirmed(false);
              }}
            />
          </label>
        </div>
        <p className={styles.hint}>
          {source
            ? `Recipient appears in your selected saved source, version ${source.revision}, page ${source.page}.`
            : "Confirm this address yourself; no exact course-material source is attached."}
        </p>
        <label>
          <input
            type="checkbox"
            required
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
          />{" "}
          I chose this recipient for this assignment’s course.
        </label>
        <label>
          Your reason, in your own words
          <textarea
            required
            rows={4}
            maxLength={1000}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <p className={styles.hint}>
          Only the reason you enter is used. No illness, documentation, or
          instructor approval is invented.
        </p>
        <RecoveryTargetField
          label="Extension date you want to request"
          value={target}
          choice={choice}
          timezone={timezone}
          disabled={disabled}
          onChange={(value) => {
            setTarget(value);
            setChoice("");
          }}
          onChoice={setChoice}
        />
        <label>
          Exact extension policy passage (optional)
          <select
            value={policy}
            onChange={(event) => setPolicy(event.target.value)}
          >
            <option value="">Policy unknown / do not attach one</option>
            {policies.map((row) => (
              <option
                key={`${row.source.material_id}:${row.source.page}`}
                value={`${row.source.material_id}:${row.source.page}`}
              >
                Saved syllabus · version {row.source.revision} · page{" "}
                {row.source.page}
              </option>
            ))}
          </select>
        </label>
        {policy && (
          <blockquote>
            {
              policies.find(
                (row) =>
                  `${row.source.material_id}:${row.source.page}` === policy,
              )?.source.quote
            }
          </blockquote>
        )}
      </fieldset>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <button
        className={styles.primary}
        type="submit"
        disabled={disabled || !confirmed}
      >
        Prepare editable extension draft
      </button>
    </form>
  );
}

export function RecoveryExtensionEditor({
  value,
  timezone,
  disabled,
  onCopy,
  onForget,
}: {
  value: RecoveryExtension;
  timezone: string;
  disabled: boolean;
  onCopy: (value: {
    recipient: string;
    subject: string;
    body: string;
  }) => Promise<void>;
  onForget: () => void;
}) {
  const [to, setTo] = useState(value.recipient),
    [subject, setSubject] = useState(value.subject),
    [body, setBody] = useState(value.body),
    [error, setError] = useState("");
  async function copy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      await onCopy({ recipient: to, subject, body });
    } catch (cause) {
      setError(studentError(cause));
    }
  }
  return (
    <section className={styles.card}>
      <p className={styles.eyebrow}>Editable draft · nothing sent</p>
      <h2>Your extension request</h2>
      {value.notices.map((notice) => (
        <p className={styles.notice} key={notice}>
          {notice}
        </p>
      ))}
      <p>
        {value.deadline_source === "school_assignment"
          ? "School deadline"
          : "Deadline recorded by you"}
        : <strong>{when(value.official_due_at, timezone)}</strong>
        <br />
        Requested date:{" "}
        <strong>{when(value.requested_extension_until, timezone)}</strong>
      </p>
      <details className={styles.details}>
        <summary>The exact reason you supplied</summary>
        <blockquote>{value.student_reported_reason}</blockquote>
      </details>
      {value.policy && (
        <details className={styles.details}>
          <summary>Policy passage reviewed for this request</summary>
          <blockquote>{value.policy.source.quote}</blockquote>
          <p className={styles.hint}>
            Saved syllabus · version {value.policy.source.revision} · page{" "}
            {value.policy.source.page}. This passage does not grant an
            extension.
          </p>
        </details>
      )}
      <form className={styles.form} onSubmit={copy}>
        <fieldset disabled={disabled}>
          <label>
            To
            <input
              required
              type="email"
              value={to}
              maxLength={320}
              onChange={(event) => setTo(event.target.value)}
            />
          </label>
          {to !== value.recipient && (
            <p className={styles.hint}>
              You changed the recipient. Verify this address yourself; the saved
              recipient source describes the original address.
            </p>
          )}
          <label>
            Subject
            <input
              required
              maxLength={200}
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
            />
          </label>
          <label>
            Body
            <textarea
              required
              rows={14}
              maxLength={12000}
              value={body}
              onChange={(event) => setBody(event.target.value)}
            />
          </label>
        </fieldset>
        <p className={styles.hint}>
          Edits stay in this tab. Copying rechecks the saved sources first; you
          decide whether to use the draft in your own email app.
        </p>
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
        <div className={styles.actions}>
          <button type="submit" className={styles.primary} disabled={disabled}>
            Check sources & copy edited draft
          </button>
          <button
            type="button"
            disabled={disabled}
            className={styles.danger}
            onClick={onForget}
          >
            Forget this draft
          </button>
        </div>
      </form>
    </section>
  );
}

interface WorkDraft {
  key: string;
  title: string;
  courseId: string | null;
  source: Pick<
    RecoveryWorkChoice,
    | "task_id"
    | "task_revision"
    | "material_id"
    | "material_revision"
    | "course_id"
  >;
  remaining: number;
  priority: number;
  target: string;
  choice: string;
}
type Pending =
  | { kind: "recovery"; input: RecoveryInput }
  | {
      kind: "extension";
      parent: RecoveryReceipt;
      input: RecoveryExtensionInput;
    };

function StudentRecoveryAccount({ accountId }: { accountId: string }) {
  const [table, setTable] = useState<StudentTimetable | null>(null),
    [tasks, setTasks] = useState<StudyTask[] | null>([]),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [blocked, setBlocked] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [trigger, setTrigger] =
      useState<RecoveryInput["trigger"]["kind"]>("reported_absence"),
    [absenceCourses, setAbsenceCourses] = useState<string[]>([]),
    [from, setFrom] = useState(""),
    [through, setThrough] = useState(""),
    [triggerTask, setTriggerTask] = useState(""),
    [days, setDays] = useState(4),
    [work, setWork] = useState<WorkDraft[]>([]),
    [policies, setPolicies] = useState<RecoveryPolicy[]>([]),
    [policyEditor, setPolicyEditor] = useState(false),
    [materialCourse, setMaterialCourse] = useState(""),
    [materials, setMaterials] = useState<CourseMaterial[]>([]),
    [reading, setReading] = useState(false),
    [confirmed, setConfirmed] = useState(false),
    [preview, setPreview] = useState<RecoveryView | null>(null),
    [extensionWork, setExtensionWork] = useState<RecoveryWork | null>(null),
    [contacts, setContacts] = useState<CourseStaff[]>([]),
    [extension, setExtension] = useState<RecoveryExtension | null>(null),
    [pending, setPending] = useState<Pending | null>(null),
    [forgotten, setForgotten] = useState<RecoveryHandle[]>([]),
    [epoch, setEpoch] = useState(0);
  const alive = useRef(true),
    generation = useRef(0),
    operation = useRef(false),
    handles = useRef<RecoveryHandle[]>([]),
    contactsGeneration = useRef(0),
    materialsGeneration = useRef(0);
  const clearPrivate = useCallback((message: string) => {
    ++generation.current;
    ++materialsGeneration.current;
    ++contactsGeneration.current;
    setTasks(null);
    setTable(null);
    setWork([]);
    setPolicies([]);
    setMaterials([]);
    setMaterialCourse("");
    setFrom("");
    setThrough("");
    setAbsenceCourses([]);
    setTriggerTask("");
    setConfirmed(false);
    setPreview(null);
    setExtension(null);
    setExtensionWork(null);
    setContacts([]);
    setPending(null);
    setPolicyEditor(false);
    setBusy(false);
    setReading(false);
    setLoading(false);
    setBlocked(true);
    setError("");
    setNotice(message);
    setEpoch((current) => current + 1);
  }, []);
  const owner = useCallback(async () => {
    try {
      const current = await recoveryAccount();
      if (current !== accountId)
        throw new Error(
          "Your account changed. Reload this page before reviewing private recovery work.",
        );
    } catch (cause) {
      if (alive.current) {
        clearPrivate(
          "Private recovery text cleared. Check your account before continuing.",
        );
        setError(studentError(cause));
      }
      throw cause;
    }
  }, [accountId, clearPrivate]);
  const load = useCallback(async () => {
    const request = ++generation.current;
    setLoading(true);
    setError("");
    try {
      await owner();
      const [nextTable, nextTasks] = await Promise.all([
        studentRequest("/timetable?days=1", readStudentTimetable),
        studyRequest("/tasks", readStudyTasks).catch(() => null),
      ]);
      await owner();
      if (!alive.current || request !== generation.current) return;
      setTable(nextTable);
      setTasks(nextTasks);
      setBlocked(false);
      setNotice(
        "Saved sources checked. Choose only the work you want to catch up on.",
      );
    } catch (cause) {
      if (alive.current && request === generation.current) {
        clearPrivate(
          "Private recovery work was cleared. Check your account before continuing.",
        );
        setError(studentError(cause));
      }
    } finally {
      if (alive.current && request === generation.current) setLoading(false);
    }
  }, [owner, clearPrivate]);
  useEffect(() => {
    alive.current = true;
    void load();
    const hidden = () => {
        if (document.hidden)
          clearPrivate(
            "Private recovery text cleared while this page was hidden. Reopen saved sources to continue.",
          );
      },
      blur = () =>
        clearPrivate(
          "Private recovery text cleared when you left this page. Reopen saved sources to continue.",
        ),
      accountChanged = () =>
        clearPrivate("Account changed. Reload this page before continuing.");
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("blur", blur);
    window.addEventListener("navox:account-changed", accountChanged);
    window.addEventListener("navox:signout", accountChanged);
    window.addEventListener("storage", accountChanged);
    return () => {
      alive.current = false;
      ++generation.current;
      ++materialsGeneration.current;
      ++contactsGeneration.current;
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("blur", blur);
      window.removeEventListener("navox:account-changed", accountChanged);
      window.removeEventListener("navox:signout", accountChanged);
      window.removeEventListener("storage", accountChanged);
    };
  }, [load, clearPrivate]);
  useEffect(() => {
    if (!preview) return;
    const timer = setTimeout(() => {
      ++generation.current;
      ++contactsGeneration.current;
      setPreview(null);
      setExtension(null);
      setExtensionWork(null);
      setContacts([]);
      setPending(null);
      setConfirmed(false);
      setNotice(
        "This proposal reached its next review time. Request a new one using current sources and available time.",
      );
      setEpoch((current) => current + 1);
    }, recoveryReviewMilliseconds(preview));
    return () => clearTimeout(timer);
  }, [preview]);
  function remember(value: RecoveryReceipt, command: Pending) {
    const handle: RecoveryHandle = {
      ...recoveryReceipt(value),
      kind: command.kind,
      parent_id:
        command.kind === "extension" ? command.parent.preview_id : null,
    };
    handles.current = [
      ...handles.current.filter((row) => row.preview_id !== handle.preview_id),
      handle,
    ];
    setForgotten(handles.current);
  }
  async function stale(cause: unknown, clearOnFailure = false) {
    if (
      clearOnFailure ||
      (cause instanceof RecoveryRequestError &&
        [401, 403, 404, 409, 410].includes(cause.status ?? 0))
    ) {
      ++generation.current;
      ++contactsGeneration.current;
      setPreview(null);
      setExtension(null);
      setExtensionWork(null);
      setContacts([]);
      setPending(null);
      setConfirmed(false);
      setEpoch((current) => current + 1);
    }
    setError(studentError(cause));
  }
  async function execute(command: Pending) {
    if (operation.current || blocked) return;
    operation.current = true;
    setBusy(true);
    setError("");
    const request = generation.current;
    try {
      await owner();
      const value =
        command.kind === "recovery"
          ? await recoveryRequest(
              "/preview",
              readRecoveryView,
              "POST",
              command.input,
            )
          : await recoveryRequest(
              `/previews/${command.parent.preview_id}/extension-draft`,
              readRecoveryExtension,
              "POST",
              command.input,
            );
      await owner();
      if (!alive.current) return;
      remember(value, command);
      if (request !== generation.current) return;
      setPending(null);
      if (command.kind === "recovery") {
        const proposal = value as RecoveryView;
        setPreview(proposal);
        if (table && proposal.timezone !== table.timezone) {
          setWork((current) =>
            current.map((row) => ({ ...row, target: "", choice: "" })),
          );
          setConfirmed(false);
        }
        setTable((current) =>
          current
            ? {
                ...current,
                timezone: proposal.timezone,
                server_now: proposal.server_now,
              }
            : current,
        );
        setExtension(null);
        setExtensionWork(null);
        setNotice(
          "Catch-up proposal prepared. Your recorded deadlines and saved plan are unchanged.",
        );
      } else {
        setExtension(value as RecoveryExtension);
        setExtensionWork(null);
        setNotice(
          "Editable draft prepared. No email or external draft was created.",
        );
      }
    } catch (cause) {
      if (alive.current && request === generation.current) {
        if (cause instanceof RecoveryRequestError && cause.uncertain)
          setPending(command);
        else {
          setPending(null);
          await stale(cause);
        }
        if (cause instanceof RecoveryRequestError && cause.uncertain)
          setError(
            "The preview result is unknown. Check this same request before creating another preview.",
          );
      }
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const courses = table?.courses.filter((course) => !course.archived) ?? [],
    zone = preview?.timezone ?? extension?.timezone ?? table?.timezone ?? "UTC",
    locked = busy || loading || blocked || !!pending;
  const usableTasks = (tasks ?? []).filter(
    (task) =>
      task.status === "active" &&
      task.remaining_minutes > 0 &&
      !task.source_needs_review &&
      (!task.course_id ||
        courses.some((course) => course.id === task.course_id)),
  );
  const overdueTasks = usableTasks.filter(
    (task) =>
      task.due_at &&
      table &&
      Date.parse(task.due_at) < Date.parse(table.server_now),
  );
  function addTask(task: StudyTask) {
    if (work.length >= 20 || work.some((row) => row.source.task_id === task.id))
      return;
    setWork([
      ...work,
      {
        key: crypto.randomUUID(),
        title: task.title,
        courseId: task.course_id,
        source: {
          task_id: task.id,
          task_revision: task.revision,
          ...(task.course_id ? { course_id: task.course_id } : {}),
        },
        remaining: task.remaining_minutes,
        priority: task.priority,
        target: "",
        choice: "",
      },
    ]);
    setConfirmed(false);
  }
  async function chooseMaterialCourse(id: string) {
    setMaterialCourse(id);
    setMaterials([]);
    const request = ++materialsGeneration.current;
    if (!id) return;
    setReading(true);
    setError("");
    try {
      await owner();
      const rows = await courseRequest(id, "/materials", readCourseMaterials);
      await owner();
      if (alive.current && request === materialsGeneration.current)
        setMaterials(rows);
    } catch (cause) {
      if (alive.current && request === materialsGeneration.current)
        setError(studentError(cause));
    } finally {
      if (alive.current && request === materialsGeneration.current)
        setReading(false);
    }
  }
  async function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      if (!table || !confirmed || !work.length)
        throw new Error(
          "Choose work and review the effort, personal targets, and reported trigger first.",
        );
      const selected = tasks?.find((task) => task.id === triggerTask);
      if (
        trigger === "reported_absence" &&
        (!absenceCourses.length || !from || !through)
      )
        throw new Error(
          "Choose the courses and dates you are reporting as missed.",
        );
      if (trigger === "reported_absence" && absenceCourses.length > 10)
        throw new Error("Choose up to ten courses for one recovery proposal.");
      if (trigger === "overdue_task" && !selected)
        throw new Error("Choose an actually overdue saved task.");
      const selectedCourses = new Set([
        ...work.flatMap((row) => (row.courseId ? [row.courseId] : [])),
        ...(trigger === "reported_absence" ? absenceCourses : []),
      ]);
      if (policies.some((policy) => !selectedCourses.has(policy.course_id)))
        throw new Error(
          "Remove policy passages for courses outside this recovery request.",
        );
      const input: RecoveryInput = {
        request_id: crypto.randomUUID(),
        trigger: {
          kind: trigger,
          course_ids: trigger === "reported_absence" ? [...absenceCourses] : [],
          from_day: trigger === "reported_absence" ? from : null,
          through_day: trigger === "reported_absence" ? through : null,
          task_id: trigger === "overdue_task" ? (selected?.id ?? null) : null,
          task_revision:
            trigger === "overdue_task" ? (selected?.revision ?? null) : null,
        },
        days,
        work: work.map((row) => {
          if (
            !Number.isInteger(row.remaining) ||
            row.remaining < 1 ||
            row.remaining > 10080
          )
            throw new Error(
              "Choose between 1 and 10,080 remaining minutes for each item.",
            );
          return {
            ...row.source,
            remaining_minutes: row.remaining,
            priority: row.priority,
            personal_catchup_target: recoveryTarget(
              row.target,
              zone,
              row.choice,
            ),
          };
        }),
        policies: policies.map((row) => ({
          ...row,
          source: { ...row.source },
        })),
      };
      await execute({ kind: "recovery", input });
    } catch (cause) {
      setError(studentError(cause));
    }
  }
  async function checkPreview() {
    if (!preview || operation.current || locked) return;
    operation.current = true;
    setBusy(true);
    const request = generation.current;
    try {
      await owner();
      const value = await recoveryRequest(
        `/previews/${preview.preview_id}`,
        readRecoveryView,
      );
      if (
        value.preview_id !== preview.preview_id ||
        value.preview_hash !== preview.preview_hash
      )
        throw new Error("This saved preview changed. Request a new proposal.");
      await owner();
      if (alive.current && request === generation.current) {
        setPreview(value);
        setNotice("Sources and available time checked again.");
      }
    } catch (cause) {
      if (alive.current && request === generation.current)
        await stale(cause, true);
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function forget(handle: RecoveryReceipt, parent = false) {
    if (operation.current || busy) return;
    const saved = handles.current.find(
      (row) => row.preview_id === handle.preview_id,
    );
    const isParent = parent || saved?.kind === "recovery";
    const clearsCurrent = isParent && preview?.preview_id === handle.preview_id;
    operation.current = true;
    setBusy(true);
    setError("");
    const request = generation.current;
    if (clearsCurrent) {
      ++contactsGeneration.current;
      setPreview(null);
      setExtension(null);
      setExtensionWork(null);
      setContacts([]);
      setPending(null);
    } else if (extension?.preview_id === handle.preview_id) setExtension(null);
    try {
      await owner();
      await recoveryRequest(
        `/previews/${handle.preview_id}`,
        (value) => {
          if (value !== null)
            throw new Error("The deletion result could not be checked.");
          return true;
        },
        "DELETE",
        { preview_hash: handle.preview_hash },
      );
      await owner();
      if (!alive.current || request !== generation.current) return;
      handles.current = recoveryErasedHandles(
        handles.current,
        handle.preview_id,
      );
      setForgotten(handles.current);
      setNotice(
        isParent
          ? "Recovery preview and its extension drafts erased. Your original tasks, materials, and deadlines stay."
          : "Extension draft erased. No email was sent.",
      );
      setConfirmed(false);
      setEpoch((current) => current + 1);
    } catch (cause) {
      if (alive.current && request === generation.current)
        setError(
          `${studentError(cause)} The displayed private preview was cleared; its deletion is not confirmed. Retry forgetting it below.`,
        );
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function openExtension(row: RecoveryWork) {
    if (!preview || !row.course_id || locked) return;
    const request = generation.current,
      contactRequest = ++contactsGeneration.current;
    setExtensionWork(row);
    setContacts([]);
    setExtension(null);
    try {
      await owner();
      const result = await courseRequest(
        row.course_id,
        "/overview",
        readCourseOverview,
      );
      await owner();
      if (
        alive.current &&
        request === generation.current &&
        contactRequest === contactsGeneration.current
      )
        setContacts(result.staff);
    } catch {
      if (
        alive.current &&
        request === generation.current &&
        contactRequest === contactsGeneration.current
      )
        setNotice(
          "Saved course contacts are unavailable. Enter and confirm the intended recipient yourself.",
        );
    }
  }
  async function copyExtension(value: {
    recipient: string;
    subject: string;
    body: string;
  }) {
    if (!extension || operation.current || locked) return;
    operation.current = true;
    setBusy(true);
    setError("");
    const request = generation.current;
    try {
      await owner();
      const checked = await recoveryRequest(
        `/previews/${extension.preview_id}`,
        readRecoveryExtension,
      );
      if (
        checked.preview_id !== extension.preview_id ||
        checked.preview_hash !== extension.preview_hash
      )
        throw new Error(
          "This saved draft changed. Prepare a current draft before copying.",
        );
      await owner();
      if (!alive.current || request !== generation.current) return;
      await recoveryCopyExtension(value, (text) =>
        navigator.clipboard.writeText(text),
      );
      if (alive.current && request === generation.current)
        setNotice(
          "Edited draft copied. Nothing was sent. Review it in your email app before taking any action.",
        );
    } catch (cause) {
      if (alive.current && request === generation.current) {
        if (cause instanceof RecoveryClipboardError)
          setNotice(
            "Sources checked. Clipboard access is unavailable; your edited draft is still here for manual copying.",
          );
        else await stale(cause, true);
        throw cause;
      }
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function reopen(handle: RecoveryHandle) {
    if (operation.current || locked || !table) return;
    operation.current = true;
    setBusy(true);
    setError("");
    const request = generation.current;
    try {
      await owner();
      const parent =
        handle.kind === "recovery"
          ? await recoveryRequest(
              `/previews/${handle.preview_id}`,
              readRecoveryView,
            )
          : handle.parent_id
            ? await recoveryRequest(
                `/previews/${handle.parent_id}`,
                readRecoveryView,
              )
            : null;
      if (!parent)
        throw new Error(
          "This draft no longer has a recovery proposal. Prepare a current preview.",
        );
      const draft =
        handle.kind === "extension"
          ? await recoveryRequest(
              `/previews/${handle.preview_id}`,
              readRecoveryExtension,
            )
          : null;
      await owner();
      if (!alive.current || request !== generation.current) return;
      if (
        parent.preview_id !==
          (handle.kind === "recovery" ? handle.preview_id : handle.parent_id) ||
        (handle.kind === "recovery" &&
          parent.preview_hash !== handle.preview_hash) ||
        (draft && draft.preview_hash !== handle.preview_hash)
      )
        throw new Error(
          "This saved preview changed. Request a current proposal.",
        );
      setPreview(parent);
      setExtension(draft);
      setExtensionWork(null);
      setPolicyEditor(false);
      setContacts([]);
      setPending(null);
      setTable((current) =>
        current
          ? {
              ...current,
              timezone: parent.timezone,
              server_now: parent.server_now,
            }
          : current,
      );
      setEpoch((current) => current + 1);
      setNotice(
        "Saved preview reopened after checking current sources. It is still a proposal; nothing has been sent or applied.",
      );
    } catch (cause) {
      if (alive.current && request === generation.current)
        await stale(cause, true);
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const selectedCourseIds = new Set([
    ...work.flatMap((row) => (row.courseId ? [row.courseId] : [])),
    ...(trigger === "reported_absence" ? absenceCourses : []),
  ]);
  return (
    <section className={styles.panel} aria-label="Catch-up recovery">
      <div className={`${styles.card} ${styles.hero}`}>
        <p className={styles.eyebrow}>Make room to catch up</p>
        <h2>One step at a time.</h2>
        <p className={styles.muted}>
          Choose what you missed and what needs attention. Review a realistic
          proposal around your timetable, existing study time, and planning
          routine.
        </p>
        <p className={styles.hint}>
          A private preview lasts up to 20 minutes. Leaving this page clears its
          displayed text. Forgetting a preview erases its saved copy; original
          school records stay unchanged.
        </p>
      </div>
      {notice && (
        <p className={styles.notice} role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">Opening your saved sources…</p>}
      {blocked && !loading && (
        <button type="button" onClick={() => void load()}>
          Check account & reopen saved sources
        </button>
      )}
      {pending && (
        <section className={styles.warning}>
          <h3>The last result is unconfirmed</h3>
          <p>
            The preview may already be saved. This checks the exact same request
            so another preview won’t be created.
          </p>
          <button
            type="button"
            disabled={busy || blocked}
            onClick={() => void execute(pending)}
          >
            Check this same request
          </button>
        </section>
      )}
      {preview && table && (
        <RecoveryProposal
          preview={preview}
          timezone={zone}
          courses={courses}
          tasks={tasks ?? []}
          disabled={locked}
          onCheck={() => void checkPreview()}
          onExtension={(row) => void openExtension(row)}
          onForget={() => void forget(preview, true)}
        />
      )}
      {extensionWork && preview && (
        <RecoveryExtensionForm
          key={`${extensionWork.id}:${epoch}`}
          preview={preview}
          work={extensionWork}
          timezone={zone}
          contacts={contacts}
          disabled={locked}
          onClose={() => setExtensionWork(null)}
          onSubmit={(input) =>
            execute({
              kind: "extension",
              parent: recoveryReceipt(preview),
              input,
            })
          }
        />
      )}
      {extension && (
        <RecoveryExtensionEditor
          key={`${extension.preview_id}:${epoch}`}
          value={extension}
          timezone={zone}
          disabled={locked}
          onCopy={copyExtension}
          onForget={() => void forget(extension)}
        />
      )}
      {!preview && table && !blocked && (
        <div className={styles.card}>
          <form onSubmit={prepare}>
            <fieldset className={styles.stack} disabled={locked}>
              <legend>Start with what actually happened</legend>
              <label>
                Why you want a catch-up proposal
                <select
                  value={trigger}
                  onChange={(event) => {
                    setTrigger(
                      event.target.value as RecoveryInput["trigger"]["kind"],
                    );
                    setConfirmed(false);
                  }}
                >
                  <option value="reported_absence">I missed classes</option>
                  <option value="overdue_task">A saved task is overdue</option>
                  <option value="skipped_blocks">
                    I recorded three skipped study blocks in a row
                  </option>
                </select>
              </label>
              {trigger === "reported_absence" && (
                <>
                  <div className={styles.fields}>
                    <label>
                      First missed date
                      <input
                        type="date"
                        required
                        value={from}
                        onChange={(event) => {
                          setFrom(event.target.value);
                          setConfirmed(false);
                        }}
                      />
                    </label>
                    <label>
                      Last missed date
                      <input
                        type="date"
                        required
                        value={through}
                        onChange={(event) => {
                          setThrough(event.target.value);
                          setConfirmed(false);
                        }}
                      />
                    </label>
                  </div>
                  <p className={styles.hint}>
                    Report up to 14 past days within the last 90 days. Scheduled
                    meetings are evidence of the timetable, not verified
                    attendance.
                  </p>
                  <fieldset className={styles.courseChoices}>
                    <legend>Courses you missed</legend>
                    {courses.map((course) => (
                      <label key={course.id}>
                        <input
                          type="checkbox"
                          checked={absenceCourses.includes(course.id)}
                          onChange={(event) => {
                            setAbsenceCourses((current) =>
                              event.target.checked
                                ? [...current, course.id]
                                : current.filter((id) => id !== course.id),
                            );
                            setConfirmed(false);
                          }}
                        />
                        {course.code ? `${course.code} · ` : ""}
                        {course.name}
                      </label>
                    ))}
                    {!courses.length && (
                      <p>
                        No active courses saved. Add your timetable in Classes
                        first.
                      </p>
                    )}
                  </fieldset>
                </>
              )}
              {trigger === "overdue_task" && (
                <label>
                  Overdue incomplete task
                  <select
                    required
                    value={triggerTask}
                    onChange={(event) => {
                      setTriggerTask(event.target.value);
                      const selected = overdueTasks.find(
                        (task) => task.id === event.target.value,
                      );
                      if (selected) addTask(selected);
                      setConfirmed(false);
                    }}
                  >
                    <option value="">
                      {overdueTasks.length
                        ? "Choose a saved task"
                        : "No eligible overdue tasks found"}
                    </option>
                    {overdueTasks.map((task) => (
                      <option key={task.id} value={task.id}>
                        {task.title}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {trigger === "skipped_blocks" && (
                <p className={styles.hint}>
                  NavoX checks that your three latest recorded study outcomes in
                  the last 14 days were actual skips. Unfinished or merely
                  planned blocks are not counted.
                </p>
              )}
              <h3>Choose the work you want to catch up on</h3>
              <p className={styles.hint}>
                Select saved tasks or materials, then review the effort and set
                your own personal targets. Those targets never change school
                deadlines.
              </p>
              {tasks === null ? (
                <p className={styles.warning}>
                  Saved tasks could not be read. Reopen saved sources to try
                  again, or choose course materials below.
                </p>
              ) : (
                <label>
                  Add a saved incomplete task
                  <select
                    value=""
                    disabled={work.length >= 20}
                    onChange={(event) => {
                      const task = tasks.find(
                        (row) => row.id === event.target.value,
                      );
                      if (task) addTask(task);
                    }}
                  >
                    <option value="">Choose a task to add</option>
                    {usableTasks
                      .filter(
                        (task) =>
                          !work.some((row) => row.source.task_id === task.id),
                      )
                      .map((task) => (
                        <option key={task.id} value={task.id}>
                          {task.title}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              {tasks?.some((task) => task.source_needs_review) && (
                <p className={styles.warning}>
                  Some imported tasks need a school-source review in Planner
                  before they can be selected here.
                </p>
              )}
              <label>
                Find saved material in a course
                <select
                  value={materialCourse}
                  disabled={reading}
                  onChange={(event) =>
                    void chooseMaterialCourse(event.target.value)
                  }
                >
                  <option value="">Choose a course</option>
                  {courses.map((course) => (
                    <option key={course.id} value={course.id}>
                      {course.code ? `${course.code} · ` : ""}
                      {course.name}
                    </option>
                  ))}
                </select>
              </label>
              {reading && <p role="status">Opening saved material titles…</p>}
              {materialCourse && !reading && (
                <label>
                  Add material to review
                  <select
                    value=""
                    disabled={work.length >= 20}
                    onChange={(event) => {
                      const material = materials.find(
                        (row) => row.id === event.target.value,
                      );
                      if (
                        material &&
                        !work.some(
                          (row) => row.source.material_id === material.id,
                        )
                      ) {
                        setWork([
                          ...work,
                          {
                            key: crypto.randomUUID(),
                            title: `Review ${material.title}`,
                            courseId: materialCourse,
                            source: {
                              material_id: material.id,
                              material_revision: material.revision,
                              course_id: materialCourse,
                            },
                            remaining: 60,
                            priority: 3,
                            target: "",
                            choice: "",
                          },
                        ]);
                        setConfirmed(false);
                      }
                    }}
                  >
                    <option value="">
                      {materials.length
                        ? "Choose a saved material"
                        : "No material saved in this course"}
                    </option>
                    {materials
                      .filter(
                        (material) =>
                          !work.some(
                            (row) => row.source.material_id === material.id,
                          ),
                      )
                      .map((material) => (
                        <option key={material.id} value={material.id}>
                          {material.title} · version {material.revision}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              {work.map((row) => (
                <section key={row.key} className={styles.workCard}>
                  <div className={styles.heading}>
                    <h3>{row.title}</h3>
                    <button
                      type="button"
                      onClick={() => {
                        setWork((current) =>
                          current.filter((item) => item.key !== row.key),
                        );
                        setConfirmed(false);
                      }}
                    >
                      Remove
                    </button>
                  </div>
                  <div className={styles.fields}>
                    <label>
                      Effort left (minutes)
                      <input
                        type="number"
                        required
                        min={1}
                        max={10080}
                        value={row.remaining}
                        onChange={(event) => {
                          setWork((current) =>
                            current.map((item) =>
                              item.key === row.key
                                ? {
                                    ...item,
                                    remaining: Number(event.target.value),
                                  }
                                : item,
                            ),
                          );
                          setConfirmed(false);
                        }}
                      />
                    </label>
                    <label>
                      Priority
                      <select
                        value={row.priority}
                        onChange={(event) => {
                          setWork((current) =>
                            current.map((item) =>
                              item.key === row.key
                                ? {
                                    ...item,
                                    priority: Number(event.target.value),
                                  }
                                : item,
                            ),
                          );
                          setConfirmed(false);
                        }}
                      >
                        {[1, 2, 3, 4, 5].map((priority) => (
                          <option key={priority} value={priority}>
                            {priority} ·{" "}
                            {
                              ["Low", "Lower", "Normal", "High", "Highest"][
                                priority - 1
                              ]
                            }
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <RecoveryTargetField
                    label="My personal catch-up target"
                    value={row.target}
                    choice={row.choice}
                    timezone={zone}
                    disabled={locked}
                    onChange={(value) => {
                      setWork((current) =>
                        current.map((item) =>
                          item.key === row.key
                            ? { ...item, target: value, choice: "" }
                            : item,
                        ),
                      );
                      setConfirmed(false);
                    }}
                    onChoice={(value) => {
                      setWork((current) =>
                        current.map((item) =>
                          item.key === row.key
                            ? { ...item, choice: value }
                            : item,
                        ),
                      );
                      setConfirmed(false);
                    }}
                  />
                  <p className={styles.hint}>
                    Choose a future personal target within 31 days. A later
                    target does not grant an extension.
                  </p>
                </section>
              ))}
              <label>
                How many days should the proposal cover?
                <select
                  value={days}
                  onChange={(event) => {
                    setDays(Number(event.target.value));
                    setConfirmed(false);
                  }}
                >
                  {Array.from({ length: 13 }, (_, index) => index + 2).map(
                    (day) => (
                      <option key={day} value={day}>
                        {day} days
                      </option>
                    ),
                  )}
                </select>
              </label>
              <h3>Policy passages (optional)</h3>
              {policies.map((policy) => (
                <article
                  className={styles.item}
                  key={`${policy.course_id}:${policy.kind}:${policy.source.material_id}:${policy.source.page}`}
                >
                  <strong>
                    {policy.kind === "extension"
                      ? "Extension policy"
                      : "Late-work policy"}{" "}
                    · page {policy.source.page} · version{" "}
                    {policy.source.revision}
                  </strong>
                  <blockquote>{policy.source.quote}</blockquote>
                  <button
                    type="button"
                    onClick={() => {
                      setPolicies((current) =>
                        current.filter((row) => row !== policy),
                      );
                      setConfirmed(false);
                    }}
                  >
                    Remove passage
                  </button>
                </article>
              ))}
              <button
                disabled={selectedCourseIds.size === 0 || policies.length >= 10}
                type="button"
                onClick={() => setPolicyEditor(true)}
              >
                ＋ Choose an exact syllabus passage
              </button>
              <p className={styles.hint}>
                If you don’t have a policy source, it stays unknown. NavoX won’t
                assume late work is allowed or that an assessment is dropped.
              </p>
              <label>
                <input
                  type="checkbox"
                  required
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />{" "}
                I reviewed the reported trigger, selected sources, effort, and
                my personal catch-up targets.
              </label>
              <button
                type="submit"
                className={styles.primary}
                disabled={locked || !confirmed || !work.length}
              >
                Preview my catch-up proposal
              </button>
            </fieldset>
          </form>
          {policyEditor && (
            <RecoveryPolicyPicker
              key={`policy-${epoch}`}
              courses={courses.filter((course) =>
                selectedCourseIds.has(course.id),
              )}
              disabled={locked}
              onClose={() => setPolicyEditor(false)}
              onMaterials={(id) =>
                courseRequest(id, "/materials", readCourseMaterials)
              }
              onMaterial={(id, materialId) =>
                courseRequest(
                  id,
                  `/materials/${materialId}`,
                  readCourseMaterialDetail,
                )
              }
              onAdd={(policy) => {
                setPolicies((current) => [
                  ...current.filter(
                    (row) =>
                      !(
                        row.course_id === policy.course_id &&
                        row.kind === policy.kind &&
                        row.source.material_id === policy.source.material_id &&
                        row.source.page === policy.source.page
                      ),
                  ),
                  policy,
                ]);
                setConfirmed(false);
              }}
            />
          )}
          <p className={styles.hint}>
            {zone.replaceAll("_", " ")} · saved sources checked{" "}
            {when(table.server_now, zone)}
          </p>
        </div>
      )}
      {forgotten.length > 0 && (
        <details className={styles.details}>
          <summary>Saved recovery previews</summary>
          <p>
            Erasing a recovery proposal also erases its extension drafts. It
            does not delete your original tasks, syllabus, attendance records,
            or school deadlines.
          </p>
          {forgotten.map((handle, index) => (
            <div className={styles.actions} key={handle.preview_id}>
              <span>
                {handle.kind === "recovery"
                  ? "Catch-up proposal"
                  : "Extension draft"}{" "}
                {index + 1}
              </span>
              <button
                disabled={locked || !table}
                type="button"
                onClick={() => void reopen(handle)}
              >
                Check & reopen
              </button>
              <button
                disabled={busy || loading}
                type="button"
                onClick={() =>
                  void forget(handle, handle.preview_id === preview?.preview_id)
                }
              >
                Forget saved preview
              </button>
            </div>
          ))}
        </details>
      )}
    </section>
  );
}
export function StudentRecovery({ accountId }: { accountId: string }) {
  return <StudentRecoveryAccount key={accountId} accountId={accountId} />;
}
export function StudentRecoveryCard() {
  return (
    <section
      className={`${styles.panel} ${styles.card} ${styles.entryCard}`}
      aria-label="Catch-up support"
    >
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>A little room to catch up</p>
          <h3>Fallen behind?</h3>
          <p className={styles.muted}>
            Choose saved tasks or course material and review a manageable
            catch-up proposal. School deadlines stay visible beside your
            personal targets.
          </p>
        </div>
        <Link href="/recovery">Review a catch-up proposal →</Link>
      </div>
    </section>
  );
}
