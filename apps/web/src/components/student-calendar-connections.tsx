"use client";

import { useId, useMemo, useState } from "react";
import {
  type CalendarView,
  createStudentCalendarApi,
  type StudentCalendarApi,
} from "../lib/student-calendar-api";
import { notifyCalendarAccessChanged } from "../lib/student-calendar-privacy";
import { AppleCalendarConnection } from "./apple-calendar-connection";
import styles from "./student-calendar.module.css";
import { StudentCalendarSchedulingConsent } from "./student-calendar-scheduling";
import { useCalendarWorkspace } from "./use-calendar-workspace";

export const calendarConsent =
  "Use my Google calendar list and busy times to find openings. Event details, Gmail and calendar changes are not requested. I can stop this use at any time.";

interface CalendarConnectionsProps {
  accountId: string;
  api?: StudentCalendarApi;
}

export function StudentCalendarConnections(props: CalendarConnectionsProps) {
  return (
    <>
      <CalendarConnectionsWorkspace key={props.accountId} {...props} />
      <AppleCalendarConnection
        key={`apple:${props.accountId}`}
        accountId={props.accountId}
      />
      <StudentCalendarSchedulingConsent
        key={`scheduling:${props.accountId}`}
        accountId={props.accountId}
      />
    </>
  );
}

function CalendarConnectionsWorkspace({
  accountId,
  api: suppliedApi,
}: CalendarConnectionsProps) {
  const id = useId();
  const api = useMemo(
    () => suppliedApi ?? createStudentCalendarApi(accountId),
    [accountId, suppliedApi],
  );
  const [view, setView] = useState<CalendarView | null>(null);
  const [consent, setConsent] = useState(false);
  const [outlookConsent, setOutlookConsent] = useState(false);
  const [notice, setNotice] = useState("");
  const privateState = useCalendarWorkspace(accountId, api, () => {
    setView(null);
    setConsent(false);
    setOutlookConsent(false);
    setNotice("");
  });
  const google = view?.providers.find((provider) => provider.id === "google");
  const outlook = view?.providers.find((provider) => provider.id === "outlook");
  const refresh = () => {
    setView(null);
    setConsent(false);
    setOutlookConsent(false);
    setNotice("");
    void privateState.run(
      (signal) => api.calendars(signal),
      (current) => {
        setView(current);
        setNotice("Calendar access checked. No calendar events were read.");
      },
    );
  };
  const connect = () => {
    if (!view || !consent || google?.availability !== "available") return;
    const captured = view;
    setNotice("");
    void privateState.run(
      (signal) =>
        api.start(
          {
            generation: captured.generation,
            revision: captured.revision,
            request_id: crypto.randomUUID(),
            consent_version: "calendar-availability.v1",
            confirmed: true,
            connection_id: null,
          },
          signal,
        ),
      (authorization) =>
        window.location.assign(authorization.authorization_url),
    );
  };
  const disconnect = (grantId: string) => {
    if (!view) return;
    const captured = view;
    setConsent(false);
    setNotice("");
    void privateState.run(
      (signal) =>
        api.disconnect(
          grantId,
          {
            generation: captured.generation,
            revision: captured.revision,
            request_id: crypto.randomUUID(),
          },
          signal,
        ),
      (current) => {
        setView(current);
        notifyCalendarAccessChanged();
        setNotice(
          "Calendar availability access stopped. NavoX will not read this connection’s busy times.",
        );
      },
    );
  };
  const connectOutlook = () => {
    if (
      !view ||
      !outlookConsent ||
      outlook?.availability !== "available" ||
      !api.startOutlook
    )
      return;
    const captured = view;
    const startOutlook = api.startOutlook;
    setNotice("");
    void privateState.run(
      (signal) =>
        startOutlook(
          {
            generation: captured.generation,
            revision: captured.revision,
            request_id: crypto.randomUUID(),
            consent_version: "calendar-availability.v1",
            confirmed: true,
            connection_id: null,
          },
          signal,
        ),
      (authorization) =>
        window.location.assign(authorization.authorization_url),
    );
  };
  return (
    <section className={styles.panel} aria-labelledby={`${id}-heading`}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Calendar availability</p>
          <h2 id={`${id}-heading`}>Find time without sharing event details</h2>
        </div>
        <button
          type="button"
          className={styles.secondary}
          disabled={!privateState.ready || privateState.busy}
          onClick={refresh}
        >
          {privateState.busy ? "Checking…" : "Check calendar access"}
        </button>
      </div>
      <p>
        Connect calendars for private busy-time checks in Planner. This
        permission is separate from coursework and Gmail access.
      </p>
      <ul className={styles.providers} aria-label="Calendar providers">
        <li className={styles.provider}>
          <strong>Google Calendar</strong>
          <span className={styles.status}>
            {!view
              ? "Access not checked"
              : google?.availability === "available"
                ? "Available to connect"
                : "Setup unavailable here"}
          </span>
        </li>
        <li className={styles.provider}>
          <strong>Outlook / Microsoft 365</strong>
          <span className={styles.status}>
            {!view
              ? "Access not checked"
              : outlook?.availability === "available"
                ? "Available to connect"
                : "Setup unavailable here"}
          </span>
        </li>
        <li className={styles.provider}>
          <strong>Apple / iCloud</strong>
          <span className={styles.status}>
            {!view
              ? "Access not checked"
              : view.providers.find((provider) => provider.id === "icloud")
                    ?.availability === "available"
                ? "Available to connect"
                : "Setup unavailable here"}
          </span>
        </li>
      </ul>
      {view?.grants
        .filter((grant) => grant.provider !== "icloud")
        .map((grant) => (
          <article className={styles.provider} key={grant.id}>
            <div>
              <strong>
                {grant.account_label ??
                  `${grant.provider === "outlook" ? "Microsoft" : "Google"} calendar account`}
              </strong>
              <p className={styles.hint}>
                {grant.status === "connected"
                  ? "Busy-time access is on"
                  : grant.status === "pending"
                    ? "Calendar setup has not completed"
                    : grant.status === "revoked"
                      ? "Availability access is off"
                      : "Calendar access is unavailable"}
              </p>
            </div>
            {grant.status !== "revoked" && (
              <button
                type="button"
                className={styles.secondary}
                disabled={!privateState.ready || privateState.busy}
                onClick={() => disconnect(grant.id)}
              >
                Stop availability access
              </button>
            )}
          </article>
        ))}
      {google?.availability === "available" && (
        <>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={consent}
              disabled={!privateState.ready || privateState.busy}
              onChange={(event) => setConsent(event.target.checked)}
            />
            <span>{calendarConsent}</span>
          </label>
          <button
            type="button"
            disabled={!privateState.ready || privateState.busy || !consent}
            onClick={connect}
          >
            Connect Google Calendar
          </button>
        </>
      )}
      {outlook?.availability === "available" && api.startOutlook && (
        <>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={outlookConsent}
              disabled={!privateState.ready || privateState.busy}
              onChange={(event) => setOutlookConsent(event.target.checked)}
            />
            <span>
              Use busy times from all my Outlook calendars to find openings.
              Microsoft calendar read permission is separate from email. No
              events or invitations are created. I can stop this use anytime.
            </span>
          </label>
          <button
            type="button"
            disabled={
              !privateState.ready || privateState.busy || !outlookConsent
            }
            onClick={connectOutlook}
          >
            Connect Outlook Calendar
          </button>
        </>
      )}
      {!privateState.ready && (
        <p className={styles.hint} role="status">
          Checking your current account before showing calendar access…
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {privateState.error && (
        <p className={styles.error} role="alert">
          {privateState.error}
        </p>
      )}
      <p className={styles.hint}>
        No calendar is changed. Selecting a time in Planner only shows an
        option; it does not create an event, invitation or email.
      </p>
    </section>
  );
}
