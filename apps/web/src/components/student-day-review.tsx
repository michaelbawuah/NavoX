"use client";

import Link from "next/link";
import {
  type ReactNode,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";
import { productFeatures } from "../lib/product-features";
import {
  type DayReviewHref,
  type DayReviewLinks,
  type DayReviewStatus,
  dayReviewLinkAllowed,
  observeDayReviewPrivacy,
  type ReviewGoals,
  type ReviewOutcomes,
  type ReviewSchedule,
  type ReviewWindow,
  type ReviewWork,
  StudentDayReviewController,
  type StudentDayReviewView,
} from "../lib/student-day-review";
import { studyMinutes } from "../lib/student-planning";
import {
  studentClock,
  studentDateInZone,
  studentDayLabel,
  studentLocation,
} from "../lib/student-schedule";
import styles from "./student-day-review.module.css";

const kinds = [
  ["morning", "Morning"],
  ["evening", "Evening"],
  ["weekly", "Week so far"],
] as const;
function when(value: string, timezone: string) {
  return `${studentDayLabel(studentDateInZone(value, timezone))} at ${studentClock(value, timezone)}`;
}
function dates(window: ReviewWindow, timezone: string) {
  const start = studentDateInZone(window.starts_at, timezone),
    end = studentDateInZone(
      new Date(Date.parse(window.ends_at) - 1).toISOString(),
      timezone,
    );
  return start === end
    ? studentDayLabel(start)
    : `${studentDayLabel(start)} – ${studentDayLabel(end)}`;
}
function period(start: string, end: string) {
  return start === end
    ? studentDayLabel(start)
    : `${studentDayLabel(start)} – ${studentDayLabel(end)}`;
}
function ReviewLink({
  href,
  links,
  children,
}: {
  href: DayReviewHref;
  links: DayReviewLinks;
  children: ReactNode;
}) {
  return dayReviewLinkAllowed(href, links) ? (
    <Link href={href}>
      {children} <span aria-hidden="true">→</span>
    </Link>
  ) : null;
}
function Status({ status }: { status: DayReviewStatus }) {
  return (
    <span
      className={
        status === "incomplete" || status === "unavailable"
          ? styles.partial
          : styles.badge
      }
    >
      {status === "complete"
        ? "Saved records"
        : status === "incomplete"
          ? "Known records only"
          : status === "disabled"
            ? "Not enabled here"
            : "Couldn’t check these records"}
    </span>
  );
}
function SourceMessage({
  status,
  label,
}: {
  status: DayReviewStatus;
  label: string;
}) {
  return status === "disabled" ? (
    <p className={styles.muted}>{label} aren’t available here yet.</p>
  ) : status === "unavailable" ? (
    <p className={styles.warning}>
      {label} could not be checked. Their totals are unknown.
    </p>
  ) : status === "incomplete" ? (
    <p className={styles.warning}>
      Some records are missing or need review. The known records below are a
      partial view.
    </p>
  ) : null;
}
function LimitedList({
  children,
  rest,
  label,
}: {
  children: ReactNode;
  rest: ReactNode;
  label: string;
}) {
  return (
    <>
      {children}
      {rest && (
        <details className={styles.more}>
          <summary>{label}</summary>
          {rest}
        </details>
      )}
    </>
  );
}
export function DayReviewSchedule({
  section,
  title,
  timezone,
  links,
}: {
  section: ReviewSchedule;
  title: string;
  timezone: string;
  links: DayReviewLinks;
}) {
  const available =
    section.status === "complete" || section.status === "incomplete";
  const classes = section.classes.map((item) => (
      <li key={item.id}>
        <div>
          <strong>
            {item.course_code ? `${item.course_code} · ` : ""}
            {item.course_name}
          </strong>
          <span>
            {when(item.start_at, timezone)} –{" "}
            {studentClock(item.end_at, timezone)} · {item.kind}
          </span>
          <span>{studentLocation(item)} · Scheduled</span>
        </div>
      </li>
    )),
    fixed = section.fixed_blocks.map((item) => (
      <li key={item.id}>
        <div>
          <strong>{item.title}</strong>
          <span>
            {when(item.start_at, timezone)} –{" "}
            {studentClock(item.end_at, timezone)} · Scheduled time block
          </span>
        </div>
      </li>
    ));
  return (
    <section className={styles.card}>
      <div className={styles.heading}>
        <h2>{title}</h2>
        <Status status={section.status} />
      </div>
      <p className={styles.hint}>
        {dates(section.window, timezone)} · saved schedule
      </p>
      <SourceMessage status={section.status} label="Timetable records" />
      {available && (
        <>
          <dl className={styles.metrics}>
            <div>
              <dt>Saved class sessions</dt>
              <dd>{section.class_count ?? "Unknown"}</dd>
            </div>
            <div>
              <dt>Saved time blocks</dt>
              <dd>{section.fixed_block_count ?? "Unknown"}</dd>
            </div>
          </dl>
          {section.next_class ? (
            <p className={styles.inset}>
              <strong>Next class</strong>
              <br />
              {section.next_class.course_name} ·{" "}
              {when(section.next_class.start_at, timezone)}
            </p>
          ) : !section.classes_complete ? (
            <p className={styles.hint}>
              The next class is unknown until the timetable is reviewed.
            </p>
          ) : (
            <p className={styles.hint}>
              No later class is saved for these dates.
            </p>
          )}
          <h3>Class sessions</h3>
          {classes.length ? (
            <LimitedList
              rest={
                classes.length > 5 ? (
                  <ul className={styles.records}>{classes.slice(5)}</ul>
                ) : null
              }
              label={`Show ${classes.length - 5} more saved class sessions`}
            >
              <ul className={styles.records}>{classes.slice(0, 5)}</ul>
            </LimitedList>
          ) : (
            <p>
              {section.classes_complete
                ? "No class sessions saved for these dates."
                : "No class sessions could be listed from the known records."}
            </p>
          )}
          <h3>Time blocks</h3>
          {fixed.length ? (
            <LimitedList
              rest={
                fixed.length > 5 ? (
                  <ul className={styles.records}>{fixed.slice(5)}</ul>
                ) : null
              }
              label={`Show ${fixed.length - 5} more saved time blocks`}
            >
              <ul className={styles.records}>{fixed.slice(0, 5)}</ul>
            </LimitedList>
          ) : (
            <p>
              {section.fixed_blocks_complete
                ? "No time blocks saved for these dates."
                : "No time blocks could be listed from the known records."}
            </p>
          )}
        </>
      )}
      <div className={styles.actions}>
        <ReviewLink href="/classes" links={links}>
          Review Classes
        </ReviewLink>
      </div>
    </section>
  );
}
export function DayReviewWork({
  section,
  timezone,
  links,
}: {
  section: ReviewWork;
  timezone: string;
  links: DayReviewLinks;
}) {
  const available =
      section.status === "complete" || section.status === "incomplete",
    totals = section.totals ?? section.known_totals;
  return (
    <section className={styles.card}>
      <div className={styles.heading}>
        <h2>Saved work to review</h2>
        <Status status={section.status} />
      </div>
      <SourceMessage status={section.status} label="Study tasks" />
      {available && (
        <>
          {totals ? (
            <>
              <p className={styles.hint}>
                {section.totals
                  ? "Saved task totals"
                  : "Known task totals only"}{" "}
                · checked through{" "}
                {studentClock(section.window.observed_through, timezone)}
              </p>
              <dl className={styles.metrics}>
                <div>
                  <dt>Tasks with work remaining</dt>
                  <dd>{totals.active_tasks}</dd>
                </div>
                <div>
                  <dt>Saved deadlines overdue</dt>
                  <dd>{totals.overdue_tasks}</dd>
                </div>
                <div>
                  <dt>Saved deadlines for these dates</dt>
                  <dd>{totals.due_in_window_tasks}</dd>
                </div>
              </dl>
            </>
          ) : (
            <p>Task totals are unknown.</p>
          )}
          <p className={styles.hint}>
            The list includes eligible saved tasks across all dates. Deadlines
            in {dates(section.window, timezone)} include upcoming ones. Effort
            is a saved estimate.
          </p>
          {section.items.length ? (
            <ul className={styles.records}>
              {section.items.map((item) => (
                <li key={item.id}>
                  <div>
                    <strong>{item.title}</strong>
                    <span>
                      {item.due_at
                        ? `Saved deadline: ${when(item.due_at, timezone)}`
                        : "No deadline saved"}{" "}
                      · {studyMinutes(item.remaining_minutes)} remaining
                      estimate
                    </span>
                    <span>
                      {item.source_kind === "manual"
                        ? "Manually saved"
                        : item.source_kind === "lms"
                          ? "Saved from a school source"
                          : "Saved from an email source"}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p>
              {section.status === "complete"
                ? "No eligible saved tasks with work remaining."
                : "No tasks could be listed from the known records."}
            </p>
          )}
          {section.has_more && (
            <p className={styles.warning}>
              This list shows the first 20 eligible saved tasks. Open Planner to
              review the rest.
            </p>
          )}
        </>
      )}
      <div className={styles.actions}>
        <ReviewLink href="/planner" links={links}>
          Review Planner
        </ReviewLink>
      </div>
    </section>
  );
}
export function DayReviewOutcomes({
  section,
  timezone,
  links,
}: {
  section: ReviewOutcomes;
  timezone: string;
  links: DayReviewLinks;
}) {
  const available =
      section.status === "complete" || section.status === "incomplete",
    totals = section.totals ?? section.known_totals;
  return (
    <section className={styles.card}>
      <div className={styles.heading}>
        <h2>Study outcomes recorded so far</h2>
        <Status status={section.status} />
      </div>
      <p className={styles.hint}>
        {dates(section.window, timezone)} · through{" "}
        {when(section.window.observed_through, timezone)}
      </p>
      <SourceMessage status={section.status} label="Recorded study outcomes" />
      {available && (
        <>
          {totals && !section.totals && (
            <p className={styles.hint}>Known recorded outcomes only</p>
          )}
          {totals ? (
            <dl className={styles.metrics}>
              <div>
                <dt>Done study blocks</dt>
                <dd>{totals.done_blocks}</dd>
              </div>
              <div>
                <dt>Took longer</dt>
                <dd>{totals.took_longer_blocks}</dd>
              </div>
              <div>
                <dt>Skipped blocks</dt>
                <dd>{totals.skipped_blocks}</dd>
              </div>
              <div>
                <dt>Recorded study minutes</dt>
                <dd>{totals.recorded_minutes}</dd>
              </div>
            </dl>
          ) : (
            <p>Recorded outcome totals are unknown.</p>
          )}
          <p className={styles.muted}>
            These are study-block outcomes recorded in Planner. A Done block
            does not mean an assignment was submitted.
          </p>
        </>
      )}
      <div className={styles.actions}>
        <ReviewLink href="/planner" links={links}>
          Review study records
        </ReviewLink>
      </div>
    </section>
  );
}
export function DayReviewGoals({
  section,
  links,
}: {
  section: ReviewGoals;
  links: DayReviewLinks;
}) {
  const available =
    section.status === "complete" || section.status === "incomplete";
  return (
    <section className={styles.card}>
      <div className={styles.heading}>
        <h2>Your reported goal progress</h2>
        <Status status={section.status} />
      </div>
      <SourceMessage status={section.status} label="Saved goal reports" />
      {available &&
        (section.items.length ? (
          <div className={styles.goals}>
            {section.items.map((goal) => (
              <article className={styles.inset} key={goal.id}>
                <p className={styles.eyebrow}>
                  {goal.category} · Self-reported
                </p>
                <h3>{goal.title}</h3>
                <p className={styles.hint}>
                  {period(goal.progress.period_start, goal.progress.period_end)}{" "}
                  · both dates included ·{" "}
                  {goal.cadence === "weekly"
                    ? "Weeks start Monday"
                    : goal.cadence === "daily"
                      ? "Daily goal period"
                      : "This goal’s own period"}
                </p>
                {goal.tracking === "count" ? (
                  <>
                    <p className={styles.goalCount}>
                      {goal.progress.reported_count === null
                        ? "No count reported"
                        : `${goal.progress.reported_count} of ${goal.progress.target} ${goal.unit}`}
                    </p>
                    <p>
                      {goal.progress.reported_count === null
                        ? "Progress is unknown for this period."
                        : goal.progress.target_met_from_reports
                          ? "Your reported counts meet this period’s target."
                          : `${goal.progress.remaining_count ?? "Unknown"} ${goal.unit} remain toward the target, based on your reports.`}
                    </p>
                  </>
                ) : (
                  <>
                    <p>
                      {goal.progress.qualitative_latest === "unknown"
                        ? "No report in this period. Progress is unknown."
                        : `Latest report: ${goal.progress.qualitative_latest === "yes" ? "Yes" : "No"}${goal.progress.latest_reported_on ? ` for ${studentDayLabel(goal.progress.latest_reported_on)}` : ""}.`}
                    </p>
                    {goal.progress.qualitative_latest !== "unknown" && (
                      <p className={styles.hint}>
                        This answer describes the date you reported.
                      </p>
                    )}
                  </>
                )}
                <p className={styles.hint}>
                  {goal.progress.days_reported}{" "}
                  {goal.progress.days_reported === 1 ? "day" : "days"} reported
                  · {goal.progress.timezone.replaceAll("_", " ")}
                </p>
              </article>
            ))}
          </div>
        ) : (
          <p>
            {section.status === "complete"
              ? "No active goals saved. You can choose whether to add one."
              : "No goal reports could be listed from the known records. Other goal progress is unknown."}
          </p>
        ))}
      <p className={styles.hint}>
        Each goal has its own reporting period. Progress comes from your
        check-ins.
      </p>
      <div className={styles.actions}>
        <ReviewLink href="/goals" links={links}>
          Review Goals
        </ReviewLink>
      </div>
    </section>
  );
}
export function DayReviewContent({
  review,
  links,
}: {
  review: StudentDayReviewView;
  links: DayReviewLinks;
}) {
  return (
    <>
      <section className={`${styles.card} ${styles.summary}`}>
        <p className={styles.eyebrow}>
          {review.kind === "weekly"
            ? "Week so far"
            : review.kind === "morning"
              ? "Morning review"
              : "Evening review"}
        </p>
        <h2>{dates(review.window, review.timezone)}</h2>
        <p className={styles.hint}>
          As of {when(review.server_now, review.timezone)} ·{" "}
          {review.timezone.replaceAll("_", " ")}. All times use this saved time
          zone.
        </p>
        {review.summary_lines.map((line) => (
          <p key={line}>{line}</p>
        ))}
        <p className={styles.hint}>
          {review.kind === "weekly"
            ? "Recorded outcomes are week so far. This week’s saved schedule and deadlines also include upcoming days."
            : "Today’s schedule and saved deadlines can include times after this snapshot."}
        </p>
      </section>
      {review.notices.length > 0 && (
        <section
          className={`${styles.card} ${styles.warning}`}
          aria-label="Records to review"
        >
          <h2>Some records need review</h2>
          {review.notices.map((notice) => (
            <div key={notice.code}>
              <p>{notice.message}</p>
              {notice.review_href && (
                <ReviewLink href={notice.review_href} links={links}>
                  Open review
                </ReviewLink>
              )}
            </div>
          ))}
        </section>
      )}
      <div className={styles.grid}>
        <DayReviewSchedule
          section={review.schedule}
          title={
            review.kind === "weekly" ? "Saved for this week" : "Saved for today"
          }
          timezone={review.timezone}
          links={links}
        />
        <DayReviewSchedule
          section={review.outlook}
          title={
            review.kind === "weekly"
              ? "Saved for next week"
              : "Saved for tomorrow"
          }
          timezone={review.timezone}
          links={links}
        />
      </div>
      <DayReviewWork
        section={review.work}
        timezone={review.timezone}
        links={links}
      />
      <div className={styles.grid}>
        <DayReviewOutcomes
          section={review.outcomes}
          timezone={review.timezone}
          links={links}
        />
        <DayReviewGoals section={review.goals} links={links} />
      </div>
      {review.question &&
        dayReviewLinkAllowed(review.question.review_href, links) && (
          <section className={styles.card}>
            <p>{review.question.text}</p>
            <ReviewLink href={review.question.review_href} links={links}>
              Review when ready
            </ReviewLink>
          </section>
        )}
      <p className={styles.hint}>
        Scheduled sessions and self-reported progress describe saved records.
        Attendance, sleep, grades and assignment submission are unknown. This
        review does not change your plan.
      </p>
    </>
  );
}
export function DayReviewPrivateSurface({
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
export function StudentDayReview({ accountId }: { accountId: string }) {
  return <StudentDayReviewSession key={accountId} accountId={accountId} />;
}
function StudentDayReviewSession({ accountId }: { accountId: string }) {
  const [controller] = useState(
      () => new StudentDayReviewController(accountId),
    ),
    state = useSyncExternalStore(
      controller.subscribe,
      controller.getSnapshot,
      controller.getSnapshot,
    );
  useEffect(
    () =>
      observeDayReviewPrivacy(
        () => controller.clear(),
        controller.suspend,
        () => void controller.resume(),
        {
          window,
          document,
          channel:
            typeof BroadcastChannel === "undefined"
              ? null
              : new BroadcastChannel("navox-account-session"),
        },
      ),
    [controller],
  );
  const links = {
      planning: productFeatures.studentPlanning,
      goals: productFeatures.studentGoals,
      email: productFeatures.studentEmail,
    },
    disabled = state.busy || state.masked || state.blocked;
  return (
    <div className={styles.panel} aria-busy={state.busy}>
      <header className={`${styles.card} ${styles.hero}`}>
        <div>
          <p className={styles.eyebrow}>A moment to review your day</p>
          <h1>Your records, one clear view.</h1>
          <p>
            Choose a review. Open it when it helps, and decide your own next
            step.
          </p>
        </div>
        <button type="button" onClick={() => controller.clear()}>
          Clear review
        </button>
      </header>
      <div className={styles.controls}>
        <fieldset className={styles.modes} aria-label="Review type">
          {kinds.map(([kind, label]) => (
            <button
              type="button"
              key={kind}
              disabled={disabled}
              aria-pressed={state.kind === kind}
              onClick={() => controller.choose(kind)}
            >
              {label}
            </button>
          ))}
        </fieldset>
        <button
          type="button"
          className={styles.primary}
          disabled={disabled}
          onClick={() => void controller.open()}
        >
          {state.busy
            ? "Opening review…"
            : state.review
              ? "Refresh review"
              : "Open review"}
        </button>
      </div>
      {state.masked && (
        <section className={styles.card} role="status">
          <p>Your review is hidden while access is checked.</p>
          <button
            type="button"
            disabled={state.busy || state.blocked}
            onClick={() => void controller.resume()}
          >
            {state.busy ? "Checking access…" : "Return to reviews"}
          </button>
        </section>
      )}
      <DayReviewPrivateSurface masked={state.masked}>
        <p className={styles.notice} role="status">
          {state.busy
            ? "Opening a fresh view of your saved records…"
            : state.notice}
        </p>
        {state.error && (
          <p className={styles.warning} role="alert">
            {state.error}
          </p>
        )}
        {state.review ? (
          <DayReviewContent review={state.review} links={links} />
        ) : (
          <section className={styles.card}>
            <h2>Start with what you’ve saved</h2>
            <p>
              Scheduled classes, saved work, recorded study outcomes and your
              own goal reports stay clearly labeled.
            </p>
            <p className={styles.hint}>
              Choose Open review to load your records. After leaving this view,
              open a fresh review to see current records.
            </p>
          </section>
        )}
      </DayReviewPrivateSurface>
    </div>
  );
}
