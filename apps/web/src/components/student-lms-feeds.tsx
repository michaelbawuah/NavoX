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
  type LmsCalendarFeed,
  type LmsCalendarItem,
  type LmsFeedMatches,
  type LmsFeedPlatform,
  type LmsFeedPreview,
  LmsFeedRequestError,
  type LmsSavedItem,
  lmsCourseMatchesComplete,
  lmsFeedAttention,
  lmsFeedGuides,
  lmsFeedPlatforms,
  lmsFeedRequest,
  lmsNeedsFreshLink,
  lmsPlatformLabels,
  lmsSafeSourceUrl,
  readLmsFeed,
  readLmsFeedMatches,
  readLmsFeeds,
  readLmsItems,
  readLmsPreview,
  readLmsRefresh,
} from "../lib/student-lms-feeds";
import {
  readStudyTask,
  readStudyTasks,
  type StudyTaskFields,
  studyRequest,
} from "../lib/student-planning";
import {
  readStudentCourse,
  readStudentList,
  readStudentProfile,
  type StudentCourse,
  type StudentProfile,
  studentClock,
  studentDateInZone,
  studentDayLabel,
  studentError,
  studentRequest,
  studentWriteUncertain,
} from "../lib/student-schedule";
import { StudentStudyTaskForm } from "./student-daily-plan";
import styles from "./student-lms-feeds.module.css";

export function LmsPlatformGuide({ platform }: { platform: LmsFeedPlatform }) {
  const guide = lmsFeedGuides[platform];
  return (
    <aside className={styles.guide}>
      <h3>
        {platform === "google_classroom"
          ? "Use your Google Calendar connection"
          : `Find your ${lmsPlatformLabels[platform]} calendar link`}
      </h3>
      <ol>
        {guide.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <p className={styles.hint}>{guide.note}</p>
      {platform === "google_classroom" && (
        <Link href="/connections">Open Google connections</Link>
      )}
      {platform === "other" && (
        <Link href="/classes">Import a timetable file in Classes</Link>
      )}
    </aside>
  );
}

export function LmsCourseMatchFields({
  hints,
  matches,
  courses,
  disabled,
  onChange,
}: {
  hints: string[];
  matches: Record<string, string | null | undefined>;
  courses: StudentCourse[];
  disabled: boolean;
  onChange: (hint: string, value: string | null | undefined) => void;
}) {
  const id = useId();
  return (
    <div className={styles.matches}>
      {hints.length === 0 ? (
        <p className={styles.muted}>
          No course labels were found. These calendar items will stay
          unassigned.
        </p>
      ) : (
        hints.map((hint, index) => (
          <div className={styles.match} key={hint}>
            <span>{hint}</span>
            <label className={styles.field} htmlFor={`${id}-${index}`}>
              Match to your course
              <select
                id={`${id}-${index}`}
                disabled={disabled}
                value={
                  Object.hasOwn(matches, hint)
                    ? matches[hint] === null
                      ? "__unassigned__"
                      : (matches[hint] ?? "")
                    : ""
                }
                onChange={(event) =>
                  onChange(
                    hint,
                    event.target.value === ""
                      ? undefined
                      : event.target.value === "__unassigned__"
                        ? null
                        : event.target.value,
                  )
                }
              >
                <option value="">Choose a course or keep unassigned</option>
                <option value="__unassigned__">Keep unassigned</option>
                {courses
                  .filter((course) => !course.archived)
                  .map((course) => (
                    <option value={course.id} key={course.id}>
                      {course.code ? `${course.code} · ` : ""}
                      {course.name}
                    </option>
                  ))}
              </select>
            </label>
          </div>
        ))
      )}
      <p className={styles.hint}>
        Missing a course? <Link href="/classes">Add it in Classes</Link>, then
        reload the saved course list. No course is created automatically.
      </p>
    </div>
  );
}

export function LmsCalendarItemCard({
  item,
  timezone,
  status,
  courseLabel,
  children,
}: {
  item: LmsCalendarItem;
  timezone: string;
  status?: "cancelled" | "conflicted";
  courseLabel?: string;
  children?: React.ReactNode;
}) {
  const due = item.due_at;
  const starts = item.starts_at;
  const source = lmsSafeSourceUrl(item.source_url);
  return (
    <article className={styles.item}>
      <h4>{item.title}</h4>
      <p>
        {item.kind}
        {courseLabel && ` · ${courseLabel}`}
        {item.course_hint && !courseLabel && ` · ${item.course_hint}`}
      </p>
      {due ? (
        <p>
          Due {studentDayLabel(studentDateInZone(due, timezone))} at{" "}
          {studentClock(due, timezone)}
          {item.all_day && " · uses your selected all-day deadline time"}
        </p>
      ) : starts ? (
        <p>
          {studentDayLabel(studentDateInZone(starts, timezone))} ·{" "}
          {studentClock(starts, timezone)}
          {item.ends_at && `–${studentClock(item.ends_at, timezone)}`}
        </p>
      ) : (
        <p>No timed deadline was provided.</p>
      )}
      {item.location && <p>{item.location}</p>}
      {status && (
        <p className={styles.warning}>
          {status === "cancelled"
            ? "Cancelled in the source calendar."
            : "The sources disagree. Review the school calendar before planning this item."}
        </p>
      )}
      {item.details && (
        <details className={styles.disclosure}>
          <summary>Details from the calendar</summary>
          <p style={{ whiteSpace: "pre-wrap" }}>{item.details}</p>
        </details>
      )}
      {source && (
        <p>
          <a
            href={source}
            target="_blank"
            rel="noreferrer noopener"
            referrerPolicy="no-referrer"
          >
            Open source calendar item ↗
          </a>
        </p>
      )}
      {children}
    </article>
  );
}

export function LmsFeedPreviewReview({
  preview,
  courses,
  timezone,
  disabled,
  name,
  matches,
  confirmed,
  onName,
  onMatch,
  onConfirm,
  onConnect,
  onDiscard,
}: {
  preview: LmsFeedPreview;
  courses: StudentCourse[];
  timezone: string;
  disabled: boolean;
  name: string;
  matches: Record<string, string | null | undefined>;
  confirmed: boolean;
  onName: (value: string) => void;
  onMatch: (hint: string, value: string | null | undefined) => void;
  onConfirm: (value: boolean) => void;
  onConnect: () => void;
  onDiscard: () => void;
}) {
  const id = useId();
  const [shown, setShown] = useState(8);
  return (
    <section className={styles.form} aria-labelledby={`${id}-title`}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Review before connecting</p>
          <h3 id={`${id}-title`}>
            {preview.count} calendar items from {preview.domain}
          </h3>
          <p className={styles.muted}>
            Nothing will sync automatically until you confirm this connection.
          </p>
        </div>
        <button
          type="button"
          disabled={disabled}
          className={styles.quiet}
          onClick={onDiscard}
        >
          Discard preview
        </button>
      </div>
      <label className={styles.field} htmlFor={`${id}-name`}>
        A name for this calendar
        <input
          id={`${id}-name`}
          required
          maxLength={120}
          disabled={disabled}
          value={name}
          onChange={(event) => onName(event.target.value)}
          placeholder="My course deadlines"
        />
      </label>
      <p className={styles.hint}>
        {preview.all_day_count} all-day{" "}
        {preview.all_day_count === 1 ? "item" : "items"} · deadline time{" "}
        {preview.all_day_due_time} · {timezone.replaceAll("_", " ")}
      </p>
      <LmsCourseMatchFields
        hints={preview.course_hints}
        matches={matches}
        courses={courses}
        disabled={disabled}
        onChange={onMatch}
      />
      <details className={styles.disclosure}>
        <summary>Review calendar items</summary>
        <div className={styles.items}>
          {preview.items.slice(0, shown).map((item) => (
            <LmsCalendarItemCard
              key={`${item.external_id}:${item.occurrence_id}`}
              item={item}
              timezone={timezone}
              status={item.cancelled ? "cancelled" : undefined}
            />
          ))}
        </div>
        {shown < preview.items.length && (
          <div className={styles.actions}>
            <button
              type="button"
              onClick={() => setShown((value) => value + 20)}
            >
              Show more items ({preview.items.length - shown} remaining)
            </button>
          </div>
        )}
      </details>
      <label className={styles.check}>
        <input
          type="checkbox"
          disabled={disabled}
          checked={confirmed}
          onChange={(event) => onConfirm(event.target.checked)}
        />
        I checked the course matches, source time zone, and all-day deadline
        time. Connect this calendar and keep its dates updated.
      </label>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.primary}
          disabled={
            disabled ||
            !confirmed ||
            !name.trim() ||
            !lmsCourseMatchesComplete(
              preview.course_hints,
              matches,
              courses
                .filter((course) => !course.archived)
                .map((course) => course.id),
            )
          }
          onClick={onConnect}
        >
          {disabled ? "Connecting…" : "Connect reviewed calendar"}
        </button>
        <span className={styles.hint}>
          Read-only dates. No grades, announcements, or submissions.
        </span>
      </div>
    </section>
  );
}

function FeedMatchEditor({
  feed,
  details,
  courses,
  disabled,
  onSave,
  onClose,
}: {
  feed: LmsCalendarFeed;
  details: LmsFeedMatches;
  courses: StudentCourse[];
  disabled: boolean;
  onSave: (matches: Record<string, string | null>) => Promise<boolean>;
  onClose: () => void;
}) {
  const [matches, setMatches] = useState<
    Record<string, string | null | undefined>
  >(() =>
    Object.fromEntries(
      details.course_hints
        .filter(
          (hint) =>
            !details.unconfirmed_hints.includes(hint) &&
            Object.hasOwn(details.course_matches, hint),
        )
        .map((hint) => [hint, details.course_matches[hint]]),
    ),
  );
  const [confirmed, setConfirmed] = useState(false);
  return (
    <section className={styles.form}>
      <div className={styles.heading}>
        <h3>Review courses for {feed.name || feed.domain}</h3>
        <button
          type="button"
          className={styles.quiet}
          disabled={disabled}
          onClick={onClose}
        >
          Cancel
        </button>
      </div>
      <LmsCourseMatchFields
        hints={details.course_hints}
        matches={matches}
        courses={courses}
        disabled={disabled}
        onChange={(hint, value) => {
          setMatches((current) => ({ ...current, [hint]: value }));
          setConfirmed(false);
        }}
      />
      <label className={styles.check}>
        <input
          type="checkbox"
          checked={confirmed}
          disabled={disabled}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        I reviewed every course label, including the items I’m keeping
        unassigned.
      </label>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.primary}
          disabled={
            disabled ||
            !confirmed ||
            !lmsCourseMatchesComplete(
              details.course_hints,
              matches,
              courses
                .filter((course) => !course.archived)
                .map((course) => course.id),
            )
          }
          onClick={async () => {
            if (
              await onSave(
                Object.fromEntries(
                  details.course_hints.map((hint) => [
                    hint,
                    matches[hint] ?? null,
                  ]),
                ),
              )
            )
              onClose();
          }}
        >
          Save reviewed matches
        </button>
      </div>
    </section>
  );
}

function StudentLmsFeedsAccount({
  planningEnabled,
}: {
  planningEnabled: boolean;
}) {
  const [feeds, setFeeds] = useState<LmsCalendarFeed[]>([]),
    [items, setItems] = useState<LmsSavedItem[]>([]),
    [cursor, setCursor] = useState<string | null>(null),
    [courses, setCourses] = useState<StudentCourse[]>([]),
    [profile, setProfile] = useState<StudentProfile | null>(null),
    [imported, setImported] = useState<string[]>([]),
    [planningReady, setPlanningReady] = useState(false);
  const [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [adding, setAdding] = useState(false),
    [platform, setPlatform] = useState<LmsFeedPlatform>("canvas"),
    [url, setUrl] = useState(""),
    [zone, setZone] = useState(""),
    [allDay, setAllDay] = useState("");
  const [preview, setPreview] = useState<LmsFeedPreview | null>(null),
    [name, setName] = useState(""),
    [matches, setMatches] = useState<Record<string, string | null | undefined>>(
      {},
    ),
    [confirmed, setConfirmed] = useState(false),
    [deleting, setDeleting] = useState<LmsCalendarFeed | null>(null),
    [deleteConfirmed, setDeleteConfirmed] = useState(false),
    [matchEditor, setMatchEditor] = useState<{
      feed: LmsCalendarFeed;
      details: LmsFeedMatches;
    } | null>(null),
    [assignment, setAssignment] = useState<LmsSavedItem | null>(null),
    [epoch, setEpoch] = useState(0);
  const generation = useRef(0),
    alive = useRef(true),
    operation = useRef(false),
    confirmId = useRef("");
  const uncertainPlanning = useRef(false);
  const id = useId();
  const load = useCallback(
    async (reset = false) => {
      const request = ++generation.current;
      setLoading(true);
      try {
        const [nextFeeds, page, nextCourses, nextProfile, nextTasks] =
          await Promise.all([
            lmsFeedRequest("", readLmsFeeds),
            lmsFeedRequest("/items", readLmsItems),
            studentRequest("/courses?archived=false", (value) =>
              readStudentList(value, "courses", readStudentCourse),
            ),
            studentRequest("/profile", readStudentProfile),
            planningEnabled
              ? studyRequest("/tasks", readStudyTasks).catch(() => null)
              : Promise.resolve([]),
          ]);
        if (request !== generation.current) return false;
        setFeeds(nextFeeds);
        setItems(page.items);
        setCursor(page.next_cursor);
        setCourses(nextCourses);
        setProfile(nextProfile);
        setPlanningReady(planningEnabled && nextTasks !== null);
        setImported(
          (nextTasks ?? []).flatMap((task) =>
            task.source_item_id ? [task.source_item_id] : [],
          ),
        );
        setZone((current) => current || nextProfile.timezone);
        setError("");
        if (reset) {
          if (uncertainPlanning.current && nextTasks === null) {
            setError(
              "Study tasks could not be checked. Open Planner or try reloading again before repeating the import.",
            );
            return false;
          }
          uncertainPlanning.current = false;
          setUncertain(false);
          setPreview(null);
          setAdding(false);
          setUrl("");
          setName("");
          setMatches({});
          setConfirmed(false);
          setDeleting(null);
          setMatchEditor(null);
          setAssignment(null);
          setEpoch((value) => value + 1);
          setNotice(
            "Saved calendars reloaded. Check the connection list and Planner before trying a change again.",
          );
        }
        return true;
      } catch (cause) {
        if (request === generation.current)
          setError(
            cause instanceof LmsFeedRequestError
              ? cause.message
              : "Saved calendars could not be loaded. Reload to try again.",
          );
        return false;
      } finally {
        if (request === generation.current) setLoading(false);
      }
    },
    [planningEnabled],
  );
  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
      ++generation.current;
    };
  }, [load]);
  const locked = loading || busy || uncertain;
  async function mutate<T>(
    path: string,
    parse: (value: unknown) => T,
    method: string,
    body?: unknown,
  ): Promise<T | null> {
    if (operation.current || uncertain) return null;
    operation.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await lmsFeedRequest(path, parse, method, body);
      if (!alive.current) return null;
      if (method === "DELETE") {
        const forgotten = path.slice(1);
        setFeeds((current) => current.filter((feed) => feed.id !== forgotten));
        setItems([]);
        setAssignment(null);
        setMatchEditor(null);
        setImported([]);
      }
      if (!(await load())) {
        setUncertain(true);
        setNotice(
          "The change finished, but the saved connection list could not be checked. Reload before making another change.",
        );
      }
      return result;
    } catch (cause) {
      if (alive.current) {
        setError(
          cause instanceof LmsFeedRequestError
            ? cause.message
            : "The calendar change could not be checked. Reload saved connections.",
        );
        if (cause instanceof LmsFeedRequestError && cause.uncertain)
          setUncertain(true);
      }
      return null;
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function previewFeed(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (operation.current || locked) return;
    setError("");
    let parsed: URL;
    try {
      parsed = new URL(url.trim());
      if (parsed.protocol !== "https:" || parsed.username || parsed.password)
        throw new Error();
    } catch {
      setError("Paste a private HTTPS calendar link from your school.");
      return;
    }
    operation.current = true;
    setBusy(true);
    try {
      const result = await lmsFeedRequest("/preview", readLmsPreview, "POST", {
        url: url.trim(),
        platform,
        timezone: zone,
        all_day_due_time: allDay,
      });
      if (alive.current) {
        setPreview(result);
        setUrl("");
        setName("");
        setMatches({});
        setConfirmed(false);
        confirmId.current = crypto.randomUUID();
        setNotice("");
        await load();
      }
    } catch (cause) {
      if (alive.current) {
        setError(
          cause instanceof LmsFeedRequestError
            ? cause.message
            : "The calendar preview could not be read. Try a fresh link or use Classes.",
        );
        if (cause instanceof LmsFeedRequestError && cause.uncertain)
          setUncertain(true);
      }
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function discardPreview() {
    if (!preview) return;
    const result = await mutate(`/${preview.feed_id}`, () => true, "DELETE");
    if (result) {
      setPreview(null);
      setConfirmed(false);
      setName("");
      setMatches({});
      setNotice("The preview and its private link were discarded.");
    }
  }
  async function moreItems() {
    if (!cursor || locked) return;
    setBusy(true);
    setError("");
    const version = generation.current;
    try {
      const page = await lmsFeedRequest(
        `/items?after=${encodeURIComponent(cursor)}`,
        readLmsItems,
      );
      if (version === generation.current) {
        setItems((current) => [
          ...new Map(
            [...current, ...page.items].map((row) => [row.id, row]),
          ).values(),
        ]);
        setCursor(page.next_cursor);
      }
    } catch {
      setError(
        "More saved calendar items could not be loaded. Try refreshing the saved list.",
      );
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function saveAssignment(value: StudyTaskFields) {
    if (!assignment || operation.current || uncertain) return false;
    operation.current = true;
    setBusy(true);
    setError("");
    try {
      await studyRequest("/tasks/from-assignment", readStudyTask, "POST", {
        ...value,
        request_id: crypto.randomUUID(),
        source_item_id: assignment.id,
        source_revision: assignment.revision,
        confirmed: true,
      });
      if (!alive.current) return false;
      if (!(await load())) setUncertain(true);
      setNotice(
        "Reviewed assignment added as a study task. Preview a plan in Planner when you’re ready.",
      );
      return true;
    } catch (cause) {
      if (alive.current) {
        setError(studentError(cause));
        if (studentWriteUncertain(cause)) {
          uncertainPlanning.current = true;
          setUncertain(true);
        }
      }
      return false;
    } finally {
      operation.current = false;
      if (alive.current) setBusy(false);
    }
  }
  return (
    <div className={styles.panel}>
      <section className={`${styles.card} ${styles.hero}`}>
        <div className={styles.heading}>
          <div>
            <p className={styles.eyebrow}>Connect your classes</p>
            <h2>Deadlines, without the school-login maze.</h2>
            <p className={styles.muted}>
              Connect a private learning-system calendar link. Review what it
              contains and match it to your own courses.
            </p>
          </div>
          <button
            type="button"
            className={styles.primary}
            disabled={locked || !profile || feeds.length >= 10 || adding}
            onClick={() => {
              setAdding(true);
              setPreview(null);
              setConfirmed(false);
              setAssignment(null);
            }}
          >
            ＋ Add a calendar
          </button>
        </div>
        <p className={styles.hint}>
          Calendars provide dates and events. Grades, announcements, submission
          status, and course files need a separate school-approved connection.
        </p>
      </section>
      {error && (
        <p className={styles.error} role="alert">
          {error}
          {!uncertain && (
            <button
              type="button"
              disabled={busy || loading}
              onClick={() => void load()}
            >
              Reload saved calendars
            </button>
          )}
        </p>
      )}
      {uncertain && (
        <div className={styles.warning} role="alert">
          <strong>Check what saved before trying again.</strong>
          <p>
            Changes are paused until you reload saved connections and study
            tasks. Your private link won’t be shown in an error or saved in this
            browser.
          </p>
          <button
            type="button"
            disabled={busy || loading}
            onClick={() => void load(true)}
          >
            Reload saved connections
          </button>
        </div>
      )}
      {notice && (
        <p className={styles.notice} role="status">
          {notice}
        </p>
      )}
      {adding && profile && (
        <section className={styles.card}>
          <div className={styles.heading}>
            <h3>Which system does your school use?</h3>
            {!preview && (
              <button
                type="button"
                className={styles.quiet}
                disabled={locked}
                onClick={() => {
                  setAdding(false);
                  setUrl("");
                }}
              >
                Not now
              </button>
            )}
          </div>
          <div className={styles.platforms}>
            {lmsFeedPlatforms.map((value) => (
              <button
                key={value}
                type="button"
                disabled={locked || !!preview}
                aria-pressed={platform === value}
                onClick={() => {
                  setPlatform(value);
                  setUrl("");
                  setConfirmed(false);
                }}
              >
                {lmsPlatformLabels[value]}
              </button>
            ))}
          </div>
          <LmsPlatformGuide platform={platform} />
          {platform !== "google_classroom" && !preview && (
            <form className={styles.form} onSubmit={previewFeed}>
              <fieldset className={styles.fields} disabled={locked}>
                <label className={styles.wide} htmlFor={`${id}-url`}>
                  Private calendar link
                  <input
                    id={`${id}-url`}
                    type="password"
                    inputMode="url"
                    autoComplete="off"
                    spellCheck={false}
                    required
                    maxLength={4096}
                    value={url}
                    onChange={(event) => setUrl(event.target.value)}
                  />
                  <span className={styles.hint}>
                    Treat this link like a password. NavoX encrypts it and shows
                    only the calendar’s domain after connecting.
                  </span>
                </label>
                <label htmlFor={`${id}-zone`}>
                  Source calendar time zone
                  <input
                    id={`${id}-zone`}
                    required
                    maxLength={64}
                    value={zone}
                    onChange={(event) => setZone(event.target.value)}
                  />
                  <span className={styles.hint}>
                    Used for entries that don’t name their own zone. Check your
                    school’s zone.
                  </span>
                </label>
                <label htmlFor={`${id}-all-day`}>
                  All-day deadlines are due at
                  <input
                    id={`${id}-all-day`}
                    type="time"
                    required
                    value={allDay}
                    onChange={(event) => setAllDay(event.target.value)}
                  />
                  <span className={styles.hint}>
                    Choose once for this calendar. Review it before connecting.
                  </span>
                </label>
              </fieldset>
              <div className={styles.actions}>
                <button
                  className={styles.primary}
                  type="submit"
                  disabled={locked}
                >
                  {busy ? "Reading calendar…" : "Preview calendar"}
                </button>
                <span className={styles.hint}>
                  One read now. Automatic checks start after confirmation.
                </span>
              </div>
            </form>
          )}
          {preview && (
            <LmsFeedPreviewReview
              key={preview.preview_token}
              preview={preview}
              courses={courses}
              timezone={zone}
              disabled={locked}
              name={name}
              matches={matches}
              confirmed={confirmed}
              onName={(value) => {
                setName(value);
                setConfirmed(false);
              }}
              onMatch={(hint, value) => {
                setMatches((current) => ({ ...current, [hint]: value }));
                setConfirmed(false);
              }}
              onConfirm={setConfirmed}
              onDiscard={() => void discardPreview()}
              onConnect={async () => {
                if (
                  !confirmed ||
                  !lmsCourseMatchesComplete(
                    preview.course_hints,
                    matches,
                    courses
                      .filter((course) => !course.archived)
                      .map((course) => course.id),
                  ) ||
                  !name.trim()
                )
                  return;
                if (name.includes("://")) {
                  setError("Use a short calendar name, not a private link.");
                  return;
                }
                const result = await mutate("", readLmsFeed, "POST", {
                  preview_token: preview.preview_token,
                  preview_hash: preview.preview_hash,
                  request_id: confirmId.current,
                  name: name.trim(),
                  course_matches: Object.fromEntries(
                    preview.course_hints.map((hint) => [
                      hint,
                      matches[hint] ?? null,
                    ]),
                  ),
                  confirmed: true,
                });
                if (result) {
                  setPreview(null);
                  setAdding(false);
                  setConfirmed(false);
                  setNotice(
                    "Calendar connected. Its saved dates are ready to review below.",
                  );
                }
              }}
            />
          )}
        </section>
      )}
      <section className={styles.card} aria-busy={loading}>
        <div className={styles.heading}>
          <div>
            <h3>Your connected calendars</h3>
            <p className={styles.muted}>
              Each link is independent. Pause or remove a calendar whenever you
              need.
            </p>
          </div>
          <button
            type="button"
            disabled={busy || loading}
            onClick={() => void load(uncertain)}
          >
            {loading ? "Refreshing…" : "Refresh saved status"}
          </button>
        </div>
        {loading && !profile ? (
          <p className={styles.muted}>Opening your saved connections…</p>
        ) : feeds.length === 0 ? (
          <p className={styles.empty}>
            No school calendars connected yet. You can still use every class you
            add by hand.
          </p>
        ) : (
          <div className={styles.feedList}>
            {feeds.map((feed) => (
              <article className={styles.feed} key={feed.id}>
                <div className={styles.heading}>
                  <div>
                    <h4>{feed.name || "Unconfirmed calendar preview"}</h4>
                    <p className={styles.hint}>
                      {feed.domain} · {lmsPlatformLabels[feed.platform]}
                    </p>
                  </div>
                  <span
                    className={`${styles.status} ${feed.state !== "active" ? styles.paused : ""}`}
                  >
                    {feed.state === "preview"
                      ? "Needs confirmation"
                      : feed.state === "paused"
                        ? "Paused"
                        : "Connected"}
                  </span>
                </div>
                <p className={styles.hint}>
                  {feed.timezone.replaceAll("_", " ")} · all-day deadlines{" "}
                  {feed.all_day_due_time}
                </p>
                <p className={styles.hint}>
                  {feed.last_synced_at
                    ? `Last checked ${studentDayLabel(studentDateInZone(feed.last_synced_at, feed.timezone))} at ${studentClock(feed.last_synced_at, feed.timezone)}`
                    : "No completed update has been recorded yet."}
                </p>
                {feed.last_error && (
                  <p className={styles.warning}>
                    {lmsFeedAttention(feed.last_error)}
                  </p>
                )}
                {feed.state === "preview" && (
                  <p className={styles.hint}>
                    This preview isn’t connected. Discard it below, or finish
                    the review while its preview is still open.
                  </p>
                )}
                <div className={styles.actions}>
                  {feed.state !== "preview" && (
                    <>
                      <button
                        type="button"
                        disabled={locked}
                        onClick={async () => {
                          if (
                            feed.state === "paused" &&
                            lmsNeedsFreshLink(feed.last_error)
                          ) {
                            setAdding(true);
                            setPlatform(feed.platform);
                            setZone(feed.timezone);
                            setAllDay(feed.all_day_due_time);
                            setUrl("");
                            setPreview(null);
                            setConfirmed(false);
                            return;
                          }
                          const result = await mutate(
                            `/${feed.id}`,
                            readLmsFeed,
                            "PUT",
                            {
                              revision: feed.revision,
                              enabled: feed.state !== "active",
                            },
                          );
                          if (result)
                            setNotice(
                              result.state === "paused"
                                ? "Calendar paused. No automatic updates will run."
                                : "Calendar resumed. Refresh its saved status to check the next update.",
                            );
                        }}
                      >
                        {feed.state === "active"
                          ? "Pause"
                          : lmsNeedsFreshLink(feed.last_error)
                            ? "Review a fresh link"
                            : "Resume"}
                      </button>
                      <button
                        type="button"
                        disabled={locked || feed.state !== "active"}
                        onClick={async () => {
                          const result = await mutate(
                            `/${feed.id}/refresh`,
                            readLmsRefresh,
                            "POST",
                          );
                          if (result)
                            setNotice(
                              "A calendar check was requested. Refresh saved status in a moment to see its result.",
                            );
                        }}
                      >
                        Check for updates
                      </button>
                      <button
                        type="button"
                        disabled={locked}
                        onClick={async () => {
                          if (operation.current) return;
                          setBusy(true);
                          setError("");
                          try {
                            const details = await lmsFeedRequest(
                              `/${feed.id}/matches`,
                              readLmsFeedMatches,
                            );
                            if (alive.current)
                              setMatchEditor({ feed, details });
                          } catch {
                            setError(
                              "Course matches could not be loaded. Reload saved calendars and try again.",
                            );
                          } finally {
                            if (alive.current) setBusy(false);
                          }
                        }}
                      >
                        Review course matches
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    className={styles.danger}
                    disabled={locked}
                    onClick={() => {
                      setDeleting(feed);
                      setDeleteConfirmed(false);
                    }}
                  >
                    Remove
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
        {matchEditor && (
          <FeedMatchEditor
            key={`${matchEditor.feed.id}-${matchEditor.details.revision}-${epoch}`}
            {...matchEditor}
            courses={courses}
            disabled={locked}
            onClose={() => setMatchEditor(null)}
            onSave={async (course_matches) => {
              const result = await mutate(
                `/${matchEditor.feed.id}/matches`,
                readLmsFeed,
                "PUT",
                {
                  revision: matchEditor.details.revision,
                  course_matches,
                  confirmed: true,
                },
              );
              if (result)
                setNotice(
                  "Course matches saved. Calendar items now use the courses you reviewed.",
                );
              return !!result;
            }}
          />
        )}
        {deleting && (
          <div className={styles.form}>
            <h3>Remove {deleting.name || deleting.domain}?</h3>
            <p className={styles.warning}>
              This forgets this calendar’s private link and items unique to it,
              including study tasks and plan copies created from those items.
              Items supplied by another connected calendar stay. Manually added
              courses and tasks stay.
            </p>
            <label className={styles.check}>
              <input
                type="checkbox"
                disabled={locked}
                checked={deleteConfirmed}
                onChange={(event) => setDeleteConfirmed(event.target.checked)}
              />
              I want to remove this calendar and its unique imported data.
            </label>
            <div className={styles.actions}>
              <button
                className={styles.danger}
                type="button"
                disabled={locked || !deleteConfirmed}
                onClick={async () => {
                  const result = await mutate(
                    `/${deleting.id}`,
                    () => true,
                    "DELETE",
                  );
                  if (result) {
                    if (preview?.feed_id === deleting.id) setPreview(null);
                    setDeleting(null);
                    setDeleteConfirmed(false);
                    setNotice(
                      "Calendar and its unique imported data forgotten.",
                    );
                  }
                }}
              >
                Remove calendar
              </button>
              <button
                className={styles.quiet}
                type="button"
                disabled={locked}
                onClick={() => setDeleting(null)}
              >
                Keep calendar
              </button>
            </div>
          </div>
        )}
      </section>
      {profile && (
        <section className={styles.card}>
          <div className={styles.heading}>
            <div>
              <h3>Dates from your school calendars</h3>
              <p className={styles.muted}>
                Review assignments before making them study tasks. A calendar
                event doesn’t create a class or task automatically.
              </p>
            </div>
            <span className={styles.hint}>
              {profile.timezone.replaceAll("_", " ")}
            </span>
          </div>
          {planningEnabled && !planningReady && (
            <p className={styles.hint}>
              Study tasks could not be checked yet.{" "}
              <Link href="/planner">Open Planner</Link> or reload before
              importing an assignment.
            </p>
          )}
          {assignment && planningEnabled && planningReady && (
            <StudentStudyTaskForm
              key={`${assignment.id}-${assignment.revision}-${epoch}`}
              assignment={{
                id: assignment.id,
                revision: assignment.revision,
                course_id: assignment.course_id,
                title: assignment.item.title,
                due_at: assignment.item.due_at,
              }}
              timezone={profile.timezone}
              courses={courses}
              disabled={locked}
              onSave={saveAssignment}
              onClose={() => setAssignment(null)}
            />
          )}
          <div className={styles.items}>
            {items.map((row) => (
              <LmsCalendarItemCard
                key={row.id}
                item={row.item}
                timezone={profile.timezone}
                courseLabel={
                  courses.find((course) => course.id === row.course_id)?.code ||
                  undefined
                }
                status={
                  row.cancelled
                    ? "cancelled"
                    : row.conflicted
                      ? "conflicted"
                      : undefined
                }
              >
                {planningEnabled &&
                  (row.item.kind === "assignment" ||
                    row.item.kind === "exam") &&
                  !row.cancelled &&
                  !row.conflicted && (
                    <div className={styles.actions}>
                      {imported.includes(row.id) ? (
                        <Link href="/planner">View study task in Planner</Link>
                      ) : (
                        <button
                          type="button"
                          disabled={locked}
                          onClick={() => setAssignment(row)}
                        >
                          Review as a study task
                        </button>
                      )}
                    </div>
                  )}
              </LmsCalendarItemCard>
            ))}
          </div>
          {items.length === 0 && (
            <p className={styles.empty}>
              No authorized calendar items are saved yet. A new connection may
              need its first update, or its course matches may need review.
            </p>
          )}
          {cursor && (
            <div className={styles.actions}>
              <button
                type="button"
                disabled={locked}
                onClick={() => void moreItems()}
              >
                Load more saved items
              </button>
            </div>
          )}
          <footer className={styles.foot}>
            <span>
              Calendar updates are read-only. Changing a task in NavoX won’t
              change its school deadline.
            </span>
            <Link href="/classes">Keep my timetable up to date</Link>
          </footer>
        </section>
      )}
    </div>
  );
}
export function StudentLmsFeeds({
  accountId,
  planningEnabled = false,
}: {
  accountId: string;
  planningEnabled?: boolean;
}) {
  return (
    <StudentLmsFeedsAccount key={accountId} planningEnabled={planningEnabled} />
  );
}
