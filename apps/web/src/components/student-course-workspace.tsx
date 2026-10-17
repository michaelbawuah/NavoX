"use client";

import Link from "next/link";
import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { productFeatures } from "../lib/product-features";
import {
  CoursePrivacySession,
  type CoursePrivacyState,
  type CourseProof,
  courseSearchCurrent,
  observeCoursePrivacy,
} from "../lib/student-course-privacy";
import {
  type CourseAssignments,
  type CourseEmailCandidate,
  type CourseEmailCandidates,
  type CourseEmails,
  type CourseMaterial,
  type CourseMaterialDetail,
  type CourseMaterialKind,
  type CourseOverview,
  type CourseQuote,
  type CourseSearch,
  courseEmailUrl,
  courseMaterialUrl,
  courseOverviewDraft,
  coursePercentHundredths,
  coursePolicyQuote,
  courseRequest,
  readCourseAssignments,
  readCourseEmailCandidates,
  readCourseMaterialDetail,
  readCourseReceipt,
  readCourseSaveReceipt,
  readCourseSearch,
  uploadCourseMaterial,
} from "../lib/student-coursework";
import { studentError, studentWriteUncertain } from "../lib/student-schedule";
import styles from "./student-course-workspace.module.css";

type Tab =
  | "overview"
  | "assignments"
  | "materials"
  | "notes"
  | "syllabus"
  | "emails"
  | "search";
const tabs: [Tab, string][] = [
  ["overview", "Overview"],
  ["assignments", "Assignments"],
  ["materials", "Materials"],
  ["notes", "Notes"],
  ["syllabus", "Syllabus"],
  ["emails", "Emails"],
  ["search", "Search"],
];
function due(value: string | null) {
  return value
    ? `${new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: "UTC",
      }).format(new Date(value))} UTC`
    : "No deadline supplied";
}

export function CourseSource({
  quote,
  materials,
  onOpen,
}: {
  quote: CourseQuote;
  materials: CourseMaterial[];
  onOpen: (id: string, page: number, revision: number) => void;
}) {
  const material = materials.find((row) => row.id === quote.material_id);
  const current = material?.revision === quote.revision;
  return (
    <p className={styles.hint}>
      {current ? (
        <button
          type="button"
          className={styles.textButton}
          onClick={() => onOpen(quote.material_id, quote.page, quote.revision)}
        >
          {material.title} · page {quote.page} · version {quote.revision}
        </button>
      ) : (
        "This saved source changed or is no longer available. Review the original fact."
      )}
    </p>
  );
}

export function CoursePolicySummary({
  overview,
  materials,
  onOpen,
}: {
  overview: CourseOverview;
  materials: CourseMaterial[];
  onOpen: (id: string, page: number, revision: number) => void;
}) {
  return (
    <div className={styles.notice}>
      <h3>Course AI policy</h3>
      {overview.ai_policy ? (
        <>
          <blockquote className={styles.quote}>
            {overview.ai_policy.quote}
          </blockquote>
          <CourseSource
            quote={overview.ai_policy}
            materials={materials}
            onOpen={onOpen}
          />
          <p>
            This is the policy passage you saved. It does not automatically
            authorize AI use. Follow your instructor’s rules for each
            assignment.
          </p>
        </>
      ) : (
        <p>
          No policy passage saved. Check your syllabus or ask your instructor
          before using AI on assessed work.
        </p>
      )}
      <p className={styles.hint}>
        The default is tutoring, outlining, and reviewing your own work.
      </p>
    </div>
  );
}

export function CourseOverviewForm({
  overview,
  disabled,
  onSave,
  onClose,
}: {
  overview: CourseOverview;
  disabled: boolean;
  onSave: (value: ReturnType<typeof courseOverviewDraft>) => Promise<boolean>;
  onClose: () => void;
}) {
  const id = useId(),
    [value, setValue] = useState(() => {
      const draft = courseOverviewDraft(overview);
      return {
        ...draft,
        staff: draft.staff.map((row, index) => ({
          ...row,
          editKey: `${id}-person-${index}`,
        })),
        grade_breakdown: draft.grade_breakdown.map((row, index) => ({
          ...row,
          editKey: `${id}-weight-${index}`,
        })),
      };
    }),
    [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const amounts = value.grade_breakdown.map((row) =>
      coursePercentHundredths(row.weight_percent),
    );
    if (
      amounts.some((amount) => amount === null) ||
      amounts.reduce<number>((sum, amount) => sum + (amount ?? 0), 0) > 10000
    ) {
      setError(
        "Use percentages with up to two decimal places, totaling no more than 100%.",
      );
      return;
    }
    if (
      await onSave({
        ...value,
        staff: value.staff.map(({ editKey: _, ...row }) => row),
        grade_breakdown: value.grade_breakdown.map(
          ({ editKey: _, ...row }) => row,
        ),
      })
    )
      onClose();
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.heading}>
        <h3>Edit course details</h3>
        <button type="button" disabled={disabled} onClick={onClose}>
          Cancel
        </button>
      </div>
      <p className={styles.hint}>
        Add details you know. Editing a sourced fact removes its old citation so
        it won’t appear verified by a changed source.
      </p>
      <fieldset disabled={disabled}>
        <legend>People and office hours</legend>
        {value.staff.map((person, index) => (
          <div className={styles.row} key={person.editKey}>
            <label>
              Name
              <input
                required
                maxLength={120}
                value={person.name}
                onChange={(event) =>
                  setValue({
                    ...value,
                    staff: value.staff.map((row, i) =>
                      i === index
                        ? { ...row, name: event.target.value, source: null }
                        : row,
                    ),
                  })
                }
              />
            </label>
            <label>
              Role
              <input
                required
                maxLength={80}
                value={person.role}
                onChange={(event) =>
                  setValue({
                    ...value,
                    staff: value.staff.map((row, i) =>
                      i === index
                        ? { ...row, role: event.target.value, source: null }
                        : row,
                    ),
                  })
                }
              />
            </label>
            <label>
              Email (optional)
              <input
                type="email"
                maxLength={320}
                value={person.email ?? ""}
                onChange={(event) =>
                  setValue({
                    ...value,
                    staff: value.staff.map((row, i) =>
                      i === index
                        ? {
                            ...row,
                            email: event.target.value || null,
                            source: null,
                          }
                        : row,
                    ),
                  })
                }
              />
            </label>
            <label>
              Office hours (optional)
              <input
                maxLength={500}
                value={person.office_hours ?? ""}
                onChange={(event) =>
                  setValue({
                    ...value,
                    staff: value.staff.map((row, i) =>
                      i === index
                        ? {
                            ...row,
                            office_hours: event.target.value || null,
                            source: null,
                          }
                        : row,
                    ),
                  })
                }
              />
            </label>
            <button
              type="button"
              onClick={() =>
                setValue({
                  ...value,
                  staff: value.staff.filter((_, i) => i !== index),
                })
              }
            >
              Remove person {index + 1}
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={value.staff.length >= 20}
          onClick={() =>
            setValue({
              ...value,
              staff: [
                ...value.staff,
                {
                  name: "",
                  editKey: crypto.randomUUID(),
                  role: "",
                  email: null,
                  office_hours: null,
                  source: null,
                },
              ],
            })
          }
        >
          ＋ Add a person
        </button>
      </fieldset>
      <fieldset disabled={disabled}>
        <legend>Grading breakdown</legend>
        {value.grade_breakdown.map((weight, index) => (
          <div className={styles.row} key={weight.editKey}>
            <label>
              Category
              <input
                required
                maxLength={120}
                value={weight.name}
                onChange={(event) =>
                  setValue({
                    ...value,
                    grade_breakdown: value.grade_breakdown.map((row, i) =>
                      i === index
                        ? { ...row, name: event.target.value, source: null }
                        : row,
                    ),
                  })
                }
              />
            </label>
            <label>
              Percent
              <input
                required
                inputMode="decimal"
                pattern="[0-9]{1,3}(\.[0-9]{1,2})?"
                value={weight.weight_percent}
                onChange={(event) =>
                  setValue({
                    ...value,
                    grade_breakdown: value.grade_breakdown.map((row, i) =>
                      i === index
                        ? {
                            ...row,
                            weight_percent: event.target.value,
                            source: null,
                          }
                        : row,
                    ),
                  })
                }
              />
            </label>
            <button
              type="button"
              onClick={() =>
                setValue({
                  ...value,
                  grade_breakdown: value.grade_breakdown.filter(
                    (_, i) => i !== index,
                  ),
                })
              }
            >
              Remove category {index + 1}
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={value.grade_breakdown.length >= 20}
          onClick={() =>
            setValue({
              ...value,
              grade_breakdown: [
                ...value.grade_breakdown,
                {
                  name: "",
                  weight_percent: "",
                  source: null,
                  editKey: crypto.randomUUID(),
                },
              ],
            })
          }
        >
          ＋ Add a category
        </button>
      </fieldset>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <button type="submit" className={styles.primary} disabled={disabled}>
        Save course details
      </button>
    </form>
  );
}

export function CourseMaterialForm({
  kind,
  material,
  disabled,
  onSave,
  onClose,
}: {
  kind: CourseMaterialKind;
  material?: CourseMaterialDetail;
  disabled: boolean;
  onSave: (value: {
    title: string;
    kind: CourseMaterialKind;
    content: Blob;
    media_type: string;
    material_id?: string;
    revision: number;
  }) => Promise<boolean>;
  onClose: () => void;
}) {
  const id = useId(),
    [title, setTitle] = useState(material?.title ?? ""),
    [text, setText] = useState(
      material?.kind === "note" ? material.pages.join("\n\f\n") : "",
    ),
    [file, setFile] = useState<File | null>(null),
    [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (!title.trim()) {
      setError("Give this a title so you can find it later.");
      return;
    }
    let content: Blob, media_type: string;
    if (kind === "note") {
      if (!text.trim() || text.length > 200000) {
        setError("Write a nonempty note of up to 200,000 characters.");
        return;
      }
      content = new Blob([text], { type: "text/plain" });
      media_type = "text/plain";
    } else {
      if (!file) {
        setError("Choose a file, then save it when you’re ready.");
        return;
      }
      if (file.size === 0 || file.size > 5 * 1024 * 1024) {
        setError("Choose a nonempty file no larger than 5 MiB.");
        return;
      }
      const suffix = file.name.split(".").at(-1)?.toLowerCase();
      media_type =
        suffix === "pdf"
          ? "application/pdf"
          : suffix === "md"
            ? "text/markdown"
            : suffix === "txt"
              ? "text/plain"
              : "";
      if (!media_type) {
        setError("Choose a .pdf, .txt, or .md file.");
        return;
      }
      content = file;
    }
    if (
      await onSave({
        title: title.trim(),
        kind,
        content,
        media_type,
        revision: material?.revision ?? 0,
        ...(material ? { material_id: material.id } : {}),
      })
    )
      onClose();
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.heading}>
        <h3>
          {material ? "Replace saved " : "Add "}
          {kind === "note"
            ? "note"
            : kind === "syllabus"
              ? "syllabus"
              : "material"}
        </h3>
        <button type="button" disabled={disabled} onClick={onClose}>
          Cancel
        </button>
      </div>
      <fieldset disabled={disabled}>
        <label htmlFor={`${id}-title`}>
          Title
          <input
            id={`${id}-title`}
            required
            maxLength={160}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        {kind === "note" ? (
          <label htmlFor={`${id}-text`}>
            Your note
            <textarea
              id={`${id}-text`}
              required
              rows={10}
              maxLength={200000}
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </label>
        ) : (
          <>
            <label htmlFor={`${id}-file`}>
              File
              <input
                id={`${id}-file`}
                type="file"
                accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  setError("");
                }}
              />
            </label>
            <p className={styles.hint}>
              PDF, text, or Markdown · up to 5 MiB and 100 pages. Image-only
              PDFs need a text version. Selecting a file does not save it.
            </p>
            {file && (
              <p>
                {file.name} · {(file.size / 1024).toFixed(0)} KiB ready to save
              </p>
            )}
          </>
        )}
      </fieldset>
      <p className={styles.hint}>
        Saved privately to this course. Text is extracted locally for reading
        and search; this does not call an AI model.
      </p>
      {material && (
        <p className={styles.warning}>
          Replacing this material removes course facts and policy passages that
          cite its old version. Review those details afterward.
        </p>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <button className={styles.primary} disabled={disabled} type="submit">
        {material ? "Save replacement" : "Save to this course"}
      </button>
    </form>
  );
}

export function CourseMaterialReader({
  courseId,
  material,
  page,
  onPage,
}: {
  courseId: string;
  material: CourseMaterialDetail;
  page: number;
  onPage: (page: number) => void;
}) {
  return (
    <section className={styles.reader}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>
            {material.kind} · version {material.revision}
          </p>
          <h3>{material.title}</h3>
        </div>
        <a
          href={courseMaterialUrl(courseId, material.id)}
          target="_blank"
          rel="noreferrer"
        >
          Download original
        </a>
      </div>
      <label>
        Page{" "}
        <select
          value={page}
          onChange={(event) => onPage(Number(event.target.value))}
        >
          {Array.from(
            { length: material.page_count },
            (_, index) => index + 1,
          ).map((number) => (
            <option value={number} key={number}>
              {number} of {material.page_count}
            </option>
          ))}
        </select>
      </label>
      <p className={styles.hint}>
        Source: {material.title}, version {material.revision}, page {page}. Page
        numbers refer to this saved text extraction.{" "}
        <a href={`#course-source-${material.id}-page-${page}`}>
          Link to this page
        </a>
      </p>
      <pre
        id={`course-source-${material.id}-page-${page}`}
        className={styles.page}
      >
        {material.pages[page - 1] || "No readable text on this page."}
      </pre>
    </section>
  );
}

export function CoursePolicyForm({
  overview,
  materials,
  disabled,
  loadMaterial,
  onSave,
  onClose,
}: {
  overview: CourseOverview;
  materials: CourseMaterial[];
  disabled: boolean;
  loadMaterial: (id: string) => Promise<CourseMaterialDetail | null>;
  onSave: (quote: CourseQuote | null) => Promise<boolean>;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<CourseMaterialDetail | null>(null),
    [materialId, setMaterialId] = useState(""),
    [page, setPage] = useState(1),
    [quote, setQuote] = useState(""),
    [reading, setReading] = useState(false),
    [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(
    () => () => {
      ++generation.current;
    },
    [],
  );
  async function choose(id: string) {
    const request = ++generation.current;
    setMaterialId(id);
    setSelected(null);
    setQuote("");
    setPage(1);
    setError("");
    if (!id) return;
    setReading(true);
    const material = await loadMaterial(id);
    if (request !== generation.current) return;
    setReading(false);
    if (material?.kind !== "syllabus") {
      setError("Choose an available syllabus and review its current version.");
      return;
    }
    setSelected(material);
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      if (!selected) throw new Error("Choose a saved syllabus first.");
      if (await onSave(coursePolicyQuote(selected, page, quote))) onClose();
    } catch (cause) {
      setError(studentError(cause));
    }
  }
  return (
    <form className={styles.form} onSubmit={submit}>
      <div className={styles.heading}>
        <h3>Save the exact policy passage</h3>
        <button type="button" disabled={disabled} onClick={onClose}>
          Cancel
        </button>
      </div>
      <label>
        Saved syllabus
        <select
          disabled={disabled || reading}
          value={materialId}
          onChange={(event) => void choose(event.target.value)}
        >
          <option value="">Choose a syllabus</option>
          {materials
            .filter((row) => row.kind === "syllabus")
            .map((row) => (
              <option key={row.id} value={row.id}>
                {row.title} · version {row.revision}
              </option>
            ))}
        </select>
      </label>
      {reading && <p role="status">Opening the saved syllabus…</p>}
      {selected && (
        <>
          <label>
            Source page
            <select
              disabled={disabled}
              value={page}
              onChange={(event) => {
                setPage(Number(event.target.value));
                setQuote("");
              }}
            >
              {Array.from(
                { length: selected.page_count },
                (_, index) => index + 1,
              ).map((number) => (
                <option key={number} value={number}>
                  {number}
                </option>
              ))}
            </select>
          </label>
          <pre className={styles.page}>{selected.pages[page - 1]}</pre>
          <label>
            Exact quote (up to 1,000 characters)
            <textarea
              disabled={disabled}
              required
              maxLength={1000}
              rows={5}
              value={quote}
              onChange={(event) => setQuote(event.target.value)}
            />
          </label>
          <p className={styles.hint}>
            This records a source passage. It does not decide what AI use your
            instructor permits.
          </p>
        </>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      <div className={styles.actions}>
        <button
          className={styles.primary}
          type="submit"
          disabled={disabled || !selected || reading}
        >
          Save reviewed policy passage
        </button>
        {overview.ai_policy && (
          <button
            type="button"
            disabled={disabled}
            onClick={async () => {
              if (await onSave(null)) onClose();
            }}
          >
            Remove saved policy passage
          </button>
        )}
      </div>
    </form>
  );
}

export function CourseAssignmentList({ value }: { value: CourseAssignments }) {
  return (
    <>
      <h3>Your study tasks</h3>
      {value.tasks.length ? (
        value.tasks.map((task) => (
          <article key={task.id} className={styles.item}>
            <strong>{task.title}</strong>
            <p>
              {task.remaining_minutes} minutes left · {task.status} ·{" "}
              {due(task.due_at)}
            </p>
            {task.source_needs_review && (
              <p className={styles.warning}>
                {task.source_review_notice ??
                  "The school calendar source needs review in Planner."}
              </p>
            )}
          </article>
        ))
      ) : (
        <p className={styles.empty}>No study tasks saved for this course.</p>
      )}
      <h3>From your confirmed school calendars</h3>
      <p className={styles.hint}>
        Calendar entries stay separate until you review one as a study task in
        Connections. A deadline is not a submission.
      </p>
      {value.feed_items.length ? (
        value.feed_items.map((item) => (
          <article key={item.id} className={styles.item}>
            <strong>{item.title}</strong>
            <p>
              {item.kind} · {due(item.due_at)}
              {item.cancelled ? " · Cancelled at source" : ""}
              {item.conflicted
                ? " · Conflicting sources — review before planning"
                : ""}
            </p>
          </article>
        ))
      ) : (
        <p className={styles.empty}>
          No calendar entries matched to this course.
        </p>
      )}
    </>
  );
}

export function CourseEmailPicker({
  courseName,
  disabled,
  onSearch,
  onLink,
}: {
  courseName: string;
  disabled: boolean;
  onSearch: (query: string) => Promise<CourseEmailCandidates>;
  onLink: (candidate: CourseEmailCandidate) => Promise<boolean>;
}) {
  const [query, setQuery] = useState(""),
    [result, setResult] = useState<CourseEmailCandidates | null>(null),
    [selected, setSelected] = useState<CourseEmailCandidate | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const alive = useRef(true),
    generation = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      ++generation.current;
    };
  }, []);
  async function find(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || disabled) return;
    setError("");
    const clean = query.trim();
    if (
      clean.length < 2 ||
      clean.length > 120 ||
      clean.split(/\s+/).length > 8
    ) {
      setError("Search using 2 to 120 characters and up to eight words.");
      return;
    }
    setBusy(true);
    setSelected(null);
    setConfirmed(false);
    setResult(null);
    const request = ++generation.current;
    try {
      const value = await onSearch(clean);
      if (alive.current && request === generation.current) setResult(value);
    } catch (cause) {
      if (alive.current && request === generation.current)
        setError(studentError(cause));
    } finally {
      if (alive.current && request === generation.current) setBusy(false);
    }
  }
  return (
    <section className={styles.form}>
      <h3>Choose an email for this course</h3>
      <p className={styles.hint}>
        Search saved email titles, sender labels, or saved Inbox item titles.
        Messages without saved labels cannot appear here. A “Saved item” label
        is your Inbox item title, not the original email subject. This does not
        fetch your mailbox.
      </p>
      <form className={styles.search} onSubmit={find}>
        <label>
          Find a saved email
          <input
            type="search"
            minLength={2}
            maxLength={120}
            required
            disabled={disabled || busy}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button disabled={disabled || busy} type="submit">
          {busy ? "Searching saved emails…" : "Find saved emails"}
        </button>
      </form>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {result && (
        <>
          <p className={styles.hint}>{result.notice}</p>
          {!result.candidates.length && (
            <p className={styles.empty}>
              No matching saved email labels. Try another phrase or review your
              Inbox connection.
            </p>
          )}
          {result.candidates.map((row) => (
            <article className={styles.item} key={row.source_id}>
              <strong>
                {row.title ?? row.display_label ?? "No saved subject"}
              </strong>
              {!row.title && row.display_label && (
                <p className={styles.hint}>
                  Saved Inbox item label · original email subject unavailable
                </p>
              )}
              <p>{row.sender_label ?? "No saved sender label"}</p>
              <button
                type="button"
                disabled={disabled}
                aria-pressed={selected?.source_id === row.source_id}
                onClick={() => {
                  setSelected(row);
                  setConfirmed(false);
                }}
              >
                Review this email
              </button>
            </article>
          ))}
          {result.limit_reached && (
            <p className={styles.hint}>
              This search covers a limited set of saved messages. Try a more
              specific phrase if yours is missing.
            </p>
          )}
        </>
      )}
      {selected && (
        <div className={styles.notice}>
          <h3>Link this email to {courseName}?</h3>
          <p>
            <strong>
              {selected.title ?? selected.display_label ?? "No saved subject"}
            </strong>
            <br />
            {selected.sender_label ?? "No saved sender label"}
          </p>
          <p>
            Only a reference to this message is linked. The original email stays
            in your mailbox; its subject and body are not copied into course
            storage.
          </p>
          <label>
            <input
              type="checkbox"
              disabled={disabled}
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />{" "}
            I chose this message for this course.
          </label>
          <button
            type="button"
            className={styles.primary}
            disabled={disabled || !confirmed}
            onClick={async () => {
              if (await onLink(selected)) {
                if (alive.current) {
                  setSelected(null);
                  setConfirmed(false);
                  setResult(null);
                  setQuery("");
                }
              }
            }}
          >
            Link reviewed email
          </button>
        </div>
      )}
    </section>
  );
}

function CourseWorkspaceAccount({
  courseId,
  proof,
  privacy,
  readEpoch,
}: {
  courseId: string;
  proof: CourseProof;
  privacy: CoursePrivacySession;
  readEpoch: number;
}) {
  const [overview, setOverview] = useState<CourseOverview | null>(
      proof.overview,
    ),
    [materials, setMaterials] = useState<CourseMaterial[]>(proof.materials),
    [assignments, setAssignments] = useState<CourseAssignments>(
      proof.assignments,
    ),
    [emails, setEmails] = useState<CourseEmails>(proof.emails),
    [tab, setTab] = useState<Tab>("overview"),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [editor, setEditor] = useState<
      "overview" | "policy" | CourseMaterialKind | null
    >(null),
    [replacement, setReplacement] = useState<
      CourseMaterialDetail | undefined
    >(),
    [selected, setSelected] = useState<CourseMaterialDetail | null>(null),
    [page, setPage] = useState(1),
    [reading, setReading] = useState(false),
    [deleting, setDeleting] = useState<CourseMaterial | null>(null),
    [deleteConfirmed, setDeleteConfirmed] = useState(false),
    [query, setQuery] = useState(""),
    [search, setSearch] = useState<CourseSearch | null>(null),
    [searching, setSearching] = useState(false),
    [epoch] = useState(0);
  const generation = useRef(0),
    alive = useRef(true),
    operation = useRef(false),
    readerGeneration = useRef(0),
    searchGeneration = useRef(0);
  const selectedReadEpoch = useRef(readEpoch),
    resultReadEpoch = useRef(readEpoch);
  const load = useCallback(
    async (_reset = false) => privacy.refresh(),
    [privacy],
  );
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      ++generation.current;
      ++readerGeneration.current;
      ++searchGeneration.current;
    };
  }, []);
  useEffect(() => {
    setOverview(proof.overview);
    setMaterials(proof.materials);
    setAssignments(proof.assignments);
    setEmails(proof.emails);
    setLoading(false);
    setError("");
    setUncertain(false);
  }, [proof]);
  useEffect(() => {
    ++readerGeneration.current;
    ++searchGeneration.current;
    if (selectedReadEpoch.current !== readEpoch) setSelected(null);
    if (resultReadEpoch.current !== readEpoch) setSearch(null);
    setReading(false);
    setSearching(false);
    setError("");
  }, [readEpoch]);
  const read = useCallback(
    <T,>(path: string, parse: (value: unknown) => T) =>
      privacy.read((signal) =>
        courseRequest(courseId, path, parse, "GET", undefined, signal),
      ),
    [courseId, privacy],
  );
  const locked = busy || loading || uncertain || !!overview?.archived;
  async function mutate(
    action: () => Promise<unknown>,
    message: string,
    after?: () => void,
  ) {
    if (operation.current || locked) return false;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      await privacy.write(action);
      if (!alive.current) return false;
      ++readerGeneration.current;
      ++searchGeneration.current;
      setReading(false);
      setSearching(false);
      after?.();
      setNotice(message);
      await load();
      return true;
    } catch (cause) {
      if (alive.current) {
        setError(studentError(cause));
        if (studentWriteUncertain(cause)) setUncertain(true);
      }
      return false;
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function material(id: string) {
    try {
      const detail = await read(`/materials/${id}`, readCourseMaterialDetail);
      const expected = proof.materials.find((row) => row.id === id);
      if (
        !expected ||
        detail.id !== id ||
        detail.revision !== expected.revision ||
        detail.page_count !== expected.page_count
      ) {
        privacy.rejectSource();
        return null;
      }
      return detail;
    } catch {
      return null;
    }
  }
  async function openMaterial(id: string, nextPage = 1, revision?: number) {
    const request = ++readerGeneration.current;
    setReading(true);
    setError("");
    const detail = await material(id);
    if (!alive.current || request !== readerGeneration.current) return;
    setReading(false);
    if (!detail) return;
    if (
      (revision !== undefined && detail.revision !== revision) ||
      nextPage < 1 ||
      nextPage > detail.page_count
    ) {
      privacy.rejectSource();
      return;
    }
    selectedReadEpoch.current = readEpoch;
    setSelected(detail);
    setPage(nextPage);
    setReplacement(undefined);
    setEditor(null);
    setTab(
      detail.kind === "note"
        ? "notes"
        : detail.kind === "syllabus"
          ? "syllabus"
          : "materials",
    );
  }
  function source(id: string, nextPage: number, revision: number) {
    void openMaterial(id, nextPage, revision);
  }
  async function saveOverview(value: ReturnType<typeof courseOverviewDraft>) {
    return mutate(
      () =>
        courseRequest(courseId, "/overview", readCourseSaveReceipt, "PUT", {
          ...value,
          request_id: crypto.randomUUID(),
        }),
      "Course details saved.",
    );
  }
  async function find(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) return;
    const request = ++searchGeneration.current;
    setSearching(true);
    setSearch(null);
    setError("");
    try {
      const result = await read(
        `/search?q=${encodeURIComponent(query.trim())}`,
        readCourseSearch,
      );
      if (!alive.current || request !== searchGeneration.current) return;
      if (!courseSearchCurrent(result, proof, courseId)) {
        privacy.rejectSource();
        return;
      }
      resultReadEpoch.current = readEpoch;
      setSearch(result);
    } catch (cause) {
      if (alive.current && request === searchGeneration.current)
        setError(studentError(cause));
    } finally {
      if (alive.current && request === searchGeneration.current)
        setSearching(false);
    }
  }
  return (
    <section className={styles.panel} aria-label="Course workspace">
      <div className={`${styles.card} ${styles.hero}`}>
        <div className={styles.heading}>
          <div>
            <Link href="/classes" className={styles.back}>
              ← All classes
            </Link>
            <p className={styles.eyebrow}>
              {overview?.course.code || "Your course"}
            </p>
            <h2>{overview?.course.name || "Opening your course…"}</h2>
            <p className={styles.muted}>
              Keep the details, materials, and work for this class in one place.
            </p>
          </div>
          {overview && (
            <span className={styles.badge}>
              {overview.archived
                ? "Archived · read only"
                : "Private to your account"}
            </span>
          )}
        </div>
        {overview?.archived && (
          <p className={styles.warning}>
            This course is archived. You can read its history and materials.
            Restore it in Classes before making changes.
          </p>
        )}
      </div>
      <nav className={styles.tabs} aria-label="Course sections">
        {tabs.map(([key, label]) => (
          <button
            type="button"
            key={key}
            aria-current={tab === key ? "page" : undefined}
            onClick={() => {
              setTab(key);
              setEditor(null);
              setReplacement(undefined);
              setDeleting(null);
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <div aria-live="polite">
        {notice && <p className={styles.notice}>{notice}</p>}
        {loading && (
          <p className={styles.muted}>Opening saved course details…</p>
        )}
        {reading && <p className={styles.muted}>Opening the saved material…</p>}
      </div>
      {uncertain && (
        <p className={styles.warning}>
          The last change may have saved. Reload saved details before making
          another change.
        </p>
      )}
      {error && (
        <div className={styles.error} role="alert">
          <p>{error}</p>
          <button
            type="button"
            disabled={busy || loading}
            onClick={() => void load(true)}
          >
            Reload saved course
          </button>
        </div>
      )}
      {overview && (
        <div className={styles.card}>
          {tab === "overview" &&
            (editor === "overview" ? (
              <CourseOverviewForm
                key={`overview-${epoch}`}
                overview={overview}
                disabled={locked}
                onSave={saveOverview}
                onClose={() => setEditor(null)}
              />
            ) : (
              <>
                <div className={styles.heading}>
                  <h3>The details you need</h3>
                  <button
                    disabled={locked}
                    type="button"
                    onClick={() => setEditor("overview")}
                  >
                    Edit details
                  </button>
                </div>
                <div className={styles.columns}>
                  <section>
                    <h3>People & office hours</h3>
                    {overview.staff.length ? (
                      overview.staff.map((person) => (
                        <article
                          key={JSON.stringify(person)}
                          className={styles.item}
                        >
                          <strong>{person.name}</strong>
                          <p>{person.role}</p>
                          {person.email && <p>{person.email}</p>}
                          <p>
                            {person.office_hours || "Office hours not saved"}
                          </p>
                          {person.source ? (
                            <CourseSource
                              quote={person.source}
                              materials={materials}
                              onOpen={source}
                            />
                          ) : (
                            <p className={styles.hint}>
                              Added by you · no source passage attached
                            </p>
                          )}
                        </article>
                      ))
                    ) : (
                      <p className={styles.empty}>
                        No people saved yet. Add an instructor, TA, or office
                        hours when you have them.
                      </p>
                    )}
                  </section>
                  <section>
                    <h3>Grading breakdown</h3>
                    {overview.grade_breakdown.length ? (
                      <>
                        <ul className={styles.list}>
                          {overview.grade_breakdown.map((row) => (
                            <li key={JSON.stringify(row)}>
                              <strong>
                                {row.name} · {row.weight_percent}%
                              </strong>
                              {row.source ? (
                                <CourseSource
                                  quote={row.source}
                                  materials={materials}
                                  onOpen={source}
                                />
                              ) : (
                                <p className={styles.hint}>
                                  Added by you · no source passage attached
                                </p>
                              )}
                            </li>
                          ))}
                        </ul>
                        <p className={styles.hint}>
                          {overview.grade_weight_total}% recorded. Missing
                          categories are unknown.
                        </p>
                      </>
                    ) : (
                      <p className={styles.empty}>
                        No weights saved. Add the breakdown from your syllabus
                        when you’re ready.
                      </p>
                    )}
                  </section>
                </div>
                <CoursePolicySummary
                  overview={overview}
                  materials={materials}
                  onOpen={source}
                />
              </>
            ))}
          {tab === "assignments" && (
            <>
              <div className={styles.heading}>
                <h3>Work for this course</h3>
                <div className={styles.actions}>
                  <Link href="/planner">Open Planner</Link>
                  <Link href="/connections">Review school calendars</Link>
                </div>
              </div>
              <CourseAssignmentList value={assignments} />
              {assignments.next_offset !== null && (
                <button
                  type="button"
                  disabled={busy || loading}
                  onClick={async () => {
                    if (operation.current) return;
                    operation.current = true;
                    setBusy(true);
                    try {
                      const result = await read(
                        `/assignments?offset=${assignments.next_offset}`,
                        readCourseAssignments,
                      );
                      if (alive.current)
                        setAssignments((current) => ({
                          ...current,
                          feed_items: [
                            ...current.feed_items,
                            ...result.feed_items.filter(
                              (item) =>
                                !current.feed_items.some(
                                  (row) => row.id === item.id,
                                ),
                            ),
                          ],
                          next_offset: result.next_offset,
                        }));
                    } catch (cause) {
                      if (alive.current) setError(studentError(cause));
                    } finally {
                      operation.current = false;
                      if (alive.current) setBusy(false);
                    }
                  }}
                >
                  Load more calendar entries
                </button>
              )}
            </>
          )}
          {(tab === "materials" || tab === "notes" || tab === "syllabus") && (
            <>
              <div className={styles.heading}>
                <div>
                  <h3>
                    {tab === "notes"
                      ? "Your course notes"
                      : tab === "syllabus"
                        ? "Syllabus & course policy"
                        : "Your course materials"}
                  </h3>
                  <p className={styles.hint}>
                    {tab === "notes"
                      ? "A private place for what you want to remember."
                      : "Save a file, read its text, and find the exact source later."}
                  </p>
                </div>
                <button
                  disabled={locked}
                  type="button"
                  onClick={() => {
                    setReplacement(undefined);
                    setEditor(
                      tab === "notes"
                        ? "note"
                        : tab === "syllabus"
                          ? "syllabus"
                          : "material",
                    );
                  }}
                >
                  {tab === "notes"
                    ? "＋ Write a note"
                    : tab === "syllabus"
                      ? "＋ Add syllabus"
                      : "＋ Add material"}
                </button>
              </div>
              {(editor === "note" ||
                editor === "material" ||
                editor === "syllabus") && (
                <CourseMaterialForm
                  key={`${editor}-${replacement?.id ?? "new"}-${epoch}`}
                  kind={editor}
                  material={replacement}
                  disabled={locked}
                  onClose={() => {
                    setEditor(null);
                    setReplacement(undefined);
                  }}
                  onSave={(value) =>
                    mutate(
                      () =>
                        uploadCourseMaterial(courseId, {
                          ...value,
                          request_id: crypto.randomUUID(),
                        }),
                      "Saved privately to this course.",
                      () => {
                        setSelected(null);
                        setSearch(null);
                        if (value.material_id) {
                          setMaterials((current) =>
                            current.filter(
                              (row) => row.id !== value.material_id,
                            ),
                          );
                          setOverview(null);
                        }
                      },
                    )
                  }
                />
              )}
              <div className={styles.materialGrid}>
                {materials
                  .filter(
                    (row) =>
                      row.kind ===
                      (tab === "notes"
                        ? "note"
                        : tab === "syllabus"
                          ? "syllabus"
                          : "material"),
                  )
                  .map((row) => (
                    <article className={styles.material} key={row.id}>
                      <button
                        type="button"
                        className={styles.materialTitle}
                        onClick={() => void openMaterial(row.id)}
                      >
                        {row.title}
                      </button>
                      <p className={styles.hint}>
                        {row.page_count}{" "}
                        {row.page_count === 1 ? "page" : "pages"} · version{" "}
                        {row.revision} · {(row.byte_size / 1024).toFixed(0)} KiB
                      </p>
                      <div className={styles.actions}>
                        <button
                          type="button"
                          disabled={locked}
                          onClick={async () => {
                            const detail = await material(row.id);
                            if (detail && alive.current) {
                              setReplacement(detail);
                              setEditor(row.kind);
                            }
                          }}
                        >
                          {row.kind === "note" ? "Edit" : "Replace"}
                        </button>
                        <button
                          type="button"
                          disabled={locked}
                          className={styles.danger}
                          onClick={() => {
                            setDeleting(row);
                            setDeleteConfirmed(false);
                          }}
                        >
                          Delete
                        </button>
                      </div>
                    </article>
                  ))}
              </div>
              {!materials.some(
                (row) =>
                  row.kind ===
                  (tab === "notes"
                    ? "note"
                    : tab === "syllabus"
                      ? "syllabus"
                      : "material"),
              ) && (
                <p className={styles.empty}>
                  Nothing saved here yet. Add your own{" "}
                  {tab === "notes"
                    ? "first note"
                    : tab === "syllabus"
                      ? "syllabus"
                      : "material"}{" "}
                  when you’re ready.
                </p>
              )}
              {deleting && (
                <div className={styles.warning}>
                  <h3>Delete “{deleting.title}”?</h3>
                  <p>
                    This permanently deletes this private file and its extracted
                    text. Course policy passages, people, and grading facts that
                    cite this material will also be removed. Other materials,
                    your timetable, and the original file on your device stay.
                  </p>
                  <label>
                    <input
                      type="checkbox"
                      checked={deleteConfirmed}
                      disabled={locked}
                      onChange={(event) =>
                        setDeleteConfirmed(event.target.checked)
                      }
                    />{" "}
                    I understand what will be deleted.
                  </label>
                  <div className={styles.actions}>
                    <button
                      type="button"
                      disabled={locked || !deleteConfirmed}
                      className={styles.danger}
                      onClick={() =>
                        void mutate(
                          () =>
                            courseRequest(
                              courseId,
                              `/materials/${deleting.id}`,
                              readCourseReceipt,
                              "DELETE",
                              {
                                revision: deleting.revision,
                                request_id: crypto.randomUUID(),
                              },
                            ),
                          "Material and its cited course details deleted.",
                          () => {
                            setMaterials((current) =>
                              current.filter((row) => row.id !== deleting.id),
                            );
                            setSelected(null);
                            setSearch(null);
                            setReplacement(undefined);
                            setDeleting(null);
                            setEditor(null);
                            setOverview(null);
                          },
                        )
                      }
                    >
                      Permanently delete material
                    </button>
                    <button
                      type="button"
                      disabled={locked}
                      onClick={() => setDeleting(null)}
                    >
                      Keep material
                    </button>
                  </div>
                </div>
              )}
              {selected &&
                selectedReadEpoch.current === readEpoch &&
                selected.kind ===
                  (tab === "notes"
                    ? "note"
                    : tab === "syllabus"
                      ? "syllabus"
                      : "material") && (
                  <CourseMaterialReader
                    courseId={courseId}
                    material={selected}
                    page={page}
                    onPage={setPage}
                  />
                )}
              {tab === "syllabus" && (
                <>
                  <CoursePolicySummary
                    overview={overview}
                    materials={materials}
                    onOpen={source}
                  />
                  {editor === "policy" ? (
                    <CoursePolicyForm
                      key={`policy-${epoch}`}
                      overview={overview}
                      materials={materials}
                      disabled={locked}
                      loadMaterial={material}
                      onClose={() => setEditor(null)}
                      onSave={(quote) =>
                        saveOverview({
                          ...courseOverviewDraft(overview),
                          ai_policy: quote,
                        })
                      }
                    />
                  ) : (
                    <button
                      type="button"
                      disabled={
                        locked ||
                        !materials.some((row) => row.kind === "syllabus")
                      }
                      onClick={() => setEditor("policy")}
                    >
                      Review a policy passage
                    </button>
                  )}
                </>
              )}
            </>
          )}
          {tab === "emails" && (
            <>
              <div className={styles.heading}>
                <h3>Emails you linked to this course</h3>
                <Link href="/inbox">Open Inbox</Link>
              </div>
              <p className={styles.hint}>
                Only emails you explicitly link belong here. This view does not
                fetch or classify your mailbox.
              </p>
              {!overview.archived && (
                <CourseEmailPicker
                  key={`emails-${epoch}-${readEpoch}`}
                  courseName={overview.course.name}
                  disabled={locked}
                  onSearch={(query) =>
                    read(
                      `/email-candidates?q=${encodeURIComponent(query)}`,
                      readCourseEmailCandidates,
                    )
                  }
                  onLink={(candidate) =>
                    mutate(
                      () =>
                        courseRequest(
                          courseId,
                          "/email-links",
                          readCourseSaveReceipt,
                          "POST",
                          {
                            source_id: candidate.source_id,
                            source_hash: candidate.source_hash,
                            request_id: crypto.randomUUID(),
                          },
                        ),
                      "Email linked to this course. The original stays in your mailbox.",
                    )
                  }
                />
              )}
              {emails.links.length ? (
                emails.links.map((row, index) => (
                  <article key={row.id} className={styles.item}>
                    <strong>
                      {row.title ??
                        row.display_label ??
                        `Linked course email ${index + 1} · subject unavailable`}
                    </strong>
                    {!row.title && row.display_label && (
                      <p className={styles.hint}>
                        Saved Inbox item label · original email subject
                        unavailable
                      </p>
                    )}
                    {row.sender_label && <p>{row.sender_label}</p>}
                    <p className={styles.hint}>
                      The original stays in your mailbox. Its subject and body
                      are not copied into this course.
                    </p>
                    <div className={styles.actions}>
                      {courseEmailUrl(row.source_open_url) ? (
                        <a
                          href={
                            courseEmailUrl(row.source_open_url) ?? undefined
                          }
                          target="_blank"
                          rel="noreferrer"
                        >
                          Open original email
                        </a>
                      ) : (
                        <Link href="/inbox">Find in Inbox</Link>
                      )}
                      <button
                        type="button"
                        disabled={locked}
                        onClick={() =>
                          void mutate(
                            () =>
                              courseRequest(
                                courseId,
                                `/email-links/${row.id}`,
                                readCourseReceipt,
                                "DELETE",
                                {
                                  revision: row.revision,
                                  request_id: crypto.randomUUID(),
                                },
                              ),
                            "Course association removed. The original email stays in your mailbox.",
                            () =>
                              setEmails((current) => ({
                                ...current,
                                links: current.links.filter(
                                  (item) => item.id !== row.id,
                                ),
                              })),
                          )
                        }
                      >
                        Remove course link
                      </button>
                    </div>
                  </article>
                ))
              ) : (
                <p className={styles.empty}>
                  No emails linked. Your inbox stays separate until you choose a
                  message for this course.
                </p>
              )}
              {emails.unavailable_count > 0 && (
                <p className={styles.warning}>
                  {emails.unavailable_count} linked{" "}
                  {emails.unavailable_count === 1 ? "email is" : "emails are"}{" "}
                  unavailable with your current mailbox connection.
                </p>
              )}
            </>
          )}
          {tab === "search" && (
            <>
              <h3>Find something in this course</h3>
              <p className={styles.hint}>
                Search the text of your saved notes, syllabus, and materials for
                this course only.
              </p>
              <form className={styles.search} onSubmit={find}>
                <label>
                  Words or phrase
                  <input
                    type="search"
                    required
                    maxLength={100}
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                </label>
                <button type="submit" disabled={searching || loading}>
                  {searching ? "Searching…" : "Search this course"}
                </button>
              </form>
              {search && resultReadEpoch.current === readEpoch && (
                <>
                  <p role="status">
                    {search.results.length
                      ? `${search.results.length} matching passages`
                      : "No matching passages in the saved text."}
                    {search.limit_reached
                      ? " Showing the first matches. Try a more specific phrase."
                      : ""}
                  </p>
                  {search.results.map((row) => (
                    <article
                      key={`${row.material_id}-${row.revision}-${row.page}`}
                      className={styles.item}
                    >
                      <button
                        type="button"
                        className={styles.textButton}
                        onClick={() =>
                          void openMaterial(
                            row.material_id,
                            row.page,
                            row.revision,
                          )
                        }
                      >
                        {row.title} · page {row.page} · version {row.revision}
                      </button>
                      <p className={styles.snippet}>{row.snippet}</p>
                    </article>
                  ))}
                </>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
export function CoursePrivateSurface({
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
export function StudentCourseWorkspace({
  accountId,
  courseId,
  enabled = productFeatures.studentCoursework,
}: {
  accountId: string;
  courseId: string;
  enabled?: boolean;
}) {
  return enabled ? (
    <CourseWorkspaceSession
      key={`${accountId}:${courseId}`}
      accountId={accountId}
      courseId={courseId}
    />
  ) : (
    <p>Course workspaces are not available here yet.</p>
  );
}
function CourseWorkspaceSession({
  accountId,
  courseId,
}: {
  accountId: string;
  courseId: string;
}) {
  const [privacy] = useState(
    () => new CoursePrivacySession(accountId, courseId),
  );
  const state = useSyncExternalStore(
    privacy.subscribe,
    privacy.getSnapshot,
    privacy.getSnapshot,
  );
  useEffect(() => {
    const cleanup = observeCoursePrivacy(
      () => privacy.clear(),
      privacy.suspend,
      () => {
        if (privacy.getSnapshot().masked) void privacy.refresh();
      },
      {
        window,
        document,
        channel:
          typeof BroadcastChannel === "undefined"
            ? null
            : new BroadcastChannel("navox-account-session"),
      },
    );
    if (document.visibilityState === "visible" && document.hasFocus())
      void privacy.refresh();
    return cleanup;
  }, [privacy]);
  return (
    <CourseAccessView
      state={state}
      onClear={() => privacy.clear()}
      onReload={() => void privacy.refresh()}
    >
      {state.proof && (
        <CourseWorkspaceAccount
          key={state.epoch}
          courseId={courseId}
          proof={state.proof}
          privacy={privacy}
          readEpoch={state.readEpoch}
        />
      )}
    </CourseAccessView>
  );
}
export function CourseAccessView({
  state,
  onClear,
  onReload,
  children,
}: {
  state: CoursePrivacyState;
  onClear: () => void;
  onReload: () => void;
  children?: ReactNode;
}) {
  return (
    <section aria-label="Private course access" aria-busy={state.checking}>
      <div className={styles.accessControls}>
        <p className={styles.hint}>
          Keep a copy of unsaved edits elsewhere. Edits clear if saved sources
          change, you leave this page, or you stay away from this window for 10
          minutes.
        </p>
        <button type="button" onClick={onClear}>
          Clear course view
        </button>
      </div>
      {state.masked && (
        <div className={styles.warning} role="status">
          <p>
            Your course is hidden while current access and saved sources are
            checked.
          </p>
          {!state.checking && (
            <button
              type="button"
              disabled={state.blocked || state.writePending}
              onClick={onReload}
            >
              Check current course
            </button>
          )}
        </div>
      )}
      {!state.proof && (
        <div className={styles.warning}>
          <p role="status">{state.notice}</p>
          {state.error && <p role="alert">{state.error}</p>}
          {state.uncertain && (
            <p>
              The last change may have saved. Reload the saved course before
              making another change.
            </p>
          )}
          <button
            type="button"
            disabled={state.checking || state.blocked || state.writePending}
            onClick={onReload}
          >
            {state.checking ? "Checking saved course…" : "Reload saved course"}
          </button>
        </div>
      )}
      <CoursePrivateSurface masked={state.masked}>
        {state.proof && children}
      </CoursePrivateSurface>
    </section>
  );
}
