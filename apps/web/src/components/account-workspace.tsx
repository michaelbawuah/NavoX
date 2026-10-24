"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  type Account,
  authenticate,
  type GoogleConnection,
  loadGoogleConnections,
} from "../lib/account-access";
import {
  notifyAccountSessionChanged,
  notifyAccountSessionChanging,
  observeAccountSession,
} from "../lib/account-session";
import { productFeatures } from "../lib/product-features";
import { notifyCalendarAccessChanged } from "../lib/student-calendar-privacy";
import type { CourseMaterialReference } from "../lib/student-material-link";
import {
  notifyStudentTimezoneChanged,
  STUDENT_TIMEZONE_CHANGED,
} from "../lib/student-time";
import { NavoXCinematic } from "./navox-cinematic";
import { StudentAssistantPanel } from "./student-assistant-panel";
import { StudentAvailability } from "./student-availability";
import { StudentClassesWorkspace } from "./student-classes";
import { StudentCourseWorkspace } from "./student-course-workspace";
import { StudentDailyPlan } from "./student-daily-plan";
import { StudentDayReview } from "./student-day-review";
import { StudentEmailActions } from "./student-email-actions";
import { StudentGoals } from "./student-goals";
import { StudentRecovery } from "./student-recovery";
import { TodayWorkspace, type TodayWorkspaceView } from "./today-workspace";
import { WorkspaceShell } from "./workspace-shell";

const StudentWhiteboard = dynamic(
  () =>
    import("./student-whiteboard").then((module) => module.StudentWhiteboard),
  { loading: () => <p role="status">Opening the Board…</p> },
);

type AuthMode = "register" | "login";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const signInUnavailable =
  "Your sign-in could not be checked. Reload sign-in to try again.";

export { observeAccountSession } from "../lib/account-session";

export function AccountWorkspace({
  view = "today",
  courseId,
  materialReference,
}: {
  view?:
    | TodayWorkspaceView
    | "classes"
    | "course"
    | "student-assistant"
    | "recovery"
    | "student-email"
    | "goals"
    | "day-review"
    | "board";
  courseId?: string;
  materialReference?: CourseMaterialReference | null;
}) {
  const [mode, setMode] = useState<AuthMode>("register");
  const [account, setAccount] = useState<Account | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [connections, setConnections] = useState<GoogleConnection[]>([]);
  const [connectionsUnavailable, setConnectionsUnavailable] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [sessionRestored, setSessionRestored] = useState(false);
  const [sessionUnavailable, setSessionUnavailable] = useState(false);
  const sessionObserver = useRef<ReturnType<
    typeof observeAccountSession
  > | null>(null);
  const sessionGeneration = useRef(0);

  useEffect(() => {
    const connectionOutcome = new URLSearchParams(window.location.search).get(
      "google_connection",
    );
    if (connectionOutcome !== null) {
      if (
        connectionOutcome.startsWith("calendar_availability_") ||
        connectionOutcome.startsWith("calendar_scheduling_")
      )
        notifyCalendarAccessChanged();
      const outcomes: Record<string, string> = {
        cancelled: "Google connection was cancelled.",
        connected:
          "Google account linked. No Gmail, Calendar, or Drive data was requested.",
        reconnected:
          "Google authorization refreshed. Any paused connection remains paused.",
        intelligence_enabled:
          "Google read access approved. Start a sync when you are ready.",
        gmail_send_enabled:
          "Gmail sending permission enabled. Every send still requires exact approval.",
        calendar_availability_enabled:
          "Google calendar busy-time access enabled. Open Planner to check for openings.",
        calendar_availability_failed:
          "Google calendar access did not complete. Review Calendar availability in Connections.",
        calendar_scheduling_enabled:
          "Google calendar scheduling enabled. Every event still needs your review and approval.",
        calendar_scheduling_failed:
          "Google calendar scheduling did not complete. Review Calendar scheduling in Connections.",
        failed: "Google could not complete the connection. Please try again.",
        refresh_token_required:
          "Google did not return a refresh credential. Please try connecting again.",
        scope_mismatch:
          "Google returned an unrequested permission. NavoX did not link the account.",
        account_mismatch:
          "Permission was granted from a different Google account, so NavoX rejected it.",
        unverified: "Google did not confirm a verified email address.",
      };
      setMessage(
        outcomes[connectionOutcome] ?? "Google connection did not complete.",
      );
      window.history.replaceState({}, "", window.location.pathname);
    }

    const observer = observeAccountSession({
      checking: () => {
        ++sessionGeneration.current;
        setSessionRestored(false);
        setSessionUnavailable(false);
      },
      restored: (restored) => {
        setSessionUnavailable(false);
        setAccount(restored.account);
        setConnections(restored.connections);
        setConnectionsUnavailable(Boolean(restored.connectionsError));
        if (restored.connectionsError) setMessage(restored.connectionsError);
        else
          setMessage((current) =>
            current === signInUnavailable ? "" : current,
          );
        setIsLoading(false);
        setSessionRestored(true);
      },
      unavailable: () => {
        setSessionUnavailable(true);
        setMode("login");
        setAccount(null);
        setConnections([]);
        setConnectionsUnavailable(false);
        setMessage(signInUnavailable);
        setIsLoading(false);
        setSessionRestored(true);
      },
    });
    sessionObserver.current = observer;
    return () => {
      observer.dispose();
      sessionObserver.current = null;
    };
  }, []);

  useEffect(() => {
    const changed = (event: Event) => {
      const detail = (
        event as CustomEvent<{ accountKey: string; timezone: string }>
      ).detail;
      setAccount((current) =>
        current &&
        detail?.accountKey === `${current.workspace.id}:${current.id}`
          ? { ...current, timezone: detail.timezone }
          : current,
      );
    };
    window.addEventListener(STUDENT_TIMEZONE_CHANGED, changed);
    return () => window.removeEventListener(STUDENT_TIMEZONE_CHANGED, changed);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const change = notifyAccountSessionChanging();
    setMessage("");
    setIsLoading(true);

    const payload =
      mode === "register"
        ? { email, password, display_name: displayName }
        : { email, password };
    try {
      const authenticated = await authenticate(apiBaseUrl, mode, payload);
      sessionObserver.current?.invalidate();
      ++sessionGeneration.current;
      setAccount(authenticated);
      setConnections([]);
      setSessionRestored(false);
      // The matched completion below refreshes the current cookie account and
      // its connections through the shared observer, including this document.
      setPassword("");
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Something went wrong. Please try again.",
      );
    } finally {
      notifyAccountSessionChanged(change);
      setIsLoading(false);
    }
  }

  const refreshConnections = useCallback(async () => {
    const generation = sessionGeneration.current;
    try {
      const loadedConnections = await loadGoogleConnections(apiBaseUrl);
      if (generation !== sessionGeneration.current) return;
      setConnections(loadedConnections);
      setConnectionsUnavailable(false);
      setMessage("");
    } catch (error) {
      if (generation !== sessionGeneration.current) return;
      // Do not leave older approval/read controls advertising a stale active account.
      setConnections([]);
      setConnectionsUnavailable(true);
      setMessage(
        error instanceof Error
          ? error.message
          : "Connected apps could not be loaded. Please retry.",
      );
      throw error;
    }
  }, []);

  async function signOut() {
    const change = notifyAccountSessionChanging();
    setIsLoading(true);
    try {
      const response = await fetch(`${apiBaseUrl}/auth/logout`, {
        method: "POST",
        credentials: "include",
        signal: AbortSignal.timeout(10_000),
      });
      if (!response.ok)
        throw new Error("We couldn’t sign you out. Please try again.");
      sessionObserver.current?.invalidate();
      ++sessionGeneration.current;
      setAccount(null);
      setConnections([]);
      setConnectionsUnavailable(false);
      setMessage("");
      setSessionRestored(true);
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "We couldn’t sign you out. Please try again.",
      );
    } finally {
      notifyAccountSessionChanged(change);
      setIsLoading(false);
    }
  }

  if (!sessionRestored && account === null) {
    return (
      <main aria-live="polite" className="shell">
        Getting NavoX ready…
      </main>
    );
  }

  if (account !== null) {
    return (
      <>
        {!sessionRestored && (
          <main aria-live="polite" className="shell">
            Checking your sign-in…
          </main>
        )}
        <div hidden={!sessionRestored}>
          {view === "board" ? (
            <WorkspaceShell
              current="Board"
              accountKey={`${account.workspace.id}:${account.id}`}
              timezone={account.timezone}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              {productFeatures.studentWhiteboard && sessionRestored ? (
                <StudentWhiteboard
                  key={`${account.workspace.id}:${account.id}`}
                  storageEnabled
                  accountKey={`${account.workspace.id}:${account.id}`}
                />
              ) : productFeatures.studentWhiteboard ? null : (
                <p>Board lessons are not available on this deployment.</p>
              )}
            </WorkspaceShell>
          ) : view === "classes" ? (
            <WorkspaceShell
              current="Classes"
              accountKey={`${account.workspace.id}:${account.id}`}
              timezone={account.timezone}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              <StudentClassesWorkspace
                key={`${account.id}:${account.workspace.id}`}
                accountId={`${account.workspace.id}:${account.id}`}
                displayName={account.display_name}
                timezone={account.timezone}
                onTimezoneChange={(zone) =>
                  notifyStudentTimezoneChanged(
                    `${account.workspace.id}:${account.id}`,
                    zone,
                  )
                }
              />
            </WorkspaceShell>
          ) : view === "course" ? (
            <WorkspaceShell
              current="Classes"
              accountKey={`${account.workspace.id}:${account.id}`}
              timezone={account.timezone}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              {courseId ? (
                <StudentCourseWorkspace
                  key={`${account.workspace.id}:${account.id}:${courseId}`}
                  accountId={`${account.workspace.id}:${account.id}`}
                  courseId={courseId}
                  materialReference={materialReference}
                />
              ) : (
                <p>This course could not be opened.</p>
              )}
            </WorkspaceShell>
          ) : view === "day-review" ? (
            <WorkspaceShell
              current="Today"
              accountKey={`${account.workspace.id}:${account.id}`}
              timezone={account.timezone}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              {productFeatures.studentCoach ? (
                <StudentDayReview
                  key={`${account.workspace.id}:${account.id}`}
                  accountId={`${account.workspace.id}:${account.id}`}
                />
              ) : (
                <p>Day reviews are not available here yet.</p>
              )}
            </WorkspaceShell>
          ) : view === "goals" ? (
            <WorkspaceShell
              current="Planner"
              accountKey={`${account.workspace.id}:${account.id}`}
              timezone={account.timezone}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              {productFeatures.studentGoals ? (
                <StudentGoals
                  key={`${account.workspace.id}:${account.id}`}
                  accountId={`${account.workspace.id}:${account.id}`}
                />
              ) : (
                <p>Goals are not available on this deployment.</p>
              )}
            </WorkspaceShell>
          ) : view === "student-email" ? (
            <WorkspaceShell
              current="Inbox"
              accountKey={`${account.workspace.id}:${account.id}`}
              timezone={account.timezone}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              {productFeatures.studentEmail ? (
                <StudentEmailActions
                  key={`${account.workspace.id}:${account.id}`}
                  accountId={`${account.workspace.id}:${account.id}`}
                  planningEnabled={productFeatures.studentPlanning}
                />
              ) : (
                <p>Email actions are not available on this deployment.</p>
              )}
            </WorkspaceShell>
          ) : view === "recovery" ? (
            <WorkspaceShell
              current="Planner"
              accountKey={`${account.workspace.id}:${account.id}`}
              timezone={account.timezone}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              {productFeatures.studentRecovery ? (
                <StudentRecovery
                  key={`${account.workspace.id}:${account.id}`}
                  accountId={`${account.workspace.id}:${account.id}`}
                />
              ) : (
                <p>Catch-up previews are not available on this deployment.</p>
              )}
            </WorkspaceShell>
          ) : view === "student-assistant" ? (
            <WorkspaceShell
              current="Assistant"
              accountKey={`${account.workspace.id}:${account.id}`}
              timezone={account.timezone}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              <StudentAssistantPanel
                key={`${account.workspace.id}:${account.id}`}
                accountId={`${account.workspace.id}:${account.id}`}
                toolContextConsentAvailable={
                  productFeatures.studentAgentToolContext
                }
              />
              <p>
                <Link href="/navox/actions">
                  Email drafts and saved conversations
                </Link>
              </p>
            </WorkspaceShell>
          ) : view === "planner" &&
            (productFeatures.studentPlanning ||
              productFeatures.studentCalendarAvailability) ? (
            <WorkspaceShell
              current="Planner"
              accountKey={`${account.workspace.id}:${account.id}`}
              timezone={account.timezone}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              {productFeatures.studentPlanning && (
                <StudentDailyPlan
                  key={`${account.workspace.id}:${account.id}`}
                  accountId={`${account.workspace.id}:${account.id}`}
                />
              )}
              {productFeatures.studentCalendarAvailability && (
                <StudentAvailability
                  key={`availability:${account.workspace.id}:${account.id}`}
                  accountId={`${account.workspace.id}:${account.id}`}
                  timezone={account.timezone ?? "UTC"}
                  collapsible={productFeatures.studentPlanning}
                />
              )}
            </WorkspaceShell>
          ) : (
            <TodayWorkspace
              key={`${account.id}:${account.workspace.id}`}
              view={view}
              account={account}
              connections={connections}
              connectionsUnavailable={connectionsUnavailable}
              message={message}
              onConnectionsChanged={refreshConnections}
              onSignOut={signOut}
            />
          )}
        </div>
      </>
    );
  }

  return (
    <NavoXCinematic>
      <section
        aria-labelledby="auth-heading"
        className="auth-card access-card"
        id="access-panel"
      >
        <div className="access-card-topline">
          <span>WELCOME TO NAVOX</span>
          <span>✦</span>
        </div>
        <fieldset aria-label="Account access" className="auth-tabs">
          <button
            aria-pressed={mode === "register"}
            className={mode === "register" ? "active" : ""}
            onClick={() => {
              setMode("register");
              setMessage("");
            }}
            type="button"
          >
            Create account
          </button>
          <button
            aria-pressed={mode === "login"}
            className={mode === "login" ? "active" : ""}
            onClick={() => {
              setMode("login");
              setMessage("");
            }}
            type="button"
          >
            Sign in
          </button>
        </fieldset>
        <div className="access-card-heading">
          <p className="access-step">Your account</p>
          <h2 id="auth-heading" tabIndex={-1}>
            {mode === "register" ? "Make yourself at home" : "Welcome back"}
          </h2>
          <p>
            {mode === "register"
              ? "Your day, a little easier."
              : "Pick up where you left off."}
          </p>
        </div>
        <form className="auth-form" onSubmit={submit}>
          {mode === "register" && (
            <label>
              Your name
              <input
                autoComplete="name"
                maxLength={256}
                minLength={1}
                onChange={(event) => setDisplayName(event.target.value)}
                required
                value={displayName}
              />
            </label>
          )}
          <label>
            Email address
            <input
              autoComplete="email"
              onChange={(event) => setEmail(event.target.value)}
              required
              type="email"
              value={email}
            />
          </label>
          <label>
            Password
            <input
              autoComplete={
                mode === "register" ? "new-password" : "current-password"
              }
              minLength={mode === "register" ? 12 : 1}
              maxLength={128}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
            {mode === "register" && <small>Use 12 to 128 characters.</small>}
          </label>
          {message && (
            <p className="form-message" role="alert">
              {message}
            </p>
          )}
          {sessionUnavailable && (
            <button type="button" onClick={() => window.location.reload()}>
              Reload sign-in
            </button>
          )}
          <button
            className="primary-button"
            disabled={isLoading || sessionUnavailable}
            type="submit"
          >
            {isLoading
              ? "Working…"
              : mode === "register"
                ? "Create account"
                : "Sign in"}
          </button>
        </form>
        <p className="access-footnote">
          <span aria-hidden="true" className="access-footnote-symbol">
            ⌁
          </span>
          Your account is private. Connect your apps when you’re ready.
        </p>
      </section>
    </NavoXCinematic>
  );
}
