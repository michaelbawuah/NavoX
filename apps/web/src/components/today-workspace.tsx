"use client";

import Link from "next/link";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { TodaySource } from "../lib/source-references";
import { consumerTodaySections } from "../lib/today-sections";
import { AISettings } from "./ai-settings";
import { ApprovalPanel } from "./approval-panel";
import { CommunicationDrafts } from "./communication-drafts";
import { ConnectionsPanel } from "./connections-panel";
import { DismissCommitment } from "./dismiss-commitment";
import {
  IntelligenceControls,
  IntelligenceFeedback,
  WorkspaceContext,
} from "./intelligence-controls";
import { OperationalAssistance } from "./operational-assistance";
import { PagedList } from "./paged-list";
import { ProactivePanel } from "./proactive-panel";
import { SourceReferences } from "./source-references";
import styles from "./today-workspace.module.css";
import { WorkspaceShell } from "./workspace-shell";

interface Account {
  id: string;
  email: string;
  display_name: string | null;
  timezone?: string;
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
  band?: string;
  factors?: Record<string, number>;
  suggested_capability?: string | null;
  sources: TodaySource[];
}

/** Today keeps raw connector resource types; Gmail is not a knowledge EMAIL row. */
export function workspaceItemGroups<
  T extends Pick<TodayItem, "type" | "sources">,
>(items: T[]): { inbox: T[]; planner: T[] } {
  const isEmail = (item: T) =>
    item.sources.some(
      (source) =>
        (source.provider === "google" &&
          source.source_type === "gmail_message") ||
        ["EMAIL", "EMAIL_THREAD"].includes(source.source_type),
    );
  return {
    inbox: items.filter(isEmail),
    planner: items.filter(
      (item) =>
        item.type !== "renewal" &&
        (!isEmail(item) || ["meeting", "deadline"].includes(item.type)),
    ),
  };
}

interface TodayPayload {
  generated_at: string;
  timezone: string;
  total: number;
  set_aside?: TodayItem[];
  needs_attention: TodayItem[];
  coming_up: TodayItem[];
  renewals: TodayItem[];
  waiting_on: TodayItem[];
  completed_recently: TodayItem[];
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

export type TodayWorkspaceView =
  | "today"
  | "inbox"
  | "planner"
  | "connections"
  | "settings";

interface TodayWorkspaceProps {
  view?: TodayWorkspaceView;
  account: Account;
  connections: GoogleConnection[];
  connectionsUnavailable?: boolean;
  message: string;
  onConnectionsChanged: () => Promise<void>;
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

function dueLabel(value: string | null, timezone: string): string {
  if (value === null || !Number.isFinite(Date.parse(value))) {
    return "No due date";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: timezone,
  }).format(new Date(value));
}

function greeting(timezone: string): string {
  const hour = Number(
    new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      hour: "numeric",
      hourCycle: "h23",
    }).format(new Date()),
  );
  if (hour < 12) {
    return "Good morning";
  }
  if (hour < 18) {
    return "Good afternoon";
  }
  return "Good evening";
}

export function TodayWorkspace({
  view = "today",
  account,
  connections,
  connectionsUnavailable = false,
  message,
  onConnectionsChanged,
  onSignOut,
}: TodayWorkspaceProps) {
  const needsConnectionSetup =
    connections.length === 0 && !connectionsUnavailable;
  const [showSetAside, setShowSetAside] = useState(false);
  const [timezone, setTimezone] = useState(account.timezone ?? "UTC");
  const [today, setToday] = useState<TodayPayload | null>(null);
  const [workspaceError, setWorkspaceError] = useState("");
  const [workspaceMessage, setWorkspaceMessage] = useState("");
  const [loadingToday, setLoadingToday] = useState(true);
  const todayRequest = useRef(0);
  const [mutatingId, setMutatingId] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [commitmentType, setCommitmentType] = useState("task");
  const [priority, setPriority] = useState("3");
  const [dueAt, setDueAt] = useState("");
  const [creating, setCreating] = useState(false);

  const [agentPaused, setAgentPaused] = useState(false);
  const [togglingAgent, setTogglingAgent] = useState(false);
  const [handlingId, setHandlingId] = useState<string | null>(null);
  const [activePlan, setActivePlan] = useState<PlanDetail | null>(null);
  const [recentPlans, setRecentPlans] = useState<PlanSummary[]>([]);

  const refreshToday = useCallback(async () => {
    const requestNumber = ++todayRequest.current;
    setLoadingToday(true);
    setWorkspaceError("");
    const params = new URLSearchParams({ timezone });
    try {
      const response = await fetch(`${apiBaseUrl}/today?${params.toString()}`, {
        credentials: "include",
        cache: "no-store",
      });
      if (requestNumber !== todayRequest.current) return;
      if (!response.ok) {
        setWorkspaceError(await readApiError(response));
        return;
      }
      const payload = (await response.json()) as TodayPayload;
      if (requestNumber === todayRequest.current) setToday(payload);
    } catch {
      if (requestNumber === todayRequest.current)
        setWorkspaceError("Your day could not be refreshed. Please try again.");
    } finally {
      if (requestNumber === todayRequest.current) setLoadingToday(false);
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
    const refreshVisible = () => {
      if (document.visibilityState === "visible") void refreshToday();
    };
    document.addEventListener("visibilitychange", refreshVisible);
    const timer = window.setInterval(refreshVisible, 60_000);
    return () => {
      document.removeEventListener("visibilitychange", refreshVisible);
      window.clearInterval(timer);
      todayRequest.current += 1;
    };
  }, [refreshToday]);

  useEffect(() => {
    void refreshPlans();
    void refreshAgentState();
  }, [refreshAgentState, refreshPlans]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const target = document.getElementById(window.location.hash.slice(1));
      if (!target) return;
      target.tabIndex = -1;
      target.scrollIntoView();
      target.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    if (activePlan === null || isTerminalPlan(activePlan.status)) {
      return;
    }
    const timer = window.setTimeout(() => {
      void loadPlan(activePlan.id);
    }, 900);
    return () => window.clearTimeout(timer);
  }, [activePlan, loadPlan]);

  const sections = useMemo(
    () =>
      consumerTodaySections(today, today ? Date.parse(today.generated_at) : 0),
    [today],
  );

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
      setWorkspaceMessage("Added to your day.");
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
          ? "Marked complete. Your day is up to date."
          : action === "dismiss"
            ? "Item marked as not a task and removed from Today."
            : "Updated.",
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
        "NavoX is working on this. You’ll be asked to approve any email before it is sent.",
      );
      await refreshPlans();
    } catch {
      setWorkspaceError("NavoX couldn’t start this. Please try again.");
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
          ? "NavoX is paused. Resume assistance when you’re ready."
          : "NavoX is ready to help again.",
      );
    } catch {
      setWorkspaceError("NavoX could not update the agent state.");
    } finally {
      setTogglingAgent(false);
    }
  }

  function renderActions(item: TodayItem) {
    if (["completed", "cancelled", "dismissed"].includes(item.status))
      return null;
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
    if (item.status === "waiting" || item.status === "waiting_on_external") {
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
          <DismissCommitment
            createdBy={item.created_by}
            status={item.status}
            busy={mutatingId === item.id}
            onDismiss={() => void mutateCommitment(item.id, "dismiss")}
          />
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
        <DismissCommitment
          createdBy={item.created_by}
          status={item.status}
          busy={mutatingId === item.id}
          onDismiss={() => void mutateCommitment(item.id, "dismiss")}
        />
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

  const allItems = useMemo(() => {
    const seen = new Set<string>();
    return [
      ...sections["Needs your attention"],
      ...sections["Coming up"],
      ...sections.Updates,
    ].filter((item) => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    });
  }, [sections]);
  const { inbox: inboxItems, planner: plannerItems } =
    workspaceItemGroups(allItems);
  const agenda = plannerItems
    .filter(
      (item) =>
        item.due_at !== null &&
        Number.isFinite(Date.parse(item.due_at)) &&
        !["completed", "cancelled", "dismissed"].includes(item.status),
    )
    .sort((a, b) => Date.parse(a.due_at ?? "") - Date.parse(b.due_at ?? ""));
  const renewals = allItems.filter(
    (item) =>
      item.type === "renewal" &&
      !["completed", "cancelled", "dismissed"].includes(item.status),
  );
  const page = {
    today: {
      current: "Today" as const,
      title: `${greeting(timezone)}, ${firstName}.`,
      copy: "Your priorities and plans, in one place.",
    },
    inbox: {
      current: "Inbox" as const,
      title: "Inbox",
      copy: "Review what needs a reply. Prepare a draft, then approve the final message.",
    },
    planner: {
      current: "Planner" as const,
      title: "Planner",
      copy: "Keep your tasks, meetings, and deadlines together.",
    },
    connections: {
      current: "Connections" as const,
      title: "Connected apps",
      copy: "Choose which accounts NavoX can use and manage their access.",
    },
    settings: {
      current: "Settings" as const,
      title: "Settings",
      copy: "Make NavoX work the way you do.",
    },
  }[view];

  function renderSection(
    label: string,
    items: TodayItem[],
    emptyCopy: string,
    setAside = false,
  ) {
    return (
      <section
        className={styles.sectionCard}
        aria-label={label}
        aria-busy={loadingToday}
      >
        <div className={styles.sectionHeading}>
          <h2>{label}</h2>
          {!loadingToday && (
            <span className={styles.sectionCount}>{items.length}</span>
          )}
        </div>
        {items.length === 0 ? (
          <div className={styles.emptyState}>
            <p>
              {loadingToday
                ? "Loading your saved items…"
                : workspaceError
                  ? "Your saved items could not be refreshed. Try again using Refresh."
                  : emptyCopy}
            </p>
            {!loadingToday && !workspaceError && (
              <Link href={needsConnectionSetup ? "/connections" : "/planner"}>
                {needsConnectionSetup ? "Connect your apps" : "Add a task"}
              </Link>
            )}
          </div>
        ) : (
          <PagedList
            key={label}
            label={label}
            items={items.map((item) => (
              <article className={styles.item} key={item.id}>
                <div className={styles.itemTopline}>
                  <span>{item.type.replaceAll("_", " ")}</span>
                  <span>
                    {item.status === "candidate"
                      ? "Suggested"
                      : item.status.replaceAll("_", " ")}
                  </span>
                </div>
                <h3>{item.title}</h3>
                <span className={styles.itemMeta}>
                  {setAside
                    ? "Review when you have a moment"
                    : item.due_at
                      ? dueLabel(item.due_at, timezone)
                      : item.status === "completed"
                        ? "Completed"
                        : "No due date"}
                </span>
                {item.description && <p>{item.description}</p>}
                {setAside ? (
                  <div className={styles.itemActions}>
                    <button
                      type="button"
                      disabled={mutatingId === item.id}
                      onClick={() => void mutateCommitment(item.id, "keep")}
                    >
                      Keep in Today
                    </button>
                    <button
                      type="button"
                      disabled={mutatingId === item.id}
                      onClick={() => void mutateCommitment(item.id, "dismiss")}
                    >
                      Not a task
                    </button>
                  </div>
                ) : (
                  renderActions(item)
                )}
                <details className={styles.evidence}>
                  <summary>Source and feedback</summary>
                  <p>
                    {item.created_by === "user"
                      ? "You added this."
                      : item.status === "candidate"
                        ? "Suggested from your connected apps. Confirm it if it belongs on your list."
                        : "From your connected apps."}
                  </p>
                  <SourceReferences
                    sources={item.sources}
                    paused={agentPaused}
                    formatDate={(value) => dueLabel(value, timezone)}
                  />
                  <IntelligenceFeedback
                    commitmentId={item.id}
                    onRefresh={refreshToday}
                  />
                </details>
              </article>
            ))}
          />
        )}
      </section>
    );
  }

  function renderCapture() {
    return (
      <section className={styles.controlCard} aria-labelledby="capture-heading">
        <h2 id="capture-heading">Add a task</h2>
        <p className={styles.mutedCopy}>
          Save a task, meeting, or deadline to your planner.
        </p>
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
                <option value="1">Low</option>
                <option value="2">Moderate</option>
                <option value="3">Standard</option>
                <option value="4">Important</option>
                <option value="5">Critical</option>
              </select>
            </label>
          </div>
          <label>
            Due · this device&apos;s local time
            <input
              onChange={(event) => setDueAt(event.target.value)}
              type="datetime-local"
              value={dueAt}
            />
          </label>
          <button disabled={creating} type="submit">
            {creating ? "Saving…" : "Add task"}
          </button>
        </form>
      </section>
    );
  }

  function renderActivity() {
    return (
      <section className={`${styles.controlCard} ${styles.agentCard}`}>
        <div className={styles.controlHeading}>
          <h2>NavoX activity</h2>
          <span className={styles.controlMeta}>You’re in control</span>
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
                ? "Resume assistance"
                : "Pause assistance"}
          </button>
        </div>
        <p className={styles.mutedCopy}>
          NavoX can help prepare your next step. Review and approve the exact
          message before any email is sent.
        </p>
        {activePlan ? (
          <div className={styles.planPanel} aria-live="polite">
            <div className={styles.planHeader}>
              <div>
                <small>Selected activity</small>
                <h3>{activePlan.goal}</h3>
              </div>
              <span data-status={activePlan.status}>
                {activePlan.status.replaceAll("_", " ")}
              </span>
            </div>
            <ol className={styles.planSteps}>
              {activePlan.steps.map((step) => (
                <li key={step.id}>
                  <div className={styles.stepNumber}>
                    {step.sequence_number}
                  </div>
                  <div className={styles.stepBody}>
                    <strong>{step.description}</strong>
                    <small>{step.status.replaceAll("_", " ")}</small>
                  </div>
                </li>
              ))}
            </ol>
            {activePlan.error_code && (
              <p className={styles.planError}>
                NavoX couldn’t finish this. Review the activity or try again.
              </p>
            )}
            <Link className={styles.textLink} href="/inbox#approvals">
              Review email approvals
            </Link>
          </div>
        ) : recentPlans.length > 0 ? (
          <div className={styles.recentPlans}>
            <small>Recent activity</small>
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
            Choose <strong>Handle this</strong> on a confirmed task to ask NavoX
            for help.
          </p>
        )}
      </section>
    );
  }

  function renderAgenda() {
    return (
      <section
        className={styles.sectionCard}
        aria-labelledby="agenda-heading"
        aria-busy={loadingToday}
      >
        <div className={styles.sectionHeading}>
          <h2 id="agenda-heading">Upcoming agenda</h2>
          <Link href="/planner">View planner</Link>
        </div>
        {agenda.length > 0 ? (
          <ol className={styles.agendaList}>
            {agenda.slice(0, 5).map((item) => (
              <li key={item.id}>
                <time dateTime={item.due_at ?? undefined}>
                  {dueLabel(item.due_at, timezone)}
                </time>
                <strong>{item.title}</strong>
                <span>{item.type.replaceAll("_", " ")}</span>
              </li>
            ))}
          </ol>
        ) : (
          <div className={styles.emptyState}>
            <p>
              {loadingToday
                ? "Loading your agenda…"
                : workspaceError
                  ? "Your agenda could not be refreshed."
                  : "No upcoming meetings or deadlines are saved yet."}
            </p>
            {!loadingToday && !workspaceError && (
              <Link href="/planner">Plan your next step</Link>
            )}
          </div>
        )}
      </section>
    );
  }

  return (
    <WorkspaceShell
      current={page.current}
      accountName={account.display_name ?? account.email}
      onSignOut={onSignOut}
    >
      <div className={styles.workspace}>
        <header className={styles.pageHeader}>
          <div>
            <p className={styles.kicker}>{page.current}</p>
            <h1>{page.title}</h1>
            <p className={styles.heroCopy}>{page.copy}</p>
          </div>
          {["today", "inbox", "planner"].includes(view) && (
            <button
              className={styles.secondaryAction}
              disabled={loadingToday}
              onClick={() => void refreshToday()}
              type="button"
            >
              {loadingToday ? "Refreshing…" : "Refresh"}
            </button>
          )}
        </header>

        {(workspaceError || workspaceMessage || message) && (
          <div
            className={
              workspaceError ? styles.errorBanner : styles.statusBanner
            }
            role={workspaceError ? "alert" : "status"}
          >
            {workspaceError || workspaceMessage || message}
          </div>
        )}

        {view === "today" && (
          <div className={styles.focusLayout}>
            <div className={styles.focusColumn}>
              {needsConnectionSetup && (
                <section
                  className={styles.setupCard}
                  aria-labelledby="setup-heading"
                >
                  <span className={styles.setupIcon} aria-hidden="true">
                    ↗
                  </span>
                  <div>
                    <h2 id="setup-heading">Make your day easier</h2>
                    <p>
                      Connect your email and calendar to see requests and
                      deadlines here. You can also start with a task.
                    </p>
                    <div className={styles.setupActions}>
                      <Link className={styles.primaryLink} href="/connections">
                        Connect your apps
                      </Link>
                      <Link className={styles.textLink} href="/planner">
                        Add a task
                      </Link>
                    </div>
                  </div>
                </section>
              )}
              {renderSection(
                "Needs your attention",
                sections["Needs your attention"],
                "You’re caught up on your saved items. New requests will appear here as your apps sync.",
              )}
              <section
                className={styles.quickLinks}
                aria-label="Continue your day"
              >
                <Link href="/inbox#approvals">
                  <strong>Review email</strong>
                  <span>
                    Drafts, replies, and approvals{" "}
                    <span aria-hidden="true">→</span>
                  </span>
                </Link>
                <Link href="/navox">
                  <strong>Ask NavoX</strong>
                  <span>
                    Talk through your next step{" "}
                    <span aria-hidden="true">→</span>
                  </span>
                </Link>
              </section>
              {sections.Updates.length > 0 && (
                <details className={styles.toolDisclosure}>
                  <summary>
                    Recent updates <span>{sections.Updates.length}</span>
                  </summary>
                  {renderSection(
                    "Updates",
                    sections.Updates,
                    "No recent updates.",
                  )}
                </details>
              )}
              <details
                className={styles.toolDisclosure}
                open={activePlan !== null}
              >
                <summary>Activity</summary>
                {renderActivity()}
              </details>
            </div>
            <aside
              className={styles.toolsColumn}
              aria-label="Your day at a glance"
            >
              <WorkspaceContext
                timezone={timezone}
                onTimezoneChange={setTimezone}
              />
              {renderAgenda()}
              <section
                className={styles.sectionCard}
                aria-labelledby="renewals-heading"
                aria-busy={loadingToday}
              >
                <div className={styles.sectionHeading}>
                  <h2 id="renewals-heading">Upcoming renewals</h2>
                  <Link href="/subscriptions">View all</Link>
                </div>
                {renewals.length > 0 ? (
                  <ul className={styles.renewalList}>
                    {renewals.slice(0, 3).map((item) => (
                      <li key={item.id}>
                        <strong>{item.title}</strong>
                        <span>{dueLabel(item.due_at, timezone)}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className={styles.emptyState}>
                    {loadingToday
                      ? "Loading renewals…"
                      : workspaceError
                        ? "Renewals could not be refreshed."
                        : "No upcoming renewals in your saved items."}
                  </p>
                )}
              </section>
            </aside>
          </div>
        )}

        {view === "inbox" && (
          <div className={styles.layout}>
            <nav className={styles.viewTabs} aria-label="Inbox sections">
              <a href="#needs-reply">Needs reply</a>
              <a href="#drafts">Drafts</a>
              <a href="#approvals">Approvals</a>
              <a href="#follow-ups">Follow-ups</a>
            </nav>
            <div id="needs-reply">
              {renderSection(
                "Needs reply",
                inboxItems.filter(
                  (item) =>
                    ![
                      "completed",
                      "cancelled",
                      "dismissed",
                      "waiting",
                      "waiting_on_external",
                    ].includes(item.status),
                ),
                "No saved email requests need a reply. Connect and sync Gmail to bring them here.",
              )}
            </div>
            <section
              id="drafts"
              className={styles.panelSlot}
              aria-label="Email drafts"
            >
              <CommunicationDrafts
                commitments={approvalCommitments}
                connections={connections}
                paused={agentPaused}
                onStateChanged={refreshToday}
              />
            </section>
            <section
              id="approvals"
              className={styles.panelSlot}
              aria-label="Exact message approvals"
            >
              <ApprovalPanel
                agentPaused={agentPaused}
                commitments={approvalCommitments}
                connections={connections}
                onStateChanged={refreshToday}
              />
            </section>
            <div id="follow-ups">
              {renderSection(
                "Follow-ups",
                inboxItems.filter((item) =>
                  ["waiting", "waiting_on_external"].includes(item.status),
                ),
                "No email follow-ups are waiting right now.",
              )}
            </div>
            {(today?.set_aside?.length ?? 0) > 0 && (
              <div className={styles.setAside}>
                <button
                  type="button"
                  aria-expanded={showSetAside}
                  aria-controls="set-aside-items"
                  onClick={() => setShowSetAside(!showSetAside)}
                >
                  Suggestions set aside <span>{today?.set_aside?.length}</span>
                </button>
                <p>
                  Older or unverified suggestions stay here until you decide to
                  keep them.
                </p>
                {showSetAside && (
                  <div id="set-aside-items">
                    {renderSection(
                      "Set aside",
                      today?.set_aside ?? [],
                      "No suggestions set aside.",
                      true,
                    )}
                  </div>
                )}
              </div>
            )}
            <details
              className={styles.toolDisclosure}
              open={activePlan !== null}
            >
              <summary>Activity</summary>
              {renderActivity()}
            </details>
          </div>
        )}

        {view === "planner" && (
          <div className={styles.focusLayout}>
            <div className={styles.focusColumn}>
              {renderSection(
                "Tasks & events",
                plannerItems.filter(
                  (item) =>
                    !["completed", "cancelled", "dismissed"].includes(
                      item.status,
                    ),
                ),
                "Your planner is ready. Add your first task or connect Calendar and Canvas for meetings and deadlines.",
              )}
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
              <OperationalAssistance items={allItems} paused={agentPaused} />
              <details className={styles.toolDisclosure}>
                <summary>Completed & recent updates</summary>
                {renderSection(
                  "Planner updates",
                  plannerItems.filter((item) =>
                    ["completed", "cancelled", "dismissed"].includes(
                      item.status,
                    ),
                  ),
                  "No completed tasks yet.",
                )}
              </details>
              <details
                className={styles.toolDisclosure}
                open={activePlan !== null}
              >
                <summary>Activity</summary>
                {renderActivity()}
              </details>
            </div>
            <aside className={styles.toolsColumn} aria-label="Plan your day">
              {renderCapture()}
              {renderAgenda()}
              <div className={styles.connectionHint}>
                <p>Bring meetings and assignments into your planner.</p>
                <Link href="/connections">Manage Calendar &amp; Canvas</Link>
              </div>
            </aside>
          </div>
        )}

        {view === "connections" && (
          <div className={styles.layout}>
            <ConnectionsPanel
              agentPaused={agentPaused}
              onConnectionsChanged={onConnectionsChanged}
            />
            <IntelligenceControls
              connections={connections}
              paused={agentPaused}
              onRefresh={refreshToday}
            />
          </div>
        )}

        {view === "settings" && (
          <div className={styles.settingsGrid}>
            <div className={styles.focusColumn}>
              <section className={styles.controlCard}>
                <h2>Account</h2>
                <dl className={styles.accountDetails}>
                  <div>
                    <dt>Name</dt>
                    <dd>{account.display_name ?? "Not set"}</dd>
                  </div>
                  <div>
                    <dt>Email</dt>
                    <dd>{account.email}</dd>
                  </div>
                  <div>
                    <dt>Workspace</dt>
                    <dd>{account.workspace.name}</dd>
                  </div>
                </dl>
                <Link className={styles.textLink} href="/connections">
                  Manage connected apps
                </Link>
              </section>
              <AISettings />
            </div>
            <div className={styles.focusColumn}>
              <WorkspaceContext
                timezone={timezone}
                onTimezoneChange={setTimezone}
              />
              {renderActivity()}
            </div>
          </div>
        )}
      </div>
    </WorkspaceShell>
  );
}
