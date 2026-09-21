"use client";

import { type FormEvent, useEffect, useState } from "react";

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
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function restoreSession() {
      try {
        const response = await fetch(`${apiBaseUrl}/auth/me`, {
          credentials: "include",
        });
        if (response.ok) {
          setAccount((await response.json()) as Account);
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

  async function signOut() {
    setIsLoading(true);
    await fetch(`${apiBaseUrl}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
    setAccount(null);
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
            <strong>{account.workspace.name}</strong> is ready. Google
            connections, imported data, AI, and external actions remain off
            until their dedicated Milestone 1 steps are implemented.
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
          <button
            className="secondary-button"
            type="button"
            onClick={() => void signOut()}
          >
            Sign out
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="shell">
      <section aria-labelledby="navox-heading" className="intro">
        <p className="eyebrow">NavoX · Private by design</p>
        <h1 id="navox-heading">Operational awareness, built to earn trust.</h1>
        <p className="summary">
          Start with your own protected workspace. NavoX will never connect a
          tool or take an external action without your explicit approval.
        </p>
        <ul className="trust-list">
          <li>One personal workspace, created just for you</li>
          <li>Passwords protected with modern hashing</li>
          <li>No Google data or AI processing in this step</li>
        </ul>
      </section>

      <section aria-labelledby="auth-heading" className="auth-card">
        <div aria-label="Account access" className="auth-tabs" role="tablist">
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
        <h2 id="auth-heading">
          {mode === "register"
            ? "Create your private workspace"
            : "Welcome back"}
        </h2>
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
                ? "Create workspace"
                : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
