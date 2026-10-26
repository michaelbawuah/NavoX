"use client";

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  APPLE_CALENDAR_CONSENT,
  type AppleCalendarApi,
  type AppleCalendarView,
  createAppleCalendarApi,
} from "../lib/apple-calendar-api";
import {
  CalendarRequestError,
  createStudentCalendarApi,
} from "../lib/student-calendar-api";
import {
  notifyCalendarAccessChanged,
  observeCalendarAccessChanges,
} from "../lib/student-calendar-privacy";
import styles from "./student-calendar.module.css";
import { useCalendarWorkspace } from "./use-calendar-workspace";

export const appleBusyConsent =
  "Use my iCloud calendars’ busy times to find openings. This does not approve calendar changes. I can disconnect at any time.";

interface Props {
  accountId: string;
  api?: AppleCalendarApi;
}

/** The key discards all drafts immediately when the selected account changes. */
export function AppleCalendarConnection(props: Props) {
  return <AppleCalendarConnectionWorkspace key={props.accountId} {...props} />;
}

function AppleCalendarConnectionWorkspace({
  accountId,
  api: suppliedApi,
}: Props) {
  const id = useId();
  const api = useMemo(
    () => suppliedApi ?? createAppleCalendarApi(accountId),
    [accountId, suppliedApi],
  );
  const sessionApi = useMemo(
    () => ({ ...createStudentCalendarApi(accountId), account: api.account }),
    [accountId, api],
  );
  const [view, setView] = useState<AppleCalendarView | null>(null);
  const [appleId, setAppleId] = useState("");
  const [password, setPassword] = useState("");
  const [consent, setConsent] = useState(false);
  const [notice, setNotice] = useState("");
  const notifying = useRef(false);
  const clear = useCallback(() => {
    setView(null);
    setAppleId("");
    setPassword("");
    setConsent(false);
    setNotice("");
  }, []);
  const privateState = useCalendarWorkspace(accountId, sessionApi, clear);
  const { ready, run, cancel, setError } = privateState;

  const refresh = useCallback(() => {
    void run(
      (signal) => api.view(signal),
      (current) => setView(current),
    );
  }, [run, api]);

  useEffect(
    () =>
      observeCalendarAccessChanges(() => {
        if (notifying.current) return;
        cancel();
        clear();
        refresh();
      }),
    [cancel, clear, refresh],
  );

  useEffect(() => {
    if (!ready) return;
    refresh();
  }, [ready, refresh]);

  const publish = () => {
    notifying.current = true;
    try {
      notifyCalendarAccessChanged();
    } finally {
      notifying.current = false;
    }
  };
  const connect = () => {
    const captured = view;
    const capturedPassword = password;
    setPassword("");
    setConsent(false);
    setNotice("");
    if (
      !captured ||
      !consent ||
      !captured.configured ||
      !privateState.ready ||
      privateState.busy
    )
      return;
    if (!/^[a-z]{4}(?:-[a-z]{4}){3}$/.test(capturedPassword)) {
      setError(
        "Use the app-specific password generated in your Apple Account settings.",
      );
      return;
    }
    void run(
      async (signal) => {
        try {
          return await api.connect(
            {
              generation: captured.generation,
              revision: captured.revision,
              request_id: crypto.randomUUID(),
              consent_version: APPLE_CALENDAR_CONSENT,
              confirmed: true,
              apple_id: appleId.trim(),
              app_password: capturedPassword,
            },
            signal,
          );
        } catch (cause) {
          if (cause instanceof CalendarRequestError) throw cause;
          throw new CalendarRequestError("unavailable");
        }
      },
      (current) => {
        setView(current);
        setAppleId("");
        publish();
        setNotice(
          "Apple calendar access is connected. Busy times can now be checked when finding openings.",
        );
      },
    );
  };
  const disconnect = (grantId: string) => {
    if (!view) return;
    const captured = view;
    setPassword("");
    setConsent(false);
    setNotice("");
    void run(
      async (signal) => {
        try {
          return await api.disconnect(
            grantId,
            {
              generation: captured.generation,
              revision: captured.revision,
              request_id: crypto.randomUUID(),
            },
            signal,
          );
        } catch (cause) {
          if (cause instanceof CalendarRequestError) throw cause;
          throw new CalendarRequestError("unavailable");
        }
      },
      (current) => {
        setView(current);
        publish();
        setNotice(
          "Apple calendar access stopped and its saved app-specific credential was removed.",
        );
      },
    );
  };

  return (
    <section className={styles.panel} aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>Apple / iCloud Calendar</h2>
      <p>
        Find openings around your iCloud calendars using an app-specific
        password. Apple’s credential can allow broader access; NavoX uses it
        only for the busy-time checks you approve here.
      </p>
      <p className={styles.hint}>
        Create an app-specific password in your Apple Account security settings.
        Never enter your main Apple Account password.{" "}
        <a
          href="https://support.apple.com/en-us/102654"
          target="_blank"
          rel="noreferrer"
        >
          Apple’s setup instructions
        </a>
      </p>
      {view?.grants.map((grant) => (
        <article className={styles.provider} key={grant.id}>
          <div>
            <strong>{grant.account_label ?? "Apple calendar account"}</strong>
            <p className={styles.hint}>
              {grant.status === "connected"
                ? "Busy-time access is on"
                : "Reconnect to check busy times"}
            </p>
          </div>
          <button
            type="button"
            className={styles.secondary}
            disabled={!privateState.ready || privateState.busy}
            onClick={() => disconnect(grant.id)}
          >
            Disconnect Apple calendar
          </button>
        </article>
      ))}
      {view?.configured === false ? (
        <p className={styles.hint}>
          Apple calendar setup is unavailable here. You can still disconnect
          existing access.
        </p>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            connect();
          }}
        >
          <fieldset
            className={styles.fields}
            disabled={
              !privateState.ready || privateState.busy || !view?.configured
            }
          >
            <label htmlFor={`${id}-account`}>
              Apple Account email
              <input
                id={`${id}-account`}
                type="email"
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                maxLength={256}
                value={appleId}
                onChange={(event) => setAppleId(event.target.value)}
                required
              />
            </label>
            <label htmlFor={`${id}-password`}>
              App-specific password
              <input
                id={`${id}-password`}
                type="password"
                autoComplete="off"
                spellCheck={false}
                maxLength={19}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </label>
          </fieldset>
          <label className={styles.check} htmlFor={`${id}-consent`}>
            <input
              id={`${id}-consent`}
              type="checkbox"
              checked={consent}
              disabled={
                !privateState.ready || privateState.busy || !view?.configured
              }
              onChange={(event) => setConsent(event.target.checked)}
            />
            {appleBusyConsent}
          </label>
          <button
            type="submit"
            disabled={
              !privateState.ready ||
              privateState.busy ||
              !view?.configured ||
              !consent ||
              !appleId.trim() ||
              !password
            }
          >
            {privateState.busy
              ? "Checking Apple access…"
              : "Connect Apple calendar"}
          </button>
        </form>
      )}
      {privateState.error && (
        <p className={styles.error} role="alert">
          {privateState.error}
        </p>
      )}
      {!view && !privateState.busy && privateState.ready && (
        <button
          type="button"
          className={styles.secondary}
          onClick={() => {
            setPassword("");
            setConsent(false);
            refresh();
          }}
        >
          Retry Apple setup
        </button>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
