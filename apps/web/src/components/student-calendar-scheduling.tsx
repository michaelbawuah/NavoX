"use client";

import Link from "next/link";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { productFeatures } from "../lib/product-features";
import type { AvailabilityOption } from "../lib/student-availability-options";
import type {
  AvailabilityPreviewInput,
  CalendarView,
} from "../lib/student-calendar-api";
import {
  notifyCalendarAccessChanged,
  observeCalendarAccessChanges,
} from "../lib/student-calendar-privacy";
import {
  CALENDAR_SCHEDULING_CONSENT_VERSION,
  type CalendarEventView,
  type CalendarSchedulingTargets,
  type CalendarSchedulingView,
  createStudentCalendarSchedulingApi,
  type StudentCalendarSchedulingApi,
} from "../lib/student-calendar-scheduling";
import styles from "./student-calendar-scheduling.module.css";
import { useCalendarWorkspace } from "./use-calendar-workspace";

export const calendarSchedulingConsent =
  "Let NavoX add and verify events I review in a Google calendar I own. Each event needs my approval. Undo may remove only the unchanged event NavoX created. I can stop scheduling access separately from busy-time checks.";
const MAX_LOADED_ACTIONS = 551;
const command = (view: { generation: number; revision: number }) => ({
  generation: view.generation,
  revision: view.revision,
  request_id: crypto.randomUUID(),
});
function eventTime(
  event: Pick<CalendarEventView, "start_at" | "end_at" | "timezone">,
) {
  const format = new Intl.DateTimeFormat("en-US", {
    timeZone: event.timezone,
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
  return `${format.format(new Date(event.start_at))} – ${format.format(new Date(event.end_at))}`;
}
function studyMoveCount(option: AvailabilityOption) {
  return "displaced_study_block_ids" in option &&
    Array.isArray(option.displaced_study_block_ids)
    ? option.displaced_study_block_ids.length
    : 0;
}
interface ConsentProps {
  accountId: string;
  api?: StudentCalendarSchedulingApi;
}
export function StudentCalendarSchedulingConsent({
  accountId,
  api: supplied,
}: ConsentProps) {
  const id = useId();
  const writesAvailable =
    productFeatures.studentCalendarAvailability &&
    productFeatures.studentCalendarWrites;
  const api = useMemo(
    () => supplied ?? createStudentCalendarSchedulingApi(accountId),
    [accountId, supplied],
  );
  const [view, setView] = useState<CalendarSchedulingView | null>(null);
  const [connections, setConnections] = useState<CalendarView | null>(null);
  const [selected, setSelected] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [notice, setNotice] = useState("");
  const clear = useCallback(() => {
    setView(null);
    setConnections(null);
    setSelected("");
    setConfirmed(false);
    setNotice("");
  }, []);
  const access = useCalendarWorkspace(accountId, api, clear);
  // The account observer clears hidden pages; blur also clears visible windows.
  useEffect(() => {
    const blur = () => {
      access.cancel();
      clear();
    };
    window.addEventListener("blur", blur);
    return () => window.removeEventListener("blur", blur);
  }, [access.cancel, clear]);
  const check = () => {
    clear();
    void access.run(
      async (signal) => {
        const [scheduling, calendars] = await Promise.all([
          api.scheduling(signal),
          api.calendars(signal),
        ]);
        if (
          scheduling.generation !== calendars.generation ||
          scheduling.revision !== calendars.revision
        )
          throw new Error("Calendar access changed. Check it again.");
        return { scheduling, calendars };
      },
      ({ scheduling, calendars }) => {
        setView(scheduling);
        setConnections(calendars);
      },
    );
  };
  const connect = () => {
    if (!writesAvailable || !view || !confirmed || !selected) return;
    void access.run(
      (signal) =>
        api.startScheduling(
          {
            ...command(view),
            consent_version: CALENDAR_SCHEDULING_CONSENT_VERSION,
            connection_id: selected,
            confirmed: true,
          },
          signal,
        ),
      (authorization) =>
        window.location.assign(authorization.authorization_url),
    );
  };
  const stop = (grantId: string) => {
    if (!view) return;
    setConfirmed(false);
    void access.run(
      (signal) => api.stopScheduling(grantId, command(view), signal),
      (next) => {
        setView(next);
        notifyCalendarAccessChanged();
        setNotice(
          "Scheduling access stopped. Existing calendar events were not removed.",
        );
      },
    );
  };
  const active =
    connections?.grants.filter(
      (grant) =>
        grant.provider === "google" &&
        grant.status === "connected" &&
        grant.connection_id !== null,
    ) ?? [];
  return (
    <section className={styles.panel} aria-labelledby={`${id}-heading`}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Your calendar, your approval</p>
          <h2 id={`${id}-heading`}>Add an event after reviewing it</h2>
        </div>
        <button
          type="button"
          disabled={!access.ready || access.busy}
          onClick={check}
        >
          Check scheduling access
        </button>
      </div>
      <p>
        Scheduling is optional and separate from finding free time. NavoX adds
        only the event you approve to a calendar you choose.
      </p>
      {view?.grants.map((grant) => (
        <article className={styles.card} key={grant.id}>
          <strong>{grant.account_label ?? "Google calendar account"}</strong>
          <p>
            {grant.status === "connected"
              ? "Reviewed scheduling is connected"
              : grant.status === "pending"
                ? "Google setup has not completed"
                : grant.status === "revoked"
                  ? "Scheduling access is off"
                  : "Scheduling access is unavailable"}
          </p>
          {grant.status !== "revoked" && (
            <button
              type="button"
              disabled={!access.ready || access.busy}
              onClick={() => stop(grant.id)}
            >
              Stop scheduling access
            </button>
          )}
        </article>
      ))}
      {writesAvailable && view && active.length > 0 && (
        <div className={styles.fields}>
          <label htmlFor={`${id}-account`}>
            Google account
            <select
              id={`${id}-account`}
              value={selected}
              disabled={!access.ready || access.busy}
              onChange={(e) => {
                setSelected(e.target.value);
                setConfirmed(false);
              }}
            >
              <option value="">Choose an account</option>
              {active.map((grant) => (
                <option key={grant.id} value={grant.connection_id ?? ""}>
                  {grant.account_label ?? "Connected Google account"}
                </option>
              ))}
            </select>
          </label>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={confirmed}
              disabled={!access.ready || access.busy || !selected}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            <span>{calendarSchedulingConsent}</span>
          </label>
          <button
            type="button"
            disabled={!access.ready || access.busy || !selected || !confirmed}
            onClick={connect}
          >
            Connect reviewed scheduling
          </button>
        </div>
      )}
      {writesAvailable && view && active.length === 0 && (
        <p>
          Connect Google busy-time access above first, then check scheduling
          access again.
        </p>
      )}
      {!writesAvailable && (
        <p className={styles.hint}>
          New calendar scheduling is unavailable here. You can still review and
          stop existing scheduling access.
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {access.error && (
        <p className={styles.error} role="alert">
          {access.error}
        </p>
      )}
      <p className={styles.hint}>
        Calendar invitations and emails are not sent by this flow.
      </p>
    </section>
  );
}

interface SchedulingProps {
  accountId: string;
  options: AvailabilityOption[];
  availability: AvailabilityPreviewInput | null;
  api?: StudentCalendarSchedulingApi;
}
export function StudentCalendarScheduling({
  accountId,
  options,
  availability,
  api: supplied,
}: SchedulingProps) {
  const id = useId();
  const writesAvailable =
    productFeatures.studentCalendarAvailability &&
    productFeatures.studentCalendarWrites;
  const api = useMemo(
    () => supplied ?? createStudentCalendarSchedulingApi(accountId),
    [accountId, supplied],
  );
  const [view, setView] = useState<CalendarSchedulingView | null>(null);
  const [targets, setTargets] = useState<CalendarSchedulingTargets | null>(
    null,
  );
  const [grantId, setGrantId] = useState("");
  const [calendarId, setCalendarId] = useState("");
  const [choice, setChoice] = useState("");
  const [title, setTitle] = useState("");
  const [location, setLocation] = useState("");
  const [event, setEvent] = useState<CalendarEventView | null>(null);
  const [recent, setRecent] = useState<CalendarEventView[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [undoConfirmed, setUndoConfirmed] = useState(false);
  const [mustCheck, setMustCheck] = useState(false);
  const [expired, setExpired] = useState(false);
  const [notice, setNotice] = useState("");
  const clear = useCallback(() => {
    setView(null);
    setTargets(null);
    setGrantId("");
    setCalendarId("");
    setChoice("");
    setTitle("");
    setLocation("");
    setEvent(null);
    setRecent([]);
    setCursor(null);
    setConfirmed(false);
    setUndoConfirmed(false);
    setMustCheck(false);
    setNotice("");
  }, []);
  const access = useCalendarWorkspace(accountId, api, clear);
  useEffect(() => {
    const blur = () => {
      access.cancel();
      clear();
    };
    window.addEventListener("blur", blur);
    return () => window.removeEventListener("blur", blur);
  }, [access.cancel, clear]);
  useEffect(
    () =>
      observeCalendarAccessChanges(() => {
        access.cancel();
        setView(null);
        setTargets(null);
        setEvent((old) => (old?.status === "pending" ? null : old));
        setRecent([]);
        setConfirmed(false);
        setUndoConfirmed(false);
        setNotice(
          "Calendar access changed. Check scheduling access before continuing.",
        );
      }),
    [access.cancel],
  );
  useEffect(() => {
    if (event?.status !== "pending") {
      setExpired(false);
      return;
    }
    const remaining = Date.parse(event.expires_at) - Date.now();
    setExpired(remaining <= 0);
    if (remaining <= 0) {
      setConfirmed(false);
      return;
    }
    const timer = window.setTimeout(() => {
      setExpired(true);
      setConfirmed(false);
    }, remaining);
    return () => window.clearTimeout(timer);
  }, [event]);
  const searchKey = availability ? JSON.stringify(availability) : "";
  const lastSearch = useRef(searchKey);
  useEffect(() => {
    if (lastSearch.current === searchKey) return;
    lastSearch.current = searchKey;
    access.cancel();
    setChoice("");
    setConfirmed(false);
    setUndoConfirmed(false);
    setEvent((old) => (old?.status === "pending" && !mustCheck ? null : old));
  }, [searchKey, access.cancel, mustCheck]);
  const draftChanged = () => {
    access.cancel();
    setEvent(null);
    setConfirmed(false);
    setUndoConfirmed(false);
    setMustCheck(false);
    setNotice("");
    access.setError("");
  };
  const acceptEvent = (current: CalendarEventView) => {
    setEvent(current);
    setConfirmed(false);
    setUndoConfirmed(false);
    setMustCheck(
      ["executing", "uncertain", "undoing"].includes(current.status),
    );
    setRecent((rows) =>
      [current, ...rows.filter((row) => row.id !== current.id)].slice(
        0,
        MAX_LOADED_ACTIONS,
      ),
    );
    setView((old) =>
      old
        ? {
            ...old,
            generation: current.generation,
            revision: current.calendar_revision,
          }
        : old,
    );
  };
  const check = () => {
    setConfirmed(false);
    setUndoConfirmed(false);
    void access.run(
      async (signal) => {
        const [scheduling, events] = await Promise.all([
          api.scheduling(signal),
          api.events(signal),
        ]);
        if (
          scheduling.generation !== events.generation ||
          scheduling.revision !== events.calendar_revision
        )
          throw new Error("Calendar access changed. Check it again.");
        return { scheduling, events };
      },
      ({ scheduling, events }) => {
        setView(scheduling);
        setRecent(events.events);
        setCursor(events.next_cursor);
        const recovered = events.events.find((row) => row.id === event?.id);
        if (recovered) acceptEvent(recovered);
        setTargets(null);
        setGrantId("");
        setCalendarId("");
        setNotice("Scheduling access and recent calendar actions checked.");
      },
    );
  };
  const more = () => {
    if (!cursor || !view) return;
    void access.run(
      (signal) => api.events(signal, cursor),
      (next) => {
        if (
          next.generation !== view.generation ||
          next.calendar_revision < view.revision
        )
          throw new Error("Calendar access changed. Check scheduling again.");
        setRecent((rows) =>
          [
            ...new Map(
              [...rows, ...next.events].map((row) => [row.id, row]),
            ).values(),
          ].slice(0, MAX_LOADED_ACTIONS),
        );
        setCursor(next.next_cursor);
        setView((old) =>
          old ? { ...old, revision: next.calendar_revision } : old,
        );
        const recovered = next.events.find((row) => row.id === event?.id);
        if (recovered) acceptEvent(recovered);
      },
    );
  };
  const loadTargets = (selected: string) => {
    if (!writesAvailable || !view) return;
    draftChanged();
    setTargets(null);
    setCalendarId("");
    setGrantId(selected);
    if (!selected) return;
    void access.run(
      (signal) => api.targets(selected, command(view), signal),
      (current) => {
        setTargets(current);
        setView((old) =>
          old
            ? {
                ...old,
                generation: current.generation,
                revision: current.revision,
              }
            : old,
        );
      },
    );
  };
  const preview = (submit: FormEvent<HTMLFormElement>) => {
    submit.preventDefault();
    const selected = options.find((option) => option.start_at === choice);
    if (
      !writesAvailable ||
      !view ||
      !targets ||
      !selected ||
      !calendarId ||
      !title.trim() ||
      !availability
    )
      return;
    draftChanged();
    void access.run(
      (signal) =>
        api.previewEvent(
          {
            ...command(view),
            grant_id: grantId,
            calendar_id: calendarId,
            title: title.trim(),
            location: location.trim(),
            start_at: selected.start_at,
            end_at: selected.end_at,
            timezone: availability.expected_timezone,
            availability,
            ...(studyMoveCount(selected) > 0
              ? { allow_study_displacement: true }
              : {}),
          },
          signal,
        ),
      acceptEvent,
    );
  };
  const eventCommand = (current: CalendarEventView) => ({
    ...command({
      generation: current.generation,
      revision: current.calendar_revision,
    }),
    event_revision: current.revision,
    digest: current.digest,
  });
  const approve = () => {
    if (
      !writesAvailable ||
      event?.status !== "pending" ||
      !confirmed ||
      mustCheck ||
      expired ||
      Date.parse(event.expires_at) <= Date.now()
    )
      return;
    const captured = event;
    setConfirmed(false);
    setMustCheck(true);
    setNotice(
      "Checking the time again before adding this event. If this is interrupted, check its status before trying anything else.",
    );
    void access.run(
      (signal) =>
        api.approveEvent(
          captured.id,
          { ...eventCommand(captured), confirmed: true },
          signal,
        ),
      acceptEvent,
    );
  };
  const reconcile = () => {
    if (!event) return;
    const captured = event;
    setConfirmed(false);
    setUndoConfirmed(false);
    void access.run(
      (signal) =>
        api.reconcileEvent(captured.id, eventCommand(captured), signal),
      acceptEvent,
    );
  };
  const undo = () => {
    if (!writesAvailable || !event?.can_undo || !undoConfirmed || mustCheck)
      return;
    const captured = event;
    setUndoConfirmed(false);
    setMustCheck(true);
    setNotice(
      "Checking this exact event before removing it. Check its status if this is interrupted.",
    );
    void access.run(
      (signal) =>
        api.undoEvent(
          captured.id,
          { ...eventCommand(captured), confirmed: true },
          signal,
        ),
      acceptEvent,
    );
  };
  const locked = access.busy || !access.ready;
  const unsettled =
    mustCheck ||
    (!!event && ["executing", "uncertain", "undoing"].includes(event.status));
  const unresolved = recent.some((row) =>
    ["executing", "uncertain", "undoing"].includes(row.status),
  );
  const editable = (!event || event.status === "pending") && !unresolved;
  const connected =
    view?.grants.filter((grant) => grant.status === "connected") ?? [];
  return (
    <section
      id="calendar"
      className={styles.panel}
      aria-labelledby={`${id}-heading`}
    >
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Make a time official</p>
          <h3 id={`${id}-heading`}>Review a calendar event</h3>
        </div>
        <button type="button" disabled={locked} onClick={check}>
          Check scheduling and recent actions
        </button>
      </div>
      <p>
        Choose a checked opening, review the exact event, then decide whether to
        add it.
      </p>
      {!availability && (
        <p className={styles.hint}>
          <Link href="/planner">Find confirmed openings in Planner</Link> before
          reviewing a new event. Recent actions can still be checked here.
        </p>
      )}
      {view && connected.length === 0 && (
        <p>
          <Link href="/connections">Connect reviewed scheduling</Link> to add
          events after your approval.
        </p>
      )}
      {recent.length > 0 && (
        <details>
          <summary>Recent calendar actions</summary>
          <ul className={styles.list}>
            {recent.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  disabled={locked || mustCheck}
                  onClick={() => {
                    acceptEvent(row);
                    setNotice("");
                  }}
                >
                  {row.title} ·{" "}
                  {row.status === "created"
                    ? "Added"
                    : row.status === "undone"
                      ? "Removed"
                      : row.status === "pending"
                        ? "Awaiting review"
                        : "Needs a status check"}
                </button>
              </li>
            ))}
          </ul>
          {cursor && (
            <button type="button" disabled={locked} onClick={more}>
              More saved actions
            </button>
          )}
        </details>
      )}
      {writesAvailable &&
        view &&
        connected.length > 0 &&
        availability &&
        options.length > 0 &&
        editable &&
        !unsettled && (
          <form onSubmit={preview}>
            <fieldset className={styles.fields} disabled={locked}>
              <label htmlFor={`${id}-grant`}>
                Google account
                <select
                  id={`${id}-grant`}
                  value={grantId}
                  required
                  onChange={(e) => loadTargets(e.target.value)}
                >
                  <option value="">Choose an account</option>
                  {connected.map((grant) => (
                    <option key={grant.id} value={grant.id}>
                      {grant.account_label ?? "Google calendar account"}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor={`${id}-calendar`}>
                Add to calendar
                <select
                  id={`${id}-calendar`}
                  value={calendarId}
                  required
                  disabled={!targets}
                  onChange={(e) => {
                    draftChanged();
                    setCalendarId(e.target.value);
                  }}
                >
                  <option value="">Choose a calendar</option>
                  {targets?.targets.map((target) => (
                    <option key={target.id} value={target.id}>
                      {target.label}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor={`${id}-time`}>
                Checked time
                <select
                  id={`${id}-time`}
                  value={choice}
                  required
                  onChange={(e) => {
                    draftChanged();
                    setChoice(e.target.value);
                  }}
                >
                  <option value="">Choose a time</option>
                  {options.map((option) => (
                    <option key={option.start_at} value={option.start_at}>
                      {eventTime({
                        ...option,
                        timezone: availability.expected_timezone,
                      })}
                      {studyMoveCount(option) > 0 &&
                        ` · review moving ${studyMoveCount(option)} study ${studyMoveCount(option) === 1 ? "block" : "blocks"}`}
                    </option>
                  ))}
                </select>
              </label>
              <label htmlFor={`${id}-title`}>
                Event title
                <input
                  id={`${id}-title`}
                  value={title}
                  required
                  maxLength={200}
                  onChange={(e) => {
                    draftChanged();
                    setTitle(e.target.value);
                  }}
                />
              </label>
              <label htmlFor={`${id}-location`}>
                Location (optional)
                <input
                  id={`${id}-location`}
                  value={location}
                  maxLength={500}
                  onChange={(e) => {
                    draftChanged();
                    setLocation(e.target.value);
                  }}
                />
              </label>
            </fieldset>
            <button
              type="submit"
              disabled={
                locked || !targets || !calendarId || !choice || !title.trim()
              }
            >
              Review exact event
            </button>
          </form>
        )}
      {event && (
        <article className={styles.card} aria-label="Exact calendar event">
          <h4>{event.title}</h4>
          <p>{eventTime(event)}</p>
          <dl>
            <div>
              <dt>Calendar</dt>
              <dd>{event.calendar_label}</dd>
            </div>
            <div>
              <dt>Timezone</dt>
              <dd>{event.timezone}</dd>
            </div>
            {event.location && (
              <div>
                <dt>Location</dt>
                <dd>{event.location}</dd>
              </div>
            )}
          </dl>
          {event.study_moves && (
            <section aria-label="Exact study plan changes">
              <h4>Study plan changes</h4>
              <p>
                {event.study_changes_applied
                  ? "This receipt records applied study moves."
                  : "These study moves are proposed, not applied."}
              </p>
              {event.study_details_redacted && (
                <p className={styles.warning}>
                  Some study details were forgotten and are no longer available
                  in this receipt. Check current blocks in{" "}
                  <Link href="/planner">Planner</Link>.
                </p>
              )}
              <ol className={styles.list}>
                {event.study_moves.map((move) => (
                  <li key={move.block_id}>
                    <strong>{move.title}</strong> · {move.minutes} minutes
                    <p>
                      Before:{" "}
                      {eventTime({
                        start_at: move.before_start_at,
                        end_at: move.before_end_at,
                        timezone: event.timezone,
                      })}
                    </p>
                    <p>
                      After:{" "}
                      {eventTime({
                        start_at: move.after_start_at,
                        end_at: move.after_end_at,
                        timezone: event.timezone,
                      })}
                    </p>
                  </li>
                ))}
              </ol>
              <p className={styles.warning}>
                Study moves may remain if the calendar request fails. Calendar
                Undo only removes the event.
              </p>
            </section>
          )}
          {(event.message.startsWith("This request predates your reset;") ||
            (event.status === "created" && !event.can_undo)) && (
            <p className={styles.hint}>{event.message}</p>
          )}
          <p role="status">
            {unsettled
              ? "The outcome needs checking. NavoX will not repeat this calendar change."
              : event.status === "created"
                ? "Added and verified on your calendar."
                : event.status === "undone"
                  ? "Removal verified. This event is no longer on your calendar."
                  : event.status === "pending" && !unsettled
                    ? "Ready for your review. No event has been added."
                    : event.status === "changed"
                      ? "This event changed outside NavoX. It will not be removed automatically."
                      : event.status === "failed"
                        ? "The event was not added. Review recent actions before starting again."
                        : "The outcome needs checking. NavoX will not try to add another event."}
          </p>
          {writesAvailable && event.status === "pending" && !unsettled && (
            <>
              <label className={styles.check}>
                <input
                  type="checkbox"
                  checked={confirmed}
                  disabled={
                    locked ||
                    expired ||
                    Date.parse(event.expires_at) <= Date.now()
                  }
                  onChange={(e) => setConfirmed(e.target.checked)}
                />
                <span>
                  I reviewed this title, time, timezone, location and calendar.
                  {event.study_moves &&
                    " I also reviewed every exact before-and-after study move and understand those changes may remain if the event fails."}
                  Add this exact event.
                </span>
              </label>
              <button
                type="button"
                disabled={
                  locked ||
                  !confirmed ||
                  expired ||
                  Date.parse(event.expires_at) <= Date.now()
                }
                onClick={approve}
              >
                Approve and add event
              </button>
              {(expired || Date.parse(event.expires_at) <= Date.now()) && (
                <p>
                  This review expired. Review the event again using fresh
                  openings.
                </p>
              )}
            </>
          )}
          {(unsettled ||
            event.status === "created" ||
            event.status === "changed") && (
            <button type="button" disabled={locked} onClick={reconcile}>
              Check event status
            </button>
          )}
          {writesAvailable && event.can_undo && !unsettled && (
            <>
              <label className={styles.check}>
                <input
                  type="checkbox"
                  checked={undoConfirmed}
                  disabled={locked}
                  onChange={(e) => setUndoConfirmed(e.target.checked)}
                />
                <span>Remove only this event from this calendar.</span>
              </label>
              <button
                type="button"
                disabled={locked || !undoConfirmed}
                onClick={undo}
              >
                Undo this event
              </button>
            </>
          )}
          {["created", "undone", "failed", "changed"].includes(event.status) &&
            !unsettled && (
              <button
                type="button"
                disabled={locked}
                onClick={() => {
                  draftChanged();
                  setTitle("");
                  setLocation("");
                }}
              >
                Review another event
              </button>
            )}
        </article>
      )}
      {(unsettled || unresolved) && (
        <p className={styles.warning}>
          Check the saved action’s status before starting another event.
          Rechecking does not repeat the calendar change.
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {access.error && (
        <p className={styles.error} role="alert">
          {access.error}
        </p>
      )}
      {!writesAvailable && (
        <p className={styles.hint}>
          New calendar changes are unavailable here. You can still check the
          status of saved actions.
        </p>
      )}
      <p className={styles.hint}>
        Approval adds one event without attendees. Undo removes only the
        unchanged event NavoX created.
      </p>
    </section>
  );
}
