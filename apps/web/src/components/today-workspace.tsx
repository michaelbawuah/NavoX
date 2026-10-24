"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { GmailMeetingSource } from "../lib/inbox-meeting-proposal";
import { productFeatures } from "../lib/product-features";
import { hasGoogleReadAccess } from "../lib/setup-progress";
import type { TodaySource } from "../lib/source-references";
import {
  notifyStudentTimezoneChanged,
  studentLocalTime,
} from "../lib/student-time";
import { consumerTodaySections } from "../lib/today-sections";
import { AISettings } from "./ai-settings";
import { AppearanceSettings } from "./appearance-settings";
import { ApprovalPanel } from "./approval-panel";
import { CommunicationDrafts } from "./communication-drafts";
import { ConnectionsPanel } from "./connections-panel";
import { DismissCommitment } from "./dismiss-commitment";
import { InboxEmailPreview } from "./inbox-email-preview";
import { InboxMessage } from "./inbox-message";
import { InboxWelcome } from "./inbox-welcome";
import inboxStyles from "./inbox-workspace.module.css";
import {
  IntelligenceControls,
  IntelligenceFeedback,
} from "./intelligence-controls";
import { NoaSettings } from "./noa-settings";
import { OperationalAssistance } from "./operational-assistance";
import { OutlookConnectionPanel } from "./outlook-connection-panel";
import { OutlookInbox } from "./outlook-inbox";
import { PagedList } from "./paged-list";
import { PersonalMemoryPanel } from "./personal-memory";
import { PersonalRoutines } from "./personal-routines";
import { ProactivePanel } from "./proactive-panel";
import { RoutineAlarmSettings } from "./routine-alarm-settings";
import { SourceReferences } from "./source-references";
import { StudentCalendarConnections } from "./student-calendar-connections";
import { StudentClassesWorkspace } from "./student-classes";
import { StudentGoalsCard } from "./student-goals";
import { StudentLmsFeeds } from "./student-lms-feeds";
import { StudentNotifications } from "./student-notifications";
import { StudentRecoveryCard } from "./student-recovery";
import { ThreadWatchPanel } from "./thread-watch-panel";
import { TodayHome } from "./today-home";
import styles from "./today-workspace.module.css";
import { VoxUsageReport } from "./vox-usage-report";
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

export function workspaceCanDraftReply(
  item: Pick<TodayItem, "type" | "status" | "sources">,
  connections: GoogleConnection[],
): boolean {
  return (
    ["task", "follow_up", "promise"].includes(item.type) &&
    !["candidate", "completed", "cancelled", "dismissed"].includes(
      item.status,
    ) &&
    item.sources.some(
      (source) =>
        source.provider === "google" &&
        source.source_type === "gmail_message" &&
        !!source.external_resource_id?.trim() &&
        connections.some(
          (connection) =>
            connection.id === source.connection_id &&
            connection.status === "active" &&
            connection.granted_scopes.includes(
              "https://www.googleapis.com/auth/gmail.readonly",
            ),
        ),
    )
  );
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

export interface TodayWorkspaceProps {
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

function focusSection(id: string) {
  const target = document.getElementById(id);
  if (!target) return;
  target.closest("details")?.setAttribute("open", "");
  target.tabIndex = -1;
  target.scrollIntoView({ block: "start", behavior: "smooth" });
  target.focus({ preventScroll: true });
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
  return studentLocalTime(new Date(), timezone).greeting;
}

export function TodayWorkspace(props: TodayWorkspaceProps) {
  if (!props.view || props.view === "today") return <TodayHome {...props} />;
  if (
    props.view === "inbox" &&
    !props.connectionsUnavailable &&
    !props.connections.some(
      (c) =>
        c.status === "active" &&
        c.granted_scopes.some((scope) =>
          [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
          ].includes(scope),
        ),
    )
  )
    return (
      <WorkspaceShell
        current="Inbox"
        accountName={props.account.display_name ?? props.account.email}
        accountKey={`${props.account.workspace.id}:${props.account.id}`}
        timezone={props.account.timezone}
        onSignOut={props.onSignOut}
      >
        <OutlookInbox
          accountKey={`${props.account.workspace.id}:${props.account.id}`}
          timezone={props.account.timezone}
          fallback={<InboxWelcome />}
        />
      </WorkspaceShell>
    );
  return <LegacyTodayWorkspace {...props} />;
}

function LegacyTodayWorkspace({
  view = "today",
  account,
  connections,
  connectionsUnavailable = false,
  message,
  onConnectionsChanged,
  onSignOut,
}: TodayWorkspaceProps) {
  const router = useRouter();
  const needsConnectionSetup =
    !connectionsUnavailable &&
    !connections.some((connection) => connection.status === "active");
  const _needsReadSetup = !connections.some(
    (connection) =>
      connection.status === "active" && hasGoogleReadAccess(connection),
  );
  const [_showSetAside, _setShowSetAside] = useState(false);
  const [timezone, setTimezone] = useState(account.timezone ?? "UTC");
  useEffect(() => {
    setTimezone(account.timezone ?? "UTC");
  }, [account.timezone]);
  const [today, setToday] = useState<TodayPayload | null>(null);
  const [workspaceError, setWorkspaceError] = useState("");
  const [todayError, setTodayError] = useState("");
  const [inboxTab, setInboxTab] = useState<"needs" | "drafts" | "waiting">(
    "needs",
  );
  const [draftCount, setDraftCount] = useState(0);
  const [selectedReplyId, setSelectedReplyId] = useState<string | null>(null);
  const [readMeetingSource, setReadMeetingSource] =
    useState<GmailMeetingSource | null>(null);
  const [replySelectionKey, setReplySelectionKey] = useState(0);
  const [reviewId, _setReviewId] = useState<string | null>(null);
  const handledRouteSelection = useRef(false);
  const [workspaceMessage, setWorkspaceMessage] = useState("");
  const [loadingToday, setLoadingToday] = useState(true);
  const [retryingConnections, setRetryingConnections] = useState(false);
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

  async function retryConnections() {
    setRetryingConnections(true);
    try {
      await onConnectionsChanged();
    } catch {
      // AccountWorkspace keeps the connection error visible for another retry.
    } finally {
      setRetryingConnections(false);
    }
  }

  const refreshToday = useCallback(async () => {
    const requestNumber = ++todayRequest.current;
    setLoadingToday(true);
    setTodayError("");
    const params = new URLSearchParams({ timezone });
    try {
      const response = await fetch(`${apiBaseUrl}/today?${params.toString()}`, {
        credentials: "include",
        cache: "no-store",
      });
      if (requestNumber !== todayRequest.current) return;
      if (!response.ok) {
        setTodayError(await readApiError(response));
        return;
      }
      const payload = (await response.json()) as TodayPayload;
      if (requestNumber === todayRequest.current) setToday(payload);
    } catch {
      if (requestNumber === todayRequest.current)
        setTodayError("Your day could not be refreshed. Please try again.");
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
        if (
          seen.has(item.id) ||
          ["candidate", "completed", "cancelled", "dismissed"].includes(
            item.status,
          )
        ) {
          return false;
        }
        seen.add(item.id);
        return true;
      })
      .map((item) => ({
        id: item.id,
        title: item.title,
        description: item.description,
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
            goal: `Prepare the next step for ${itemTitle}`,
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
        "NavoX is preparing a suggested next step. Check Activity for the result.",
      );
      await refreshPlans();
    } catch {
      setWorkspaceError("NavoX couldn’t start this. Please try again.");
    } finally {
      setHandlingId(null);
    }
  }

  function canDraftReply(item: TodayItem) {
    return workspaceCanDraftReply(item, connections);
  }

  function openReply(item: TodayItem) {
    if (view !== "inbox") {
      router.push(`/inbox?reply=${encodeURIComponent(item.id)}#drafts`);
      return;
    }
    setInboxTab("needs");
    setSelectedReplyId(item.id);
    setReplySelectionKey((key) => key + 1);
    focusSection("drafts");
  }

  async function handleCommitment(item: TodayItem) {
    if (canDraftReply(item)) {
      openReply(item);
      return;
    }
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
            {handlingId === item.id
              ? "Preparing…"
              : canDraftReply(item)
                ? "Draft reply"
                : "Plan next step"}
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
          {handlingId === item.id
            ? "Preparing…"
            : canDraftReply(item)
              ? "Draft reply"
              : "Plan next step"}
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
  const _renewals = allItems.filter(
    (item) =>
      item.type === "renewal" &&
      !["completed", "cancelled", "dismissed"].includes(item.status),
  );
  const reviewItem = allItems.find(
    (item) => item.id === reviewId && item.status === "candidate",
  );

  useEffect(() => {
    if (
      view !== "inbox" ||
      !today ||
      loadingToday ||
      handledRouteSelection.current
    )
      return;
    const requestedId = new URLSearchParams(window.location.search).get(
      "reply",
    );
    const owned = approvalCommitments.find((item) => item.id === requestedId);
    handledRouteSelection.current = true;
    if (owned) {
      setSelectedReplyId(owned.id);
      setReplySelectionKey((key) => key + 1);
      focusSection("drafts");
    }
  }, [view, today, loadingToday, approvalCommitments]);

  const reviewItemId = reviewItem?.id;
  useEffect(() => {
    if (!reviewItemId) return;
    const frame = window.requestAnimationFrame(() =>
      focusSection("review-suggestion"),
    );
    return () => window.cancelAnimationFrame(frame);
  }, [reviewItemId]);

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
                : todayError
                  ? "Your saved items could not be refreshed. Try again using Refresh."
                  : emptyCopy}
            </p>
            {!loadingToday && !todayError && (
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
      <section
        className={styles.controlCard}
        aria-labelledby="capture-heading"
        id="add-task"
      >
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
                {activePlan.status === "completed"
                  ? "Next step prepared"
                  : activePlan.status.replaceAll("_", " ")}
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
                <b>
                  {plan.status === "completed"
                    ? "Next step prepared"
                    : plan.status.replaceAll("_", " ")}
                </b>
              </button>
            ))}
          </div>
        ) : (
          <p className={styles.emptyAgent}>
            Choose <strong>Plan next step</strong> on a confirmed task to
            prepare a suggestion. For email requests, choose{" "}
            <strong>Draft reply</strong>.
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
                : todayError
                  ? "Your agenda could not be refreshed."
                  : "No upcoming meetings or deadlines are saved yet."}
            </p>
            {!loadingToday && !todayError && (
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
      timezone={timezone}
      accountKey={`${account.workspace.id}:${account.id}`}
      accountName={account.display_name ?? account.email}
      onSignOut={onSignOut}
    >
      <div className={styles.workspace}>
        {connectionsUnavailable && (
          <div className={styles.errorBanner} role="alert">
            <p>{message || "Connected apps could not be loaded."}</p>
            <button
              className={styles.secondaryAction}
              disabled={retryingConnections}
              onClick={() => void retryConnections()}
              type="button"
            >
              {retryingConnections
                ? "Checking connections…"
                : "Retry connections"}
            </button>
          </div>
        )}

        {(workspaceError ||
          (view !== "today" && todayError) ||
          workspaceMessage ||
          (message && !connectionsUnavailable)) && (
          <div
            className={
              workspaceError || (view !== "today" && todayError)
                ? styles.errorBanner
                : styles.statusBanner
            }
            role={
              workspaceError || (view !== "today" && todayError)
                ? "alert"
                : "status"
            }
          >
            {workspaceError ||
              (view !== "today" && todayError) ||
              workspaceMessage ||
              message}
          </div>
        )}

        {view === "inbox" && (
          <div>
            <OutlookInbox
              accountKey={`${account.workspace.id}:${account.id}`}
              timezone={timezone}
            />
            <nav className={inboxStyles.tabs} aria-label="Inbox sections">
              <button
                type="button"
                aria-pressed={inboxTab === "needs"}
                onClick={() => setInboxTab("needs")}
              >
                Needs reply ·{" "}
                {
                  inboxItems.filter(
                    (i) =>
                      ![
                        "completed",
                        "cancelled",
                        "dismissed",
                        "waiting",
                        "waiting_on_external",
                      ].includes(i.status),
                  ).length
                }
              </button>
              <button
                type="button"
                aria-pressed={inboxTab === "drafts"}
                onClick={() => setInboxTab("drafts")}
              >
                Drafts · {draftCount}
              </button>
              <button
                type="button"
                aria-pressed={inboxTab === "waiting"}
                onClick={() => setInboxTab("waiting")}
              >
                Waiting for reply ·{" "}
                {
                  inboxItems.filter((i) =>
                    ["waiting", "waiting_on_external"].includes(i.status),
                  ).length
                }
              </button>
            </nav>
            <div className={inboxStyles.layout}>
              <div className={inboxStyles.list}>
                {productFeatures.studentEmail && (
                  <Link href="/email-actions">
                    Review an email for a task or deadline
                  </Link>
                )}
                {inboxTab !== "drafts" &&
                  inboxItems
                    .filter((i) =>
                      inboxTab === "waiting"
                        ? ["waiting", "waiting_on_external"].includes(i.status)
                        : ![
                            "completed",
                            "cancelled",
                            "dismissed",
                            "waiting",
                            "waiting_on_external",
                          ].includes(i.status),
                    )
                    .map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className={inboxStyles.row}
                        aria-pressed={selectedReplyId === item.id}
                        onClick={() => {
                          setSelectedReplyId(item.id);
                          setReplySelectionKey((k) => k + 1);
                        }}
                      >
                        <InboxEmailPreview
                          key={`${account.workspace.id}:${account.id}:${item.id}`}
                          commitmentId={item.id}
                          accountKey={`${account.workspace.id}:${account.id}`}
                          title={item.title}
                          description={item.description}
                        />
                        <small>
                          {item.reasons.slice(0, 2).join(" · ") ||
                            "Review this email request"}
                        </small>
                      </button>
                    ))}
                {inboxTab === "drafts" && (
                  <p>
                    Open a saved draft on the right. Your edits stay here until
                    you save or discard them.
                  </p>
                )}
                {inboxTab === "needs" &&
                  !inboxItems.some(
                    (i) =>
                      ![
                        "completed",
                        "cancelled",
                        "dismissed",
                        "waiting",
                        "waiting_on_external",
                      ].includes(i.status),
                  ) && (
                    <p>
                      You’re caught up. Emails that need you will appear here.
                    </p>
                  )}
              </div>
              <div className={inboxStyles.detail}>
                {selectedReplyId && inboxTab !== "drafts" && (
                  <InboxMessage
                    key={selectedReplyId}
                    id={selectedReplyId}
                    accountKey={`${account.workspace.id}:${account.id}`}
                    timezone={timezone}
                    onReadSource={setReadMeetingSource}
                  />
                )}
                <div
                  id="drafts"
                  className={inboxStyles.panel}
                  hidden={inboxTab === "waiting"}
                >
                  <CommunicationDrafts
                    key={`${account.id}:${account.workspace.id}`}
                    accountKey={`${account.workspace.id}:${account.id}`}
                    timezone={timezone}
                    commitments={approvalCommitments}
                    selectedCommitmentId={selectedReplyId}
                    readMeetingSource={
                      readMeetingSource?.accountKey ===
                        `${account.workspace.id}:${account.id}` &&
                      readMeetingSource.commitmentId === selectedReplyId
                        ? readMeetingSource
                        : null
                    }
                    selectionKey={replySelectionKey}
                    connections={connections}
                    paused={agentPaused}
                    onStateChanged={refreshToday}
                    onDraftCount={setDraftCount}
                  />
                </div>
                <div
                  id="approvals"
                  className={inboxStyles.panel}
                  hidden={inboxTab !== "drafts"}
                >
                  <ApprovalPanel
                    timezone={timezone}
                    showComposer={false}
                    agentPaused={agentPaused}
                    commitments={approvalCommitments}
                    connections={connections}
                    onStateChanged={refreshToday}
                  />
                </div>
                <div
                  className={inboxStyles.panel}
                  hidden={inboxTab !== "waiting"}
                >
                  <ThreadWatchPanel
                    timezone={timezone}
                    key={`${account.id}:${account.workspace.id}`}
                    paused={agentPaused}
                    eligibleCommitmentIds={allItems
                      .filter(canDraftReply)
                      .map((i) => i.id)}
                    onDraftReply={(id) => {
                      const item = allItems.find((i) => i.id === id);
                      if (item && canDraftReply(item)) openReply(item);
                    }}
                  />
                </div>
              </div>
            </div>
          </div>
        )}

        {view === "planner" && (
          <div className={styles.focusLayout}>
            <div className={styles.focusColumn}>
              {productFeatures.studentRecovery && <StudentRecoveryCard />}
              {productFeatures.studentGoals && <StudentGoalsCard />}
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
              <PersonalRoutines
                key={`${account.id}:${account.workspace.id}`}
                paused={agentPaused}
                timezone={timezone}
              />
              <details className={styles.toolDisclosure}>
                <summary>Briefing and notification preferences</summary>
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
              </details>
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
          <>
            <OutlookConnectionPanel
              accountKey={`${account.workspace.id}:${account.id}`}
            />
            {productFeatures.studentCalendarAvailability && (
              <StudentCalendarConnections
                key={`calendar:${account.workspace.id}:${account.id}`}
                accountId={`${account.workspace.id}:${account.id}`}
              />
            )}
            {productFeatures.studentLmsFeeds && (
              <StudentLmsFeeds
                key={`${account.workspace.id}:${account.id}`}
                accountId={`${account.workspace.id}:${account.id}`}
                planningEnabled={productFeatures.studentPlanning}
              />
            )}
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
          </>
        )}

        {view === "settings" && (
          <div className={styles.settingsLayout}>
            <nav className={styles.settingsNav} aria-label="Settings sections">
              {[
                ["profile", "Profile"],
                ["school-term", "School & term"],
                ["settings-connections", "Connections"],
                ["notifications", "Notifications"],
                ["voice-noa", "Voice & Noa"],
                ["memory-privacy", "Memory & privacy"],
                ["appearance", "Appearance"],
                ["routine-alarm", "Routine & alarm"],
              ].map(([id, label]) => (
                <a key={id} href={`#${id}`}>
                  {label}
                </a>
              ))}
            </nav>
            <div className={styles.focusColumn}>
              <section id="profile" className={styles.controlCard}>
                <h2>Profile</h2>
                <dl className={styles.accountDetails}>
                  <div>
                    <dt>Name</dt>
                    <dd>{account.display_name ?? "Not set"}</dd>
                  </div>
                  <div>
                    <dt>Email</dt>
                    <dd>{account.email}</dd>
                  </div>
                </dl>
              </section>
              <section id="school-term" aria-label="School and term">
                <StudentClassesWorkspace
                  settingsOnly
                  accountId={`${account.workspace.id}:${account.id}`}
                  onTimezoneChange={(zone) =>
                    notifyStudentTimezoneChanged(
                      `${account.workspace.id}:${account.id}`,
                      zone,
                    )
                  }
                />
              </section>
              <section id="settings-connections" className={styles.controlCard}>
                <h2>Connections</h2>
                <p>
                  Choose the apps NavoX can use and review their permissions.
                </p>
                <Link className={styles.textLink} href="/connections">
                  Manage connected apps
                </Link>
              </section>
              <div id="notifications">
                <StudentNotifications
                  accountId={`${account.workspace.id}:${account.id}`}
                />
              </div>
              <div id="voice-noa">
                <NoaSettings
                  key={`${account.workspace.id}:${account.id}`}
                  accountKey={`${account.workspace.id}:${account.id}`}
                />
              </div>
              <div id="memory-privacy">
                <PersonalMemoryPanel
                  key={`${account.id}:${account.workspace.id}`}
                />
              </div>
              <div id="appearance">
                <AppearanceSettings
                  accountKey={`${account.workspace.id}:${account.id}`}
                />
              </div>
              {productFeatures.studentPlanning && (
                <RoutineAlarmSettings
                  key={`${account.workspace.id}:${account.id}`}
                  accountKey={`${account.workspace.id}:${account.id}`}
                />
              )}
              <details className={styles.toolDisclosure}>
                <summary>AI and usage</summary>
                <AISettings />
                <VoxUsageReport
                  key={`${account.workspace.id}:${account.id}`}
                  accountId={`${account.workspace.id}:${account.id}`}
                />
              </details>
            </div>
          </div>
        )}
      </div>
    </WorkspaceShell>
  );
}
