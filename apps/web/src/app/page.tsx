"use client";

import { type FormEvent, useEffect, useState } from "react";
import {
  AccessStage,
  LandingAtmosphere,
  MotionControl,
} from "../components/landing-art";

type AuthMode = "register" | "login";

interface Account {
  id: string;
  email: string;
  display_name: string | null;
  workspace: {
    id: string;
    name: string;
    workspace_type: string;
  };
}

interface GoogleConnection {
  id: string;
  provider: "google";
  status: string;
  granted_scopes: string[];
  last_checked_at: string | null;
  last_error: string | null;
}

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

async function readApiError(response: Response): Promise<string> {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
  } | null;
  return body?.detail ?? "Something went wrong. Please try again.";
}

export default function Home() {
  const [mode, setMode] = useState<AuthMode>("register");
  const [account, setAccount] = useState<Account | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [connections, setConnections] = useState<GoogleConnection[]>([]);
  const [isConnectingGoogle, setIsConnectingGoogle] = useState(false);
  const [checkingConnectionId, setCheckingConnectionId] = useState<
    string | null
  >(null);
  const [isLoading, setIsLoading] = useState(true);
  const [effectsPaused, setEffectsPaused] = useState(false);

  useEffect(() => {
    const connectionOutcome = new URLSearchParams(window.location.search).get(
      "google_connection",
    );
    if (connectionOutcome !== null) {
      const outcomes: Record<string, string> = {
        cancelled: "Google connection was cancelled.",
        connected:
          "Google account linked. No Gmail, Calendar, or Drive data was requested.",
        failed: "Google could not complete the connection. Please try again.",
        refresh_token_required:
          "Google did not return a refresh credential. Please try connecting again.",
        scope_mismatch:
          "Google returned an unrequested permission. NavoX did not link the account.",
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
      } finally {
        setIsLoading(false);
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
    const response = await fetch(
      `${apiBaseUrl}/auth/${mode === "register" ? "register" : "login"}`,
      {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );

    if (!response.ok) {
      setMessage(await readApiError(response));
      setIsLoading(false);
      return;
    }

    setAccount((await response.json()) as Account);
    setPassword("");
    setIsLoading(false);
  }

  async function connectGoogle() {
    setMessage("");
    setIsConnectingGoogle(true);
    const response = await fetch(`${apiBaseUrl}/connections/google/start`, {
      credentials: "include",
    });

    if (!response.ok) {
      setMessage(await readApiError(response));
      setIsConnectingGoogle(false);
      return;
    }

    const body = (await response.json()) as { authorization_url: string };
    window.location.assign(body.authorization_url);
  }

  async function checkGoogleConnection(connectionId: string) {
    setMessage("");
    setCheckingConnectionId(connectionId);
    const response = await fetch(
      `${apiBaseUrl}/connections/google/${connectionId}/health`,
      { method: "POST", credentials: "include" },
    );
    if (!response.ok) {
      setMessage(await readApiError(response));
      setCheckingConnectionId(null);
      return;
    }

    const updated = (await response.json()) as GoogleConnection;
    setConnections((current) =>
      current.map((connection) =>
        connection.id === updated.id ? updated : connection,
      ),
    );
    setMessage(
      updated.status === "active"
        ? "Google connection is healthy."
        : "Google needs to be reauthorized.",
    );
    setCheckingConnectionId(null);
  }

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

  if (isLoading && account === null) {
    return (
      <main aria-live="polite" className="shell">
        Preparing your workspace…
      </main>
    );
  }

  if (account !== null) {
    return (
      <main className="shell">
        <p className="eyebrow">NavoX · Personal workspace</p>
        <section aria-labelledby="welcome-heading" className="welcome-card">
          <p className="status">
            <span className="status-dot" />
            Account secured
          </p>
          <h1 id="welcome-heading">
            Welcome, {account.display_name ?? account.email}.
          </h1>
          <p className="summary">
            <strong>{account.workspace.name}</strong> is ready. You can link a
            Google identity with no Gmail, Calendar, or Drive data access. AI
            and external actions remain off.
          </p>
          <dl className="workspace-facts">
            <div>
              <dt>Workspace</dt>
              <dd>{account.workspace.workspace_type}</dd>
            </div>
            <div>
              <dt>Signed in as</dt>
              <dd>{account.email}</dd>
            </div>
          </dl>
          <section
            aria-labelledby="google-heading"
            className="connection-panel"
          >
            <div>
              <h2 id="google-heading">Google connection</h2>
              <p>
                This link verifies your Google identity only. It does not read
                Gmail, Calendar, or Drive data.
              </p>
            </div>
            {connections.length === 0 ? (
              <button
                className="primary-button"
                disabled={isConnectingGoogle}
                onClick={() => void connectGoogle()}
                type="button"
              >
                {isConnectingGoogle ? "Opening Google…" : "Connect Google"}
              </button>
            ) : (
              <ul className="connection-list">
                {connections.map((connection) => (
                  <li key={connection.id}>
                    <span>
                      Google · {connection.status.replaceAll("_", " ")}
                    </span>
                    <button
                      className="secondary-button"
                      disabled={checkingConnectionId === connection.id}
                      onClick={() => void checkGoogleConnection(connection.id)}
                      type="button"
                    >
                      {checkingConnectionId === connection.id
                        ? "Checking…"
                        : "Check health"}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          {message && (
            <p className="form-message" role="status">
              {message}
            </p>
          )}
          <div className="account-actions">
            <button
              className="secondary-button"
              type="button"
              onClick={() => void signOut()}
            >
              Sign out
            </button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="landing-shell" data-effects-paused={effectsPaused}>
      <div className="ambient-orb ambient-orb-one" />
      <div className="ambient-orb ambient-orb-two" />
      <LandingAtmosphere />

      <nav aria-label="NavoX" className="landing-nav">
        <a className="brand" href="#navox-heading">
          <span aria-hidden="true" className="brand-mark">
            <i />
            <i />
            <i />
          </span>
          <span>NavoX</span>
        </a>
        <div className="nav-context">
          <span className="nav-context-dot" />
          Private operations
        </div>
      </nav>

      <div className="landing-layout">
        <section
          aria-labelledby="navox-heading"
          className="intro landing-intro"
        >
          <p className="eyebrow landing-eyebrow">
            <span /> Human-approved by default
          </p>
          <h1 id="navox-heading">
            Know what matters.
            <span>Decide with confidence.</span>
          </h1>
          <p className="summary landing-summary">
            NavoX gives you a private command layer for the systems that shape
            your work—designed to keep you informed and in control.
          </p>
          <div className="hero-actions">
            <a className="primary-button hero-button" href="#access-panel">
              Enter your workspace
              <span aria-hidden="true" className="hero-button-arrow">
                ↗
              </span>
            </a>
            <p>Personal workspace · no credit card</p>
          </div>

          <section aria-labelledby="principles-heading" className="trust-grid">
            <h2 className="visually-hidden" id="principles-heading">
              NavoX principles
            </h2>
            <article>
              <span className="principle-number">01</span>
              <h2>Private by design</h2>
              <p>Your workspace starts with only the access you approve.</p>
            </article>
            <article>
              <span className="principle-number">02</span>
              <h2>Intent before action</h2>
              <p>No external action happens without your explicit approval.</p>
            </article>
            <article>
              <span className="principle-number">03</span>
              <h2>Built for clarity</h2>
              <p>Useful signals, understandable decisions, lasting control.</p>
            </article>
          </section>

          <section aria-label="Platform preview" className="system-preview">
            <div className="system-preview-head">
              <span className="preview-pulse" />
              <p>Live system posture</p>
              <span>01 / 01</span>
            </div>
            <div className="system-preview-body">
              <div>
                <span className="system-kicker">NavoX protocol</span>
                <strong>Every connection begins with consent.</strong>
              </div>
              <ul>
                <li>
                  <span>Workspace access</span>
                  <b>Protected</b>
                </li>
                <li>
                  <span>External actions</span>
                  <b>Approval required</b>
                </li>
              </ul>
            </div>
          </section>
        </section>

        <AccessStage>
          <section
            aria-labelledby="auth-heading"
            className="auth-card access-card"
            id="access-panel"
          >
            <div className="access-card-topline">
              <span>Secure access</span>
              <span>✦</span>
            </div>
            <div
              aria-label="Account access"
              className="auth-tabs"
              role="tablist"
            >
              <button
                aria-selected={mode === "register"}
                className={mode === "register" ? "active" : ""}
                onClick={() => {
                  setMode("register");
                  setMessage("");
                }}
                role="tab"
                type="button"
              >
                Create account
              </button>
              <button
                aria-selected={mode === "login"}
                className={mode === "login" ? "active" : ""}
                onClick={() => {
                  setMode("login");
                  setMessage("");
                }}
                role="tab"
                type="button"
              >
                Sign in
              </button>
            </div>
            <div className="access-card-heading">
              <p className="access-step">Step 01 · Identity</p>
              <h2 id="auth-heading">
                {mode === "register"
                  ? "Create your private workspace"
                  : "Welcome back"}
              </h2>
              <p>
                {mode === "register"
                  ? "Your workspace is yours from the first sign-in."
                  : "Sign in to return to your private command layer."}
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
                {mode === "register" && (
                  <small>Use at least 12 characters.</small>
                )}
              </label>
              {message && (
                <p className="form-message" role="alert">
                  {message}
                </p>
              )}
              <button
                className="primary-button"
                disabled={isLoading}
                type="submit"
              >
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
        </AccessStage>
      </div>

      <footer className="landing-footer">
        <p>© 2026 NavoX</p>
        <MotionControl
          paused={effectsPaused}
          onToggle={() => setEffectsPaused((current) => !current)}
        />
        <p>Private operations, on your terms.</p>
      </footer>
    </main>
  );
}
