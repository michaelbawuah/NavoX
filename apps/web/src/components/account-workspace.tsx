"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  type Account,
  authenticate,
  type GoogleConnection,
  restoreAccountAccess,
} from "../lib/account-access";
import { NavoXCinematic } from "./navox-cinematic";
import { TodayWorkspace, type TodayWorkspaceView } from "./today-workspace";

type AuthMode = "register" | "login";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export function AccountWorkspace({
  view = "today",
}: {
  view?: TodayWorkspaceView;
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

    async function restoreSession() {
      try {
        const restored = await restoreAccountAccess(apiBaseUrl);
        if (restored.account) {
          setConnections(restored.connections);
          setConnectionsUnavailable(Boolean(restored.connectionsError));
          if (restored.connectionsError) setMessage(restored.connectionsError);
          setAccount(restored.account);
        }
      } catch {
        // The public landing page remains usable when the API is offline.
      } finally {
        setIsLoading(false);
        setSessionRestored(true);
      }
    }

    void restoreSession();
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
      setAccount(await authenticate(apiBaseUrl, mode, payload));
      setConnectionsUnavailable(false);
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
    try {
      const response = await fetch(`${apiBaseUrl}/connections/google`, {
        credentials: "include",
        cache: "no-store",
      });
      if (!response.ok)
        throw new Error("Connection controls could not refresh.");
      setConnections((await response.json()) as GoogleConnection[]);
      setConnectionsUnavailable(false);
      setMessage("");
    } catch (error) {
      // Do not leave older approval/read controls advertising a stale active account.
      setConnections([]);
      setConnectionsUnavailable(true);
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
      setAccount(null);
      setConnections([]);
      setMessage("");
    } finally {
      setIsLoading(false);
    }
  }

  // The public homepage is readable before hydration and session restoration.
  if (!sessionRestored && account === null && view !== "today") {
    return (
      <main aria-live="polite" className="shell">
        Getting NavoX ready…
      </main>
    );
  }

  if (account !== null) {
    return (
      <TodayWorkspace
        view={view}
        account={account}
        connections={connections}
        connectionsUnavailable={connectionsUnavailable}
        message={message}
        onConnectionsChanged={refreshConnections}
        onSignOut={signOut}
      />
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
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
            {mode === "register" && <small>Use at least 12 characters.</small>}
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
