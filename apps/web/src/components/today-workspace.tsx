"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import styles from "./today-workspace.module.css";

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

interface TodaySource {
  provider: string;
  source_type: string;
  external_resource_id: string | null;
}

interface TodayItem {
  id: string;
  type: string;
  title: string;
  description: string | null;
  status: string;
  priority: number;
  due_at: string | null;
  confidence: number;
  created_by: string;
  score: number;
  reasons: string[];
  sources: TodaySource[];
}

interface TodayPayload {
  generated_at: string;
  timezone: string;
  total: number;
  needs_attention: TodayItem[];
  coming_up: TodayItem[];
  renewals: TodayItem[];
  waiting_on: TodayItem[];
}

interface QueryPayload {
  intent: string;
  answer: string;
  items: TodayItem[];
  supported_queries: string[];
}

interface TodayWorkspaceProps {
  account: Account;
  connections: GoogleConnection[];
  message: string;
  isConnectingGoogle: boolean;
  checkingConnectionId: string | null;
  onConnectGoogle: () => Promise<void>;
  onCheckGoogle: (connectionId: string) => Promise<void>;
  onSignOut: () => Promise<void>;
}

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

async function readApiError(response: Response): Promise<string> {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
  } | null;
  return body?.detail ?? "NavoX could not complete that request.";
}

function dueLabel(value: string | null): string {
  if (value === null) {
    return "No due date";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) {
    return "Good morning";
  }
  if (hour < 18) {
    return "Good afternoon";
  }
  return "Good evening";
}

function sectionEmpty(label: string): string {
  if (label === "Needs Attention") {
    return "Nothing is asking for your attention right now.";
  }
  if (label === "Coming Up") {
    return "No upcoming commitments are saved yet.";
  }
  if (label === "Money / Renewals") {
    return "No active renewals are saved.";
  }
  return "Nothing is currently waiting.";
}

export function TodayWorkspace({
  account,
  connections,
  message,
  isConnectingGoogle,
  checkingConnectionId,
  onConnectGoogle,
  onCheckGoogle,
  onSignOut,
}: TodayWorkspaceProps) {
  const [timezone, setTimezone] = useState("UTC");
  const [today, setToday] = useState<TodayPayload | null>(null);
  const [workspaceError, setWorkspaceError] = useState("");
  const [workspaceMessage, setWorkspaceMessage] = useState("");
  const [loadingToday, setLoadingToday] = useState(true);
  const [mutatingId, setMutatingId] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [commitmentType, setCommitmentType] = useState("task");
  const [priority, setPriority] = useState("3");
  const [dueAt, setDueAt] = useState("");
  const [creating, setCreating] = useState(false);

  const [query, setQuery] = useState("What do I need to know today?");
  const [queryResult, setQueryResult] = useState<QueryPayload | null>(null);
  const [querying, setQuerying] = useState(false);

  useEffect(() => {
    const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (detected) {
      setTimezone(detected);
    }
  }, []);

  const refreshToday = useCallback(async () => {
    setLoadingToday(true);
    setWorkspaceError("");
    const params = new URLSearchParams({ timezone });
    try {
      const response = await fetch(`${apiBaseUrl}/today?${params.toString()}`, {
        credentials: "include",
      });
      if (!response.ok) {
        setWorkspaceError(await readApiError(response));
        return;
      }
      setToday((await response.json()) as TodayPayload);
    } catch {
      setWorkspaceError("NavoX could not reach the workspace service.");
    } finally {
      setLoadingToday(false);
    }
  }, [timezone]);

  useEffect(() => {
    void refreshToday();
  }, [refreshToday]);

  const firstName = useMemo(() => {
    const value = account.display_name?.trim();
    return value ? value.split(/\s+/)[0] : account.email.split("@")[0];
  }, [account.display_name, account.email]);

  async function createCommitment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreating(true);
    setWorkspaceError("");
    setWorkspaceMessage("");
    try {
      const response = await fetch(`${apiBaseUrl}/commitments`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_id: crypto.randomUUID(),
          type: commitmentType,
          title,
          priority: Number(priority),
          due_at: dueAt ? new Date(dueAt).toISOString() : null,
        }),
      });
      if (!response.ok) {
        setWorkspaceError(await readApiError(response));
        return;
      }
      setTitle("");
      setDueAt("");
      setPriority("3");
      setWorkspaceMessage("Commitment added to your operational state.");
      await refreshToday();
    } catch {
      setWorkspaceError("NavoX could not save that commitment.");
    } finally {
      setCreating(false);
    }
  }

  async function mutateCommitment(id: string, action: string) {
    setMutatingId(id);
    setWorkspaceError("");
    setWorkspaceMessage("");
    try {
      const response = await fetch(
        `${apiBaseUrl}/commitments/${id}/${action}`,
        { method: "POST", credentials: "include" },
      );
      if (!response.ok) {
        setWorkspaceError(await readApiError(response));
        return;
      }
      setWorkspaceMessage(
        action === "complete"
          ? "Commitment completed. Today is refreshed."
          : "Commitment state updated.",
      );
      await refreshToday();
    } catch {
      setWorkspaceError("NavoX could not update that commitment.");
    } finally {
      setMutatingId(null);
    }
  }

  async function askNavox(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuerying(true);
    setWorkspaceError("");
    try {
      const response = await fetch(`${apiBaseUrl}/today/query`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, timezone }),
      });
      if (!response.ok) {
        setWorkspaceError(await readApiError(response));
        return;
      }
      setQueryResult((await response.json()) as QueryPayload);
    } catch {
      setWorkspaceError("NavoX could not answer that query.");
    } finally {
      setQuerying(false);
    }
  }

  function renderActions(item: TodayItem) {
    if (item.status === "candidate") {
      return (
        <div className={styles.itemActions}>
          <button
            disabled={mutatingId === item.id}
            onClick={() => void mutateCommitment(item.id, "confirm")}
            type="button"
          >
            Confirm
          </button>
          <button
            disabled={mutatingId === item.id}
            onClick={() => void mutateCommitment(item.id, "reject")}
            type="button"
          >
            Reject
          </button>
        </div>
      );
    }
    if (item.status === "waiting") {
      return (
        <div className={styles.itemActions}>
          <button
            disabled={mutatingId === item.id}
            onClick={() => void mutateCommitment(item.id, "resume")}
            type="button"
          >
            Resume
          </button>
          <button
            disabled={mutatingId === item.id}
            onClick={() => void mutateCommitment(item.id, "complete")}
            type="button"
          >
            Complete
          </button>
        </div>
      );
    }
    return (
      <div className={styles.itemActions}>
        <button
          disabled={mutatingId === item.id}
          onClick={() => void mutateCommitment(item.id, "waiting")}
          type="button"
        >
          Waiting
        </button>
        <button
          disabled={mutatingId === item.id}
          onClick={() => void mutateCommitment(item.id, "complete")}
          type="button"
        >
          Complete
        </button>
      </div>
    );
  }

  function renderSection(label: string, items: TodayItem[]) {
    return (
      <section className={styles.sectionCard}>
        <div className={styles.sectionHeading}>
          <div>
            <p>{label}</p>
            <span className={styles.sectionCount}>{items.length.toString().padStart(2, "0")}</span>
          </div>
          <i aria-hidden="true" />
        </div>
        {items.length === 0 ? (
          <p className={styles.emptyState}>{sectionEmpty(label)}</p>
        ) : (
          <div className={styles.itemList}>
            {items.map((item) => (
              <article className={styles.item} key={item.id}>
                <div className={styles.itemTopline}>
                  <span>{item.type.replaceAll("_", " ")}</span>
                  <span>Signal {item.score}</span>
                </div>
                <h3>{item.title}</h3>
                {item.description && <p>{item.description}</p>}
                <div className={styles.itemMeta}>
                  <span>{dueLabel(item.due_at)}</span>
                  <span>Priority {item.priority}</span>
                  {item.status === "candidate" && <b>Review required</b>}
                </div>
                {item.reasons.length > 0 && (
                  <div className={styles.reasons}>
                    {item.reasons.map((reason) => (
                      <span className={styles.reasonPill} key={reason}>{reason}</span>
                    ))}
                  </div>
                )}
                {item.sources.length > 0 && (
                  <p className={styles.sourceLine}>
                    Source:{" "}
                    {item.sources
                      .map((source) => source.source_type)
                      .join(", ")}
                  </p>
                )}
                {renderActions(item)}
              </article>
            ))}
          </div>
        )}
      </section>
    );
  }

  const generatedLabel = today
    ? new Intl.DateTimeFormat(undefined, {
        weekday: "long",
        month: "short",
        day: "numeric",
      }).format(new Date(today.generated_at))
    : "Today";

  return (
    <main className={styles.workspace}>
      <div className={styles.glowOne} />
      <div className={styles.glowTwo} />

      <header className={styles.topbar}>
        <a className={styles.brand} href="/" aria-label="NavoX home">
          <span aria-hidden="true" className={styles.brandMark}>
            <i />
            <i />
            <i />
          </span>
          NavoX
        </a>
        <div className={styles.topbarMeta}>
          <span>{account.workspace.name}</span>
          <span>{timezone}</span>
          <button onClick={() => void onSignOut()} type="button">
            Sign out
          </button>
        </div>
      </header>

      <section className={styles.hero}>
        <div>
          <p className={styles.kicker}>Today · {generatedLabel}</p>
          <h1>
            {greeting()}, {firstName}.<span className={styles.heroAccent}>Here&apos;s what matters now.</span>
          </h1>
          <p className={styles.heroCopy}>
            One operational view of the commitments NavoX actually has saved.
            Nothing here is fabricated, and nothing external happens from this
            screen.
          </p>
        </div>
        <div className={styles.posture}>
          <span className={styles.liveDot} />
          <div>
            <small>Operational state</small>
            <strong>
              {loadingToday ? "Syncing" : `${String(today?.total ?? 0)} active`}
            </strong>
          </div>
        </div>
      </section>

      {(workspaceError || workspaceMessage || message) && (
        <div
          className={workspaceError ? styles.errorBanner : styles.statusBanner}
          role={workspaceError ? "alert" : "status"}
        >
          {workspaceError || workspaceMessage || message}
        </div>
      )}

      <div className={styles.layout}>
        <div className={styles.primaryColumn}>
          {renderSection("Needs Attention", today?.needs_attention ?? [])}
          {renderSection("Coming Up", today?.coming_up ?? [])}
        </div>

        <aside className={styles.sideColumn}>
          <section className={styles.controlCard}>
            <div className={styles.controlHeading}>
              <p>Capture</p>
              <span className={styles.controlMeta}>Manual · explicit</span>
            </div>
            <h2>Add a commitment</h2>
            <form className={styles.captureForm} onSubmit={createCommitment}>
              <label>
                What needs to happen?
                <input
                  maxLength={256}
                  minLength={3}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="e.g. Submit ECE lab"
                  required
                  value={title}
                />
              </label>
              <div className={styles.formRow}>
                <label>
                  Type
                  <select
                    onChange={(event) => setCommitmentType(event.target.value)}
                    value={commitmentType}
                  >
                    <option value="task">Task</option>
                    <option value="deadline">Deadline</option>
                    <option value="meeting">Meeting</option>
                    <option value="follow_up">Follow-up</option>
                    <option value="promise">Promise</option>
                    <option value="renewal">Renewal</option>
                  </select>
                </label>
                <label>
                  Priority
                  <select
                    onChange={(event) => setPriority(event.target.value)}
                    value={priority}
                  >
                    <option value="1">1 · Low</option>
                    <option value="2">2</option>
                    <option value="3">3 · Normal</option>
                    <option value="4">4</option>
                    <option value="5">5 · High</option>
                  </select>
                </label>
              </div>
              <label>
                Due
                <input
                  onChange={(event) => setDueAt(event.target.value)}
                  type="datetime-local"
                  value={dueAt}
                />
              </label>
              <button disabled={creating} type="submit">
                {creating ? "Saving…" : "Add to NavoX"}
              </button>
            </form>
          </section>

          <section className={styles.controlCard}>
            <div className={styles.controlHeading}>
              <p>Ask NavoX</p>
              <span className={styles.controlMeta}>Read-only</span>
            </div>
            <h2>What do you need to know?</h2>
            <form className={styles.queryForm} onSubmit={askNavox}>
              <label className={styles.visuallyHidden} htmlFor="navox-query">
                Ask NavoX
              </label>
              <input
                id="navox-query"
                maxLength={500}
                onChange={(event) => setQuery(event.target.value)}
                value={query}
              />
              <button disabled={querying} type="submit">
                {querying ? "Reading state…" : "Ask"}
              </button>
            </form>
            {queryResult && (
              <div className={styles.queryAnswer} aria-live="polite">
                <p>{queryResult.answer}</p>
                {queryResult.items.length > 0 && (
                  <ul>
                    {queryResult.items.slice(0, 5).map((item) => (
                      <li key={item.id}>{item.title}</li>
                    ))}
                  </ul>
                )}
                {queryResult.intent === "unsupported" && (
                  <small>
                    Try today, attention, this week, waiting, renewals, or
                    promises.
                  </small>
                )}
              </div>
            )}
          </section>

          {renderSection("Money / Renewals", today?.renewals ?? [])}
          {renderSection("Waiting On", today?.waiting_on ?? [])}

          <section className={styles.controlCard}>
            <div className={styles.controlHeading}>
              <p>Connections</p>
              <span className={styles.controlMeta}>Identity scope only</span>
            </div>
            <h2>Google</h2>
            <p className={styles.mutedCopy}>
              The current connection verifies identity only. Gmail, Calendar,
              and Drive content are not being read.
            </p>
            {connections.length === 0 ? (
              <button
                disabled={isConnectingGoogle}
                onClick={() => void onConnectGoogle()}
                type="button"
              >
                {isConnectingGoogle ? "Opening Google…" : "Connect Google"}
              </button>
            ) : (
              <div className={styles.connectionList}>
                {connections.map((connection) => (
                  <div key={connection.id}>
                    <span>{connection.status.replaceAll("_", " ")}</span>
                    <button
                      disabled={checkingConnectionId === connection.id}
                      onClick={() => void onCheckGoogle(connection.id)}
                      type="button"
                    >
                      {checkingConnectionId === connection.id
                        ? "Checking…"
                        : "Check"}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>
    </main>
  );
}
