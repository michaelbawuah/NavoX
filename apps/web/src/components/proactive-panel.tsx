"use client";

import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import styles from "./proactive-panel.module.css";

interface ProactiveSignal {
  id: string;
  commitment_id: string | null;
  signal_type: string;
  status: string;
  tier: string;
  attention_score: number;
  score_components: Record<string, number>;
  what_happening: string;
  why_matters: string;
  suggested_capability: string | null;
  last_evaluated_at: string;
  last_surfaced_at: string | null;
  surface_count: number;
  snoozed_until: string | null;
}

interface Briefing {
  request_id: string;
  generated_at: string;
  timezone: string;
  headline: string;
  notify_now: ProactiveSignal[];
  briefing: ProactiveSignal[];
  dashboard: ProactiveSignal[];
}

interface Preference {
  timezone: string;
  notifications_enabled: boolean;
  quiet_hours_start: string;
  quiet_hours_end: string;
  daily_briefing_hour: number;
  notify_threshold: number;
  briefing_threshold: number;
  dashboard_threshold: number;
  max_interruptions_per_day: number;
  cooldown_minutes: number;
}

interface MeetingPrep {
  commitment_id: string;
  title: string;
  starts_at: string;
  minutes_until: number;
  description: string | null;
  related_commitments: Array<{
    id: string;
    title: string;
    status: string;
  }>;
  prep_points: string[];
}

interface Activation {
  workflows: Array<{
    entity_type: string;
    entity_id: string;
    workflow_type: string;
    status: string;
  }>;
}

export interface DeadlineItem {
  id: string;
  title: string;
  status: string;
  priority: number;
  due_at: string | null;
}

interface ProactivePanelProps {
  timezone: string;
  agentPaused: boolean;
  deadlines: DeadlineItem[];
  completedDeadlines: DeadlineItem[];
  handlingId: string | null;
  onHandleCommitment: (commitmentId: string, title: string) => Promise<void>;
}

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

async function apiError(response: Response): Promise<string> {
  const body = (await response.json().catch(() => null)) as {
    detail?: string | { message?: string };
  } | null;
  if (typeof body?.detail === "string") {
    return body.detail;
  }
  if (body?.detail && typeof body.detail.message === "string") {
    return body.detail.message;
  }
  return "NavoX could not complete that proactive request.";
}

function formatTime(value: string): string {
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

type DeadlineTone = "urgent" | "upcoming" | "completed";

function deadlineTone(item: DeadlineItem): DeadlineTone {
  if (item.status === "completed") {
    return "completed";
  }
  if (item.due_at === null) {
    return "upcoming";
  }
  const hours = (new Date(item.due_at).getTime() - Date.now()) / (60 * 60 * 1000);
  return hours <= 48 ? "urgent" : "upcoming";
}

function deadlineTiming(item: DeadlineItem): string {
  if (item.status === "completed") {
    return "Completed";
  }
  if (item.due_at === null) {
    return "No due date";
  }
  const due = new Date(item.due_at);
  const hours = (due.getTime() - Date.now()) / (60 * 60 * 1000);
  if (hours < 0) {
    const overdueHours = Math.max(1, Math.round(Math.abs(hours)));
    return overdueHours < 24
      ? `Overdue by about ${overdueHours}h`
      : `Overdue by about ${Math.max(1, Math.round(overdueHours / 24))}d`;
  }
  if (hours < 24) {
    return `Due in about ${Math.max(1, Math.round(hours))}h`;
  }
  return `Due in about ${Math.max(1, Math.round(hours / 24))}d`;
}

function signalLabel(signal: ProactiveSignal): string {
  if (signal.tier === "notify_now") {
    return "Important now";
  }
  if (signal.tier === "briefing") {
    return "Briefing";
  }
  return "On your radar";
}

export function ProactivePanel({
  timezone,
  agentPaused,
  deadlines,
  completedDeadlines,
  handlingId,
  onHandleCommitment,
}: ProactivePanelProps) {
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [preference, setPreference] = useState<Preference | null>(null);
  const [meeting, setMeeting] = useState<MeetingPrep | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activating, setActivating] = useState(false);
  const [mutatingSignal, setMutatingSignal] = useState<string | null>(null);
  const [workflowCount, setWorkflowCount] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [showControls, setShowControls] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const params = new URLSearchParams({ timezone });
    try {
      const [briefingResponse, preferenceResponse, meetingResponse] =
        await Promise.all([
          fetch(`${apiBaseUrl}/proactive/briefing?${params.toString()}`, {
            credentials: "include",
          }),
          fetch(`${apiBaseUrl}/proactive/preferences`, {
            credentials: "include",
          }),
          fetch(`${apiBaseUrl}/proactive/meeting-prep`, {
            credentials: "include",
          }),
        ]);
      if (!briefingResponse.ok) {
        setError(await apiError(briefingResponse));
        return;
      }
      if (!preferenceResponse.ok) {
        setError(await apiError(preferenceResponse));
        return;
      }
      if (!meetingResponse.ok) {
        setError(await apiError(meetingResponse));
        return;
      }
      setBriefing((await briefingResponse.json()) as Briefing);
      setPreference((await preferenceResponse.json()) as Preference);
      setMeeting((await meetingResponse.json()) as MeetingPrep | null);
    } catch {
      setError("NavoX could not reach proactive intelligence.");
    } finally {
      setLoading(false);
    }
  }, [timezone]);

  useEffect(() => {
    void load();
  }, [load]);

  const deadlineRows = useMemo(
    () => [
      ...deadlines.map((item) => ({ ...item, tone: deadlineTone(item) })),
      ...completedDeadlines.map((item) => ({
        ...item,
        tone: "completed" as const,
      })),
    ],
    [completedDeadlines, deadlines],
  );

  const otherSignals = useMemo(() => {
    if (!briefing) {
      return [];
    }
    return [
      ...briefing.notify_now,
      ...briefing.briefing,
      ...briefing.dashboard,
    ].filter((signal) => signal.signal_type !== "deadline_warning");
  }, [briefing]);

  async function activate(): Promise<void> {
    setActivating(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${apiBaseUrl}/proactive/activate`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) {
        setError(await apiError(response));
        return;
      }
      const body = (await response.json()) as Activation;
      setWorkflowCount(body.workflows.length);
      setMessage(
        body.workflows.length > 0
          ? "Durable proactive scheduling is active."
          : "Proactive scheduling is active.",
      );
    } catch {
      setError("NavoX could not start proactive scheduling.");
    } finally {
      setActivating(false);
    }
  }

  async function mutateSignal(
    signalId: string,
    mode: "dismiss" | "snooze",
  ): Promise<void> {
    setMutatingSignal(signalId);
    setError("");
    try {
      const options: RequestInit =
        mode === "snooze"
          ? {
              method: "POST",
              credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                until: new Date(Date.now() + 4 * 60 * 60 * 1000).toISOString(),
              }),
            }
          : {
              method: "POST",
              credentials: "include",
            };
      const response = await fetch(
        `${apiBaseUrl}/proactive/signals/${signalId}/${mode}`,
        options,
      );
      if (!response.ok) {
        setError(await apiError(response));
        return;
      }
      setMessage(
        mode === "snooze" ? "Snoozed for four hours." : "Signal dismissed.",
      );
      await load();
    } catch {
      setError("NavoX could not update that proactive signal.");
    } finally {
      setMutatingSignal(null);
    }
  }

  async function savePreferences(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (preference === null) {
      return;
    }
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${apiBaseUrl}/proactive/preferences`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...preference, timezone }),
      });
      if (!response.ok) {
        setError(await apiError(response));
        return;
      }
      setPreference((await response.json()) as Preference);
      setMessage("Attention controls updated.");
      await load();
    } catch {
      setError("NavoX could not save attention controls.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={styles.shell} aria-busy={loading}>
      <div className={styles.heading}>
        <div>
          <p>Proactive NavoX</p>
          <h2>Deadlines to meet</h2>
          <span className={styles.headingCopy}>
            A clear view of what is urgent, what is coming up, and what you have
            already finished.
          </span>
        </div>
        <div className={styles.headingActions}>
          <span className={agentPaused ? styles.paused : styles.live}>
            {agentPaused ? "Delivery paused" : "Intelligence live"}
          </span>
          <button
            disabled={activating}
            onClick={() => void activate()}
            type="button"
          >
            {activating ? "Starting…" : "Enable durable scheduling"}
          </button>
        </div>
      </div>

      <div className={styles.legend} aria-label="Deadline status legend">
        <span data-tone="urgent"><i />Urgent</span>
        <span data-tone="upcoming"><i />Upcoming</span>
        <span data-tone="completed"><i />Completed</span>
      </div>

      {(message || error) && (
        <p
          className={error ? styles.error : styles.message}
          role={error ? "alert" : "status"}
        >
          {error || message}
          {workflowCount !== null && !error
            ? ` · ${workflowCount} workflow(s)`
            : ""}
        </p>
      )}

      {deadlineRows.length > 0 ? (
        <div className={styles.deadlineList}>
          {deadlineRows.slice(0, 8).map((item) => {
            const isCompleted = item.tone === "completed";
            const canHandle = !agentPaused && item.status !== "candidate";
            return (
              <article
                className={styles.deadlineRow}
                data-tone={item.tone}
                key={item.id}
              >
                <span className={styles.statusRail} aria-hidden="true" />
                <div className={styles.deadlineBody}>
                  <div className={styles.deadlineTopline}>
                    <span className={styles.deadlineStatus}>
                      {item.tone === "urgent"
                        ? "Urgent"
                        : item.tone === "completed"
                          ? "Completed"
                          : "Upcoming"}
                    </span>
                    <span>{priorityLabel(item.priority)} priority</span>
                  </div>
                  <strong>{item.title}</strong>
                  <div className={styles.deadlineMeta}>
                    <span>{deadlineTiming(item)}</span>
                    {item.due_at && (
                      <span>{formatTime(item.due_at)}</span>
                    )}
                  </div>
                </div>
                <div className={styles.deadlineAction}>
                  {isCompleted ? (
                    <span className={styles.completedMark}>✓ Done</span>
                  ) : (
                    <button
                      disabled={!canHandle || handlingId === item.id}
                      onClick={() =>
                        void onHandleCommitment(item.id, item.title)
                      }
                      type="button"
                    >
                      {item.status === "candidate"
                        ? "Review first"
                        : handlingId === item.id
                          ? "Planning…"
                          : "Handle this"}
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <p className={styles.empty}>
          {loading
            ? "Reading your deadlines…"
            : "No deadlines are currently saved."}
        </p>
      )}

      {otherSignals.length > 0 && (
        <div className={styles.radar}>
          <div className={styles.radarHeading}>
            <p>Also on your radar</p>
            <span>{otherSignals.length}</span>
          </div>
          <div className={styles.radarGrid}>
            {otherSignals.slice(0, 4).map((signal) => (
              <article className={styles.radarItem} key={signal.id}>
                <span>{signalLabel(signal)}</span>
                <strong>{signal.what_happening}</strong>
                <p>{signal.why_matters}</p>
                <div>
                  <button
                    disabled={mutatingSignal === signal.id}
                    onClick={() => void mutateSignal(signal.id, "snooze")}
                    type="button"
                  >
                    Snooze 4h
                  </button>
                  <button
                    disabled={mutatingSignal === signal.id}
                    onClick={() => void mutateSignal(signal.id, "dismiss")}
                    type="button"
                  >
                    Dismiss
                  </button>
                </div>
              </article>
            ))}
          </div>
        </div>
      )}

      {meeting && (
        <div className={styles.meeting}>
          <div>
            <p>Next meeting prep</p>
            <h3>{meeting.title}</h3>
            <span>
              {formatTime(meeting.starts_at)} · about {meeting.minutes_until}{" "}
              min
            </span>
          </div>
          <ul>
            {meeting.prep_points.slice(0, 4).map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
        </div>
      )}

      <div className={styles.controlsToggle}>
        <button
          onClick={() => setShowControls((value) => !value)}
          type="button"
        >
          {showControls ? "Hide attention controls" : "Attention controls"}
        </button>
        <small>
          Red means the deadline is overdue or within 48 hours. Yellow means it
          is upcoming. Green records recently completed work.
        </small>
      </div>

      {showControls && preference && (
        <form className={styles.controls} onSubmit={savePreferences}>
          <label>
            Quiet hours start
            <input
              onChange={(event) =>
                setPreference({
                  ...preference,
                  quiet_hours_start: event.target.value,
                })
              }
              type="time"
              value={preference.quiet_hours_start}
            />
          </label>
          <label>
            Quiet hours end
            <input
              onChange={(event) =>
                setPreference({
                  ...preference,
                  quiet_hours_end: event.target.value,
                })
              }
              type="time"
              value={preference.quiet_hours_end}
            />
          </label>
          <label>
            Daily briefing hour
            <input
              max="23"
              min="0"
              onChange={(event) =>
                setPreference({
                  ...preference,
                  daily_briefing_hour: Number(event.target.value),
                })
              }
              type="number"
              value={preference.daily_briefing_hour}
            />
          </label>
          <label>
            Daily interruption budget
            <input
              max="20"
              min="0"
              onChange={(event) =>
                setPreference({
                  ...preference,
                  max_interruptions_per_day: Number(event.target.value),
                })
              }
              type="number"
              value={preference.max_interruptions_per_day}
            />
          </label>
          <label className={styles.check}>
            <input
              checked={preference.notifications_enabled}
              onChange={(event) =>
                setPreference({
                  ...preference,
                  notifications_enabled: event.target.checked,
                })
              }
              type="checkbox"
            />
            Allow proactive interruption tier
          </label>
          <button disabled={saving} type="submit">
            {saving ? "Saving…" : "Save attention controls"}
          </button>
        </form>
      )}
    </section>
  );
}
