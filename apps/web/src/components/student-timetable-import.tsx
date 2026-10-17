"use client";

import Image from "next/image";
import { useEffect, useId, useRef, useState } from "react";
import {
  readStudentScreenshot,
  type StudentScreenshotCandidate,
  type StudentScreenshotPreview,
} from "../lib/student-imports";
import {
  readStudentCourse,
  readStudentImportPreview,
  readStudentList,
  type StudentImportCandidate,
  type StudentImportPreview,
  type StudentProfile,
  studentDayNames,
  studentError,
  studentLocation,
  studentMeetingKinds,
  studentRequest,
  studentWriteUncertain,
} from "../lib/student-schedule";
import styles from "./student-schedule.module.css";

export function StudentImportReview({
  preview,
  candidates,
  selected,
  disabled,
  onEdit,
  onSelect,
}: {
  preview: StudentImportPreview;
  candidates: StudentImportCandidate[];
  selected: string[];
  disabled: boolean;
  onEdit: (id: string, change: Partial<StudentImportCandidate>) => void;
  onSelect: (id: string, checked: boolean) => void;
}) {
  const id = useId();
  return (
    <div className={styles.previewList}>
      <div>
        <h3>Check what we found.</h3>
        <p className={styles.muted}>
          {candidates.length} sections · {preview.timezone.replaceAll("_", " ")}
          . Select only the classes you want to save.
        </p>
      </div>
      {[...new Set(preview.warnings)].map((warning) => (
        <p className={styles.warning} key={warning}>
          {warning}
        </p>
      ))}
      {candidates.map((row, index) => (
        <article key={row.id} className={styles.previewCard}>
          <label className={styles.check}>
            <input
              type="checkbox"
              disabled={disabled}
              checked={selected.includes(row.id)}
              onChange={(event) => onSelect(row.id, event.target.checked)}
            />
            <strong>Include section {index + 1}</strong>
          </label>
          <p className={styles.courseMeta}>
            {row.start_time}–{row.end_time} ·{" "}
            {row.timezone.replaceAll("_", " ")} · {row.dates.length} dates (
            {row.dates[0]} to {row.dates.at(-1)})
          </p>
          <fieldset
            className={styles.fields}
            disabled={disabled || !selected.includes(row.id)}
          >
            <label htmlFor={`${id}-${index}-code`}>
              Course code
              <input
                id={`${id}-${index}-code`}
                value={row.course_code}
                maxLength={40}
                onChange={(event) =>
                  onEdit(row.id, { course_code: event.target.value })
                }
              />
            </label>
            <label htmlFor={`${id}-${index}-name`}>
              Course name
              <input
                id={`${id}-${index}-name`}
                required
                value={row.course_name}
                maxLength={200}
                onChange={(event) =>
                  onEdit(row.id, { course_name: event.target.value })
                }
              />
            </label>
            <label htmlFor={`${id}-${index}-kind`}>
              Section type
              <select
                id={`${id}-${index}-kind`}
                value={row.kind}
                onChange={(event) =>
                  onEdit(row.id, {
                    kind: event.target.value as StudentImportCandidate["kind"],
                  })
                }
              >
                {studentMeetingKinds.map((kind) => (
                  <option key={kind}>{kind}</option>
                ))}
              </select>
            </label>
            <label htmlFor={`${id}-${index}-instructor`}>
              Professor / TA
              <input
                id={`${id}-${index}-instructor`}
                value={row.instructor ?? ""}
                maxLength={200}
                onChange={(event) =>
                  onEdit(row.id, { instructor: event.target.value || null })
                }
              />
            </label>
            <label htmlFor={`${id}-${index}-building`}>
              Building / location
              <input
                id={`${id}-${index}-building`}
                value={row.building ?? ""}
                maxLength={200}
                onChange={(event) =>
                  onEdit(row.id, { building: event.target.value || null })
                }
              />
            </label>
            <label htmlFor={`${id}-${index}-room`}>
              Room
              <input
                id={`${id}-${index}-room`}
                value={row.room ?? ""}
                maxLength={80}
                onChange={(event) =>
                  onEdit(row.id, { room: event.target.value || null })
                }
              />
            </label>
          </fieldset>
          <details className={styles.disclosure}>
            <summary>Review all {row.dates.length} dates</summary>
            <p className={styles.hint}>{row.dates.join(" · ")}</p>
          </details>
        </article>
      ))}
      <p className={styles.hint}>
        File dates, times, and time zones stay as previewed. After importing,
        use Edit times or One-day change to correct them.
      </p>
    </div>
  );
}

export function StudentTimetableImport({
  mode,
  profile,
  disabled,
  onClose,
  onSaved,
  onUncertain,
  onReviewCourse,
}: {
  mode: "ics" | "screenshot";
  profile: StudentProfile;
  disabled: boolean;
  onClose: () => void;
  onSaved: () => Promise<void>;
  onUncertain: (message: string) => void;
  onReviewCourse: (candidate: StudentScreenshotCandidate) => void;
}) {
  const id = useId();
  const [file, setFile] = useState<File | null>(null);
  const [imageUrl, setImageUrl] = useState("");
  const [start, setStart] = useState(profile.term_start ?? "");
  const [end, setEnd] = useState(profile.term_end ?? "");
  const [preview, setPreview] = useState<StudentImportPreview | null>(null);
  const [candidates, setCandidates] = useState<StudentImportCandidate[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [screenshot, setScreenshot] = useState<StudentScreenshotPreview | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const generation = useRef(0);
  const requestId = useRef("");
  useEffect(
    () => () => {
      ++generation.current;
    },
    [],
  );
  useEffect(() => {
    if (mode !== "screenshot" || !file) {
      setImageUrl("");
      return;
    }
    const url = URL.createObjectURL(file);
    setImageUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file, mode]);
  function chooseFile(value: File | null) {
    ++generation.current;
    setFile(value);
    setPreview(null);
    setScreenshot(null);
    setCandidates([]);
    setSelected([]);
    setError("");
    setConfirmed(false);
    requestId.current = "";
  }
  async function read() {
    if (!file || busy || disabled) return;
    const version = ++generation.current;
    setBusy(true);
    setError("");
    setPreview(null);
    setScreenshot(null);
    setConfirmed(false);
    try {
      if (mode === "ics") {
        if (file.size > 2_000_000)
          throw new Error("Choose an .ics file smaller than 2 MB.");
        if (!file.name.toLowerCase().endsWith(".ics"))
          throw new Error(
            "Choose an .ics calendar file from your student portal.",
          );
        if (!start || !end || end < start)
          throw new Error(
            "Choose a valid start and end for the term before previewing.",
          );
        const content = await file.text();
        const result = await studentRequest(
          "/imports/preview",
          readStudentImportPreview,
          "POST",
          { content, term_start: start, term_end: end },
        );
        if (version === generation.current) {
          setPreview(result);
          setCandidates(result.candidates);
          setSelected(result.candidates.map((row) => row.id));
          requestId.current = crypto.randomUUID();
        }
      } else {
        const result = await readStudentScreenshot(file);
        if (version === generation.current) setScreenshot(result);
      }
    } catch (cause) {
      if (version === generation.current) setError(studentError(cause));
    } finally {
      if (version === generation.current) setBusy(false);
    }
  }
  async function save() {
    if (!preview || !confirmed || busy || disabled) return;
    const version = ++generation.current;
    setBusy(true);
    setError("");
    try {
      const chosen = candidates.filter((row) => selected.includes(row.id));
      if (!chosen.length || chosen.some((row) => !row.course_name.trim()))
        throw new Error(
          "Select at least one section and give each selected course a name.",
        );
      await studentRequest(
        "/imports/confirm",
        (value) => readStudentList(value, "courses", readStudentCourse),
        "POST",
        {
          preview_id: preview.preview_id,
          preview_hash: preview.preview_hash,
          request_id: requestId.current,
          confirmed: true,
          candidates: chosen,
        },
      );
      if (version === generation.current) await onSaved();
    } catch (cause) {
      if (version === generation.current) {
        setError(studentError(cause));
        if (studentWriteUncertain(cause)) onUncertain(studentError(cause));
      }
    } finally {
      if (version === generation.current) setBusy(false);
    }
  }
  const locked = disabled || busy;
  return (
    <section
      className={styles.form}
      aria-labelledby={`${id}-title`}
      aria-busy={busy}
    >
      <div className={styles.formTitle}>
        <h3 id={`${id}-title`}>
          {mode === "ics"
            ? "Import your timetable"
            : "Read a schedule screenshot"}
        </h3>
        <button
          className={styles.quiet}
          type="button"
          disabled={locked}
          onClick={onClose}
        >
          Close import
        </button>
      </div>
      <p className={styles.muted}>
        {mode === "ics"
          ? "Upload a calendar file from your student portal. Nothing is added until you review and confirm."
          : "Choose a clear screenshot. Reading an image can miss details; review each class, its days, times, and location before saving."}
      </p>
      <div className={styles.upload}>
        <label className={styles.field} htmlFor={`${id}-file`}>
          {mode === "ics"
            ? "Calendar file (.ics, up to 2 MB)"
            : "Screenshot (PNG, JPEG, WebP; up to 5 MB)"}
          <input
            id={`${id}-file`}
            type="file"
            accept={
              mode === "ics"
                ? ".ics,text/calendar"
                : "image/png,image/jpeg,image/webp"
            }
            disabled={locked}
            onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
          />
        </label>
        {mode === "ics" && (
          <fieldset className={styles.fields} disabled={locked}>
            <label htmlFor={`${id}-term-start`}>
              Term starts
              <input
                required
                id={`${id}-term-start`}
                type="date"
                value={start}
                onChange={(event) => {
                  setStart(event.target.value);
                  setPreview(null);
                }}
              />
            </label>
            <label htmlFor={`${id}-term-end`}>
              Term ends
              <input
                required
                id={`${id}-term-end`}
                type="date"
                min={start || undefined}
                value={end}
                onChange={(event) => {
                  setEnd(event.target.value);
                  setPreview(null);
                }}
              />
            </label>
          </fieldset>
        )}
        {mode === "screenshot" && imageUrl && (
          <Image
            className={styles.previewImage}
            src={imageUrl}
            width={1200}
            height={800}
            unoptimized
            alt="Your uploaded timetable to review beside the extracted details"
          />
        )}
        <p className={styles.hint}>
          {mode === "screenshot"
            ? "The image is used to read this preview. It is not added to your timetable or stored as a course file."
            : "Only reviewed schedule details are saved. The original calendar file is discarded after parsing."}
        </p>
        <div className={styles.actions} style={{ marginTop: 0 }}>
          <button
            type="button"
            className={styles.primary}
            disabled={locked || !file}
            onClick={() => void read()}
          >
            {busy
              ? "Reading…"
              : mode === "ics"
                ? "Preview classes"
                : "Read screenshot"}
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      {preview && (
        <>
          <StudentImportReview
            preview={preview}
            candidates={candidates}
            selected={selected}
            disabled={locked}
            onEdit={(candidateId, change) => {
              setCandidates((rows) =>
                rows.map((row) =>
                  row.id === candidateId ? { ...row, ...change } : row,
                ),
              );
              setConfirmed(false);
            }}
            onSelect={(candidateId, checked) => {
              setSelected((ids) =>
                checked
                  ? [...ids, candidateId]
                  : ids.filter((id) => id !== candidateId),
              );
              setConfirmed(false);
            }}
          />
          {candidates.length === 0 ? (
            <p className={styles.warning}>
              No supported timed class meetings were found. You can add a course
              by hand instead.
            </p>
          ) : (
            <>
              <label className={styles.check}>
                <input
                  type="checkbox"
                  disabled={locked}
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                I checked the selected classes, dates, times, and locations.
              </label>
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.primary}
                  disabled={locked || !confirmed || !selected.length}
                  onClick={() => void save()}
                >
                  {busy
                    ? "Saving…"
                    : `Confirm and add ${selected.length} ${selected.length === 1 ? "section" : "sections"}`}
                </button>
                <small>Only the selected sections will be added.</small>
              </div>
            </>
          )}
        </>
      )}
      {screenshot && (
        <>
          <h3>Read it once. Check it twice.</h3>
          {[...new Set(screenshot.warnings)].map((warning) => (
            <p className={styles.warning} key={warning}>
              {warning}
            </p>
          ))}
          <div className={styles.previewList}>
            {screenshot.candidates.map((row) => (
              <article className={styles.previewCard} key={JSON.stringify(row)}>
                <h4>
                  {row.course_code ? `${row.course_code} · ` : ""}
                  {row.course_name}
                </h4>
                <p className={styles.courseMeta}>
                  {studentDayNames(row.weekdays) || "Days need review"} ·{" "}
                  {row.start_time ?? "Start time missing"}–
                  {row.end_time ?? "End time missing"} · {studentLocation(row)}
                </p>
                {row.uncertainties.length > 0 && (
                  <p className={styles.warning}>
                    {row.uncertainties.join(" ")}
                  </p>
                )}
                <div className={styles.actions}>
                  <button
                    type="button"
                    disabled={locked}
                    onClick={() => onReviewCourse(row)}
                  >
                    Review this class
                  </button>
                  <small>
                    You’ll confirm term dates and the time zone in the form.
                  </small>
                </div>
              </article>
            ))}
          </div>
          {screenshot.candidates.length === 0 && (
            <p className={styles.warning}>
              No reliable class rows were found. Keep the screenshot handy and
              add your classes by hand.
            </p>
          )}
          <details className={styles.disclosure}>
            <summary>Read extracted text</summary>
            <p className={styles.hint} style={{ whiteSpace: "pre-wrap" }}>
              {screenshot.extracted_text || "No readable text was found."}
            </p>
          </details>
          <p className={styles.hint}>
            Screenshot text is a starting point, not a confirmed timetable.
          </p>
        </>
      )}
    </section>
  );
}
