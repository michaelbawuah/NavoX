"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { ApprovalPanel } from "./approval-panel";
import { ProactivePanel } from "./proactive-panel";
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
  external_email: string | null;
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
  completed_recently: TodayItem[];
}

interface QueryPayload {
  intent: string;
  answer: string;
  items: TodayItem[];
  supported_queries: string[];
  details: string[];
}

interface PlanAction {
  id: string;
  provider: string;
  action_type: string;
  risk_level: string;
  requires_approval: boolean;
  status: string;
  policy_reason: string | null;
  payload_hash: string;
  result: Record<string, unknown>;
  created_at: string;
  executed_at: string | null;
}

interface PlanStep {
  id: string;
  sequence_number: number;
  action_type: string;
  description: string;
  status: string;
  risk_level: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  action: PlanAction | null;
}

interface PlanSummary {
  id: string;
  commitment_id: string | null;
  goal: string;
  status: string;
  planner_version: string;
  context_hash: string;
  max_steps: number;
  replan_count: number;
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
}

interface PlanDetail extends PlanSummary {
  steps: PlanStep[];
  workflow: {
    workflow_id: string;
    status: string;
    run_id: string | null;
  } | null;
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
    detail?: string | { message?: string };
  } | null;
  if (typeof body?.detail === "string") {
    return body.detail;
  }
  if (body?.detail && typeof body.detail.message === "string") {
    return body.detail.message;
  }
  return "NavoX could not complete that request.";
}

function isTerminalPlan(status: string): boolean {
  return ["completed", "blocked", "failed", "dispatch_failed"].includes(status);
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

function priorityLabel(priority: number): string {
  const labels: Record<number, string> = {
    1: "Low",
    2: "Moderate",
    3: "Standard",
    4: "Important",
    5: "Critical",
  };
  return labels[priority] ?? "Standard";
}

function itemUrgency(
  item: TodayItem,
): "urgent" | "upcoming" | "completed" | "neutral" {
  if (item.status === "completed") {
    return "completed";
  }
  if (item.due_at === null) {
    return "neutral";
  }
  const hours =
    (new Date(item.due_at).getTime() - Date.now()) / (60 * 60 * 1000);
  return hours <= 48 ? "urgent" : "upcoming";
}

function urgencyLabel(item: TodayItem): string {
  const urgency = itemUrgency(item);
  if (urgency === "urgent") return "Urgent";
  if (urgency === "completed") return "Completed";
  if (urgency === "upcoming") return "Upcoming";
  return priorityLabel(item.priority);
}

function queryHeading(intent: string): string {
  const headings: Record<string, string> = {
    today: "Your day at a glance",
    attention: "Needs your attention",
    this_week: "Coming up this week",
    waiting: "Waiting on",
    renewals: "Money and renewals",
    promises: "Promises you made",
    forgetting: "Worth a second look",
    meeting_prep: "Next meeting prep",
    handleable: "NavoX can handle",
    unsupported: "Try another operational question",
  };
  return headings[intent] ?? "NavoX briefing";
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

  const [agentPaused, setAgentPaused] = useState(false);
  const [togglingAgent, setTogglingAgent] = useState(false);
  const [handlingId, setHandlingId] = useState<string | null>(null);
  const [activePlan, setActivePlan] = useState<PlanDetail | null>(null);
  const [recentPlans, setRecentPlans] = useState<PlanSummary[]>([]);

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

  const refreshPlans = useCallback(async () => {
    try {
      const response = await fetch(`${apiBaseUrl}/plans?limit=5`, {
        credentials: "include",
      });
      if (response.ok) {
        setRecentPlans((await response.json()) as PlanSummary[]);
      }
    } catch {
      // Today remains usable even if plan history cannot be refreshed.
    }
  }, []);

  const refreshAgentState = useCallback(async () => {
    try {
      const response = await fetch(`${apiBaseUrl}/agent/state`, {
        credentials: "include",
      });
      if (response.ok) {
        const state = (await response.json()) as { paused: boolean };
        setAgentPaused(state.paused);
      }
    } catch {
      // Execution controls fail closed server-side even if this badge is stale.
    }
  }, []);

  const loadPlan = useCallback(async (planId: string) => {
    try {
      const response = await fetch(`${apiBaseUrl}/plans/${planId}`, {
        credentials: "include",
      });
      if (response.ok) {
        setActivePlan((await response.json()) as PlanDetail);
      }
    } catch {
      // Polling is best effort; persisted state remains authoritative.
    }
  }, []);

  useEffect(() => {
    void refreshToday();
  }, [refreshToday]);

  useEffect(() => {
    void refreshPlans();
    void refreshAgentState();
  }, [refreshAgentState, refreshPlans]);

  useEffect(() => {
    if (activePlan === null || isTerminalPlan(activePlan.status)) {
      return;
    }
    const timer = window.setTimeout(() => {
      void loadPlan(activePlan.id);
    }, 900);
    return () => window.clearTimeout(timer);
  }, [activePlan, loadPlan]);

  const approvalCommitments = useMemo(() => {
    const combined = [
      ...(today?.needs_attention ?? []),
      ...(today?.coming_up ?? []),
      ...(today?.waiting_on ?? []),
    ];
    const seen = new Set<string>();
    return combined
      .filter((item) => {
        if (seen.has(item.id) || item.status === "candidate") {
          return false;
        }
        seen.add(item.id);
        return true;
      })
      .map((item) => ({
        id: item.id,
        title: item.title,
        status: item.status,
      }));
  }, [today]);

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

  async function handleCommitmentById(commitmentId: string, itemTitle: string) {
    setHandlingId(commitmentId);
    setWorkspaceError("");
    setWorkspaceMessage("");
    try {
      const response = await fetch(
        `${apiBaseUrl}/commitments/${commitmentId}/handle`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            request_id: crypto.randomUUID(),
            goal: `Safely handle ${itemTitle}`,
          }),
        },
      );
      if (!response.ok) {
        setWorkspaceError(await readApiError(response));
        return;
      }
      const plan = (await response.json()) as PlanDetail;
      setActivePlan(plan);
      setWorkspaceMessage(
        "Bounded plan started. NavoX will only execute permitted R0/R1 steps.",
      );
      await refreshPlans();
    } catch {
      setWorkspaceError("NavoX could not start that bounded plan.");
    } finally {
      setHandlingId(null);
    }
  }

  async function handleCommitment(item: TodayItem) {
    await handleCommitmentById(item.id, item.title);
  }

  async function toggleAgent() {
    setTogglingAgent(true);
    setWorkspaceError("");
    try {
      const response = await fetch(
        `${apiBaseUrl}/agent/${agentPaused ? "resume" : "pause"}`,
        { method: "POST", credentials: "include" },
      );
      if (!response.ok) {
        setWorkspaceError(await readApiError(response));
        return;
      }
      const state = (await response.json()) as { paused: boolean };
      setAgentPaused(state.paused);
      setWorkspaceMessage(
        state.paused
          ? "Agent execution paused. New plans and future steps will fail closed."
          : "Agent execution resumed.",
      );
    } catch {
      setWorkspaceError("NavoX could not update the agent state.");
    } finally {
      setTogglingAgent(false);
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
          <button
            disabled={handlingId === item.id || agentPaused}
            onClick={() => void handleCommitment(item)}
            type="button"
          >
            {handlingId === item.id ? "Planning…" : "Handle this"}
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
        <button
          disabled={handlingId === item.id || agentPaused}
          onClick={() => void handleCommitment(item)}
          type="button"
        >
          {handlingId === item.id ? "Planning…" : "Handle this"}
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
            <span className={styles.sectionCount}>
              {items.length.toString().padStart(2, "0")}
            </span>
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
                  <span data-urgency={itemUrgency(item)}>
                    {urgencyLabel(item)}
                  </span>
                </div>
                <h3>{item.title}</h3>
                {item.description && <p>{item.description}</p>}
                <div className={styles.itemMeta}>
                  <span>{dueLabel(item.due_at)}</span>
                  <span>
                    Priority {item.priority} · {priorityLabel(item.priority)}
                  </span>
                  {item.status === "candidate" && <b>Review required</b>}
                </div>
                {item.reasons.length > 0 && (
                  <div className={styles.reasons}>
                    {item.reasons.map((reason) => (
                      <span className={styles.reasonPill} key={reason}>
                        {reason}
                      </span>
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
            {greeting()}, {firstName}.
            <span className={styles.heroAccent}>
              Here&apos;s what matters now.
            </span>
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
          <ProactivePanel
            agentPaused={agentPaused}
            completedDeadlines={(today?.completed_recently ?? []).filter(
              (item) => item.type === "deadline",
            )}
            deadlines={[
              ...(today?.needs_attention ?? []),
              ...(today?.coming_up ?? []),
            ].filter((item) => item.type === "deadline")}
            handlingId={handlingId}
            onHandleCommitment={handleCommitmentById}
            timezone={timezone}
          />
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
                    <option value="2">2 · Moderate</option>
                    <option value="3">3 · Standard</option>
                    <option value="4">4 · Important</option>
                    <option value="5">5 · Critical</option>
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
                <div className={styles.queryAnswerHeader}>
                  <div>
                    <span>NavoX briefing</span>
                    <strong>{queryHeading(queryResult.intent)}</strong>
                  </div>
                  <b>
                    {queryResult.items.length > 0
                      ? `${queryResult.items.length} item${queryResult.items.length === 1 ? "" : "s"}`
                      : "Clear"}
                  </b>
                </div>
                <p className={styles.querySummary}>{queryResult.answer}</p>
                {queryResult.items.length > 0 && (
                  <div className={styles.queryFocusList}>
                    {queryResult.items.slice(0, 5).map((item) => (
                      <article className={styles.queryFocusItem} key={item.id}>
                        <div>
                          <strong>{item.title}</strong>
                          <span className={styles.queryDue}>
                            {item.due_at
                              ? dueLabel(item.due_at)
                              : "No due date"}
                          </span>
                        </div>
                        <span
                          className={styles.queryUrgency}
                          data-urgency={itemUrgency(item)}
                        >
                          {urgencyLabel(item)}
                        </span>
                      </article>
                    ))}
                  </div>
                )}
                {queryResult.details.length > 0 && (
                  <div className={styles.queryDetails}>
                    {queryResult.details.slice(0, 5).map((detail) => (
                      <p key={detail}>{detail}</p>
                    ))}
                  </div>
                )}
                {queryResult.intent === "unsupported" && (
                  <small>
                    Try today, attention, this week, waiting, renewals,
                    promises, forgetting, meeting prep, or what NavoX can
                    handle.
                  </small>
                )}
              </div>
            )}
          </section>

          <ApprovalPanel
            agentPaused={agentPaused}
            commitments={approvalCommitments}
            connections={connections}
            onStateChanged={refreshToday}
          />

          <section className={`${styles.controlCard} ${styles.agentCard}`}>
            <div className={styles.controlHeading}>
              <p>Agent runtime</p>
              <span className={styles.controlMeta}>
                Bounded · approval gated
              </span>
            </div>
            <div className={styles.agentStateRow}>
              <div>
                <span
                  className={
                    agentPaused ? styles.agentPausedDot : styles.agentLiveDot
                  }
                />
                <strong>{agentPaused ? "Paused" : "Ready"}</strong>
              </div>
              <button
                disabled={togglingAgent}
                onClick={() => void toggleAgent()}
                type="button"
              >
                {togglingAgent
                  ? "Updating…"
                  : agentPaused
                    ? "Resume agent"
                    : "Pause agent"}
              </button>
            </div>
            <p className={styles.mutedCopy}>
              Internal R0/R1 work can run automatically. Consequential R3
              actions, including Gmail send, require exact user approval before
              the provider boundary can execute them.
            </p>

            {activePlan ? (
              <div className={styles.planPanel} aria-live="polite">
                <div className={styles.planHeader}>
                  <div>
                    <small>Active plan</small>
                    <h3>{activePlan.goal}</h3>
                  </div>
                  <span data-status={activePlan.status}>
                    {activePlan.status.replaceAll("_", " ")}
                  </span>
                </div>
                <div className={styles.planMeta}>
                  <span>{activePlan.planner_version}</span>
                  <span>
                    {activePlan.steps.length} / {activePlan.max_steps} steps
                  </span>
                  <span>Replans {activePlan.replan_count} / 2</span>
                </div>
                <ol className={styles.planSteps}>
                  {activePlan.steps.map((step) => (
                    <li key={step.id}>
                      <div className={styles.stepNumber}>
                        {String(step.sequence_number).padStart(2, "0")}
                      </div>
                      <div className={styles.stepBody}>
                        <div className={styles.stepTopline}>
                          <strong>{step.description}</strong>
                          <span className={styles.riskBadge}>
                            {step.risk_level}
                          </span>
                        </div>
                        <p>{step.action_type}</p>
                        <small>{step.status.replaceAll("_", " ")}</small>
                      </div>
                    </li>
                  ))}
                </ol>
                {activePlan.error_code && (
                  <p className={styles.planError}>
                    Stopped safely: {activePlan.error_code.replaceAll("_", " ")}
                  </p>
                )}
              </div>
            ) : recentPlans.length > 0 ? (
              <div className={styles.recentPlans}>
                <small>Recent plans</small>
                {recentPlans.map((plan) => (
                  <button
                    key={plan.id}
                    onClick={() => void loadPlan(plan.id)}
                    type="button"
                  >
                    <span>{plan.goal}</span>
                    <b>{plan.status.replaceAll("_", " ")}</b>
                  </button>
                ))}
              </div>
            ) : (
              <p className={styles.emptyAgent}>
                Choose <strong>Handle this</strong> on a confirmed commitment to
                create the first bounded plan.
              </p>
            )}
          </section>

          {renderSection("Money / Renewals", today?.renewals ?? [])}
          {renderSection("Waiting On", today?.waiting_on ?? [])}

          <section className={styles.controlCard}>
            <div className={styles.controlHeading}>
              <p>Connections</p>
              <span className={styles.controlMeta}>Least privilege</span>
            </div>
            <h2>Google</h2>
            <p className={styles.mutedCopy}>
              Google identity stays minimal. Gmail send is granted separately
              when you enable it; Calendar and Drive content are still not read.
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
