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
  type AccountAccess,
  authenticate,
  type GoogleConnection,
  loadGoogleConnections,
  restoreAccountAccess,
} from "../lib/account-access";
import { productFeatures } from "../lib/product-features";
import { NavoXCinematic } from "./navox-cinematic";
import { StudentAssistantPanel } from "./student-assistant-panel";
import { StudentClassesWorkspace } from "./student-classes";
import { StudentCourseWorkspace } from "./student-course-workspace";
import { StudentDailyPlan } from "./student-daily-plan";
import { StudentDayReview } from "./student-day-review";
import { StudentEmailActions } from "./student-email-actions";
import { StudentGoals, StudentGoalsCard } from "./student-goals";
import { StudentRecovery, StudentRecoveryCard } from "./student-recovery";
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

const accountSessionChannel = "navox-account-session";

export function observeAccountSession(callbacks: {
  checking: () => void;
  restored: (access: AccountAccess) => void;
  unavailable: () => void;
}) {
  let version = 0;
  let disposed = false;
  const channel =
    typeof BroadcastChannel === "undefined"
      ? null
      : new BroadcastChannel(accountSessionChannel);

  async function check() {
    const request = ++version;
    callbacks.checking();
    try {
      const access = await restoreAccountAccess(apiBaseUrl);
      if (!disposed && request === version) callbacks.restored(access);
    } catch {
      if (!disposed && request === version) callbacks.unavailable();
    }
  }

  function visibilityChanged() {
    if (document.visibilityState === "visible") void check();
    else {
      ++version;
      callbacks.checking();
    }
  }

  const sessionChanged = () => void check();
  document.addEventListener("visibilitychange", visibilityChanged);
  channel?.addEventListener("message", sessionChanged);
  void check();
  return {
    invalidate: () => {
      ++version;
    },
    dispose: () => {
      disposed = true;
      ++version;
      document.removeEventListener("visibilitychange", visibilityChanged);
      channel?.removeEventListener("message", sessionChanged);
      channel?.close();
    },
  };
}

function notifyAccountSessionChanged() {
  if (typeof BroadcastChannel === "undefined") return;
  const channel = new BroadcastChannel(accountSessionChannel);
  channel.postMessage("session-changed");
  channel.close();
}

export function AccountWorkspace({
  view = "today",
  courseId,
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
  const sessionObserver = useRef<ReturnType<
    typeof observeAccountSession
  > | null>(null);
  const sessionGeneration = useRef(0);

  useEffect(() => {
    const connectionOutcome = new URLSearchParams(window.location.search).get(
      "google_connection",
    );
    if (connectionOutcome !== null) {
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
        failed: "Google could not complete the connection. Please try again.",
        refresh_token_required:
          "Google did not return a refresh credential. Please try connecting again.",
        scope_mismatch:
          "Google returned an unrequested permission. NavoX did not link the account.",
        account_mismatch:
          "Gmail permission was granted from a different Google account, so NavoX rejected it.",
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
      },
      restored: (restored) => {
        setAccount(restored.account);
        setConnections(restored.connections);
        setConnectionsUnavailable(Boolean(restored.connectionsError));
        if (restored.connectionsError) setMessage(restored.connectionsError);
        setIsLoading(false);
        setSessionRestored(true);
      },
      unavailable: () => {
        setAccount(null);
        setConnections([]);
        setConnectionsUnavailable(false);
        setMessage("Your sign-in could not be checked. Please sign in again.");
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

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setIsLoading(true);

    const payload =
      mode === "register"
        ? { email, password, display_name: displayName }
        : { email, password };
    try {
      const authenticated = await authenticate(apiBaseUrl, mode, payload);
      sessionObserver.current?.invalidate();
      const generation = ++sessionGeneration.current;
      notifyAccountSessionChanged();
      setAccount(authenticated);
      setConnections([]);
      setSessionRestored(false);
      try {
        const loadedConnections = await loadGoogleConnections(apiBaseUrl);
        if (generation !== sessionGeneration.current) return;
        setConnections(loadedConnections);
        setConnectionsUnavailable(false);
      } catch (error) {
        if (generation !== sessionGeneration.current) return;
        setConnections([]);
        setConnectionsUnavailable(true);
        setMessage(
          error instanceof Error
            ? error.message
            : "Connected apps could not be loaded. Please retry.",
        );
      } finally {
        if (generation === sessionGeneration.current) setSessionRestored(true);
      }
      setPassword("");
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "Something went wrong. Please try again.",
      );
    } finally {
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
    setIsLoading(true);
    try {
      const response = await fetch(`${apiBaseUrl}/auth/logout`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok)
        throw new Error("We couldn’t sign you out. Please try again.");
      sessionObserver.current?.invalidate();
      ++sessionGeneration.current;
      notifyAccountSessionChanged();
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
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              {productFeatures.studentWhiteboard && sessionRestored ? (
                <StudentWhiteboard
                  key={`${account.workspace.id}:${account.id}`}
                />
              ) : productFeatures.studentWhiteboard ? null : (
                <p>Board lessons are not available on this deployment.</p>
              )}
            </WorkspaceShell>
          ) : view === "classes" ? (
            <WorkspaceShell
              current="Classes"
              accountKey={`${account.workspace.id}:${account.id}`}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              <StudentClassesWorkspace
                key={`${account.id}:${account.workspace.id}`}
                accountId={`${account.workspace.id}:${account.id}`}
                displayName={account.display_name}
                timezone={account.timezone}
              />
            </WorkspaceShell>
          ) : view === "course" ? (
            <WorkspaceShell
              current="Classes"
              accountKey={`${account.workspace.id}:${account.id}`}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              {courseId ? (
                <StudentCourseWorkspace
                  key={`${account.workspace.id}:${account.id}:${courseId}`}
                  accountId={`${account.workspace.id}:${account.id}`}
                  courseId={courseId}
                />
              ) : (
                <p>This course could not be opened.</p>
              )}
            </WorkspaceShell>
          ) : view === "day-review" ? (
            <WorkspaceShell
              current="Today"
              accountKey={`${account.workspace.id}:${account.id}`}
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
          ) : view === "planner" && productFeatures.studentPlanning ? (
            <WorkspaceShell
              current="Planner"
              accountKey={`${account.workspace.id}:${account.id}`}
              accountName={account.display_name ?? account.email}
              onSignOut={signOut}
            >
              {productFeatures.studentRecovery && <StudentRecoveryCard />}
              {productFeatures.studentGoals && <StudentGoalsCard />}
              <StudentDailyPlan
                key={`${account.workspace.id}:${account.id}`}
                accountId={`${account.workspace.id}:${account.id}`}
              />
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
          <button className="primary-button" disabled={isLoading} type="submit">
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
