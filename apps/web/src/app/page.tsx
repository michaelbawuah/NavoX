"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { NavoXCinematic } from "../components/navox-cinematic";
import { TodayWorkspace } from "../components/today-workspace";
import { type Account, authenticate } from "../lib/account-access";

type AuthMode = "register" | "login";

interface GoogleConnection {
  id: string;
  provider: "google";
  external_email: string | null;
  status: string;
  granted_scopes: string[];
  last_checked_at: string | null;
  last_error: string | null;
}

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export default function Home() {
  const [mode, setMode] = useState<AuthMode>("register");
  const [account, setAccount] = useState<Account | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [connections, setConnections] = useState<GoogleConnection[]>([]);
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
        const response = await fetch(`${apiBaseUrl}/auth/me`, {
          credentials: "include",
        });
        if (response.ok) {
          setAccount((await response.json()) as Account);
          const connectionResponse = await fetch(
            `${apiBaseUrl}/connections/google`,
            { credentials: "include" },
          );
          if (connectionResponse.ok) {
            setConnections(
              (await connectionResponse.json()) as GoogleConnection[],
            );
          }
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
    } catch (error) {
      // Do not leave older approval/read controls advertising a stale active account.
      setConnections([]);
      throw error;
    }
  }, []);

  async function signOut() {
    setIsLoading(true);
    await fetch(`${apiBaseUrl}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
    setAccount(null);
    setConnections([]);
    setMessage("");
    setIsLoading(false);
  }

  if (!sessionRestored && account === null) {
    return (
      <main aria-live="polite" className="shell">
        Preparing your workspace…
      </main>
    );
  }

  if (account !== null) {
    return (
      <TodayWorkspace
        account={account}
        connections={connections}
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
          <span>YOUR WORKSPACE</span>
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
          <p className="access-step">Step 01 · Identity</p>
          <h2 id="auth-heading" tabIndex={-1}>
            {mode === "register" ? "Make yourself at home" : "Welcome back"}
          </h2>
          <p>
            {mode === "register"
              ? "A clearer day starts with your own workspace."
              : "Your workspace is ready when you are."}
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
                ? "Create my workspace"
                : "Sign in"}
          </button>
        </form>
        <p className="access-footnote">
          <span aria-hidden="true" className="access-footnote-symbol">
            ⌁
          </span>
          Passwords are protected with modern hashing.
        </p>
      </section>
    </NavoXCinematic>
  );
}
