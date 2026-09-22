"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";
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

interface ProactivePanelProps {
  timezone: string;
  agentPaused: boolean;
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

function humanTier(value: string): string {
  if (value === "notify_now") {
    return "Notify now";
  }
  return value.replaceAll("_", " ");
}

function capabilityLabel(value: string | null): string {
  if (value === "meeting.prepare") {
    return "Prepare meeting";
  }
  if (value === "commitment.review") {
    return "Review commitment";
  }
  if (value === "commitment.handle") {
    return "Handle this";
  }
  return "Keep watching";
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function ProactivePanel({
  timezone,
  agentPaused,
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
      setMessage(mode === "snooze" ? "Snoozed for four hours." : "Signal dismissed.");
      await load();
    } catch {
      setError("NavoX could not update that proactive signal.");
    } finally {
      setMutatingSignal(null);
    }
  }

  async function savePreferences(event: FormEvent<HTMLFormElement>): Promise<void> {
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
      setMessage("Proactive controls updated.");
      await load();
    } catch {
      setError("NavoX could not save proactive controls.");
    } finally {
      setSaving(false);
    }
  }

  const signals = briefing
    ? [...briefing.notify_now, ...briefing.briefing, ...briefing.dashboard]
    : [];

  return (
    <section className={styles.shell} aria-busy={loading}>
      <div className={styles.heading}>
        <div>
          <p>Proactive NavoX</p>
          <h2>What deserves attention before it becomes a problem?</h2>
        </div>
        <div className={styles.headingActions}>
          <span className={agentPaused ? styles.paused : styles.live}>
            {agentPaused ? "Delivery paused" : "Intelligence live"}
          </span>
          <button disabled={activating} onClick={() => void activate()} type="button">
            {activating ? "Starting…" : "Enable durable scheduling"}
          </button>
        </div>
      </div>

      <div className={styles.summary}>
        <div>
          <span>Operational briefing</span>
          <strong>{loading ? "Re-evaluating current state…" : briefing?.headline}</strong>
        </div>
        <div className={styles.metrics}>
          <span>
            <b>{briefing?.notify_now.length ?? 0}</b>
            notify-now tier
          </span>
          <span>
            <b>{briefing?.briefing.length ?? 0}</b>
            briefing tier
          </span>
          <span>
            <b>{briefing?.dashboard.length ?? 0}</b>
            dashboard tier
          </span>
        </div>
      </div>

      {(message || error) && (
        <p className={error ? styles.error : styles.message} role={error ? "alert" : "status"}>
          {error || message}
          {workflowCount !== null && !error ? ` · ${workflowCount} workflow(s)` : ""}
        </p>
      )}

      {signals.length > 0 ? (
        <div className={styles.signalGrid}>
          {signals.slice(0, 6).map((signal) => (
            <article className={styles.signal} key={signal.id}>
              <div className={styles.signalTopline}>
                <span data-tier={signal.tier}>{humanTier(signal.tier)}</span>
                <b>{signal.attention_score}</b>
              </div>
              <h3>{signal.what_happening}</h3>
              <p>{signal.why_matters}</p>
              <div className={styles.scoreLine}>
                {Object.entries(signal.score_components)
                  .filter(([, value]) => value !== 0)
                  .slice(0, 4)
                  .map(([name, value]) => (
                    <small key={name}>
                      {name.replaceAll("_", " ")} {value > 0 ? "+" : ""}
                      {value}
                    </small>
                  ))}
              </div>
              <div className={styles.signalFooter}>
                <span>{capabilityLabel(signal.suggested_capability)}</span>
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
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className={styles.empty}>
          {loading
            ? "Reading current operational state…"
            : "Nothing proactive needs surfacing right now."}
        </p>
      )}

      {meeting && (
        <div className={styles.meeting}>
          <div>
            <p>Next meeting prep</p>
            <h3>{meeting.title}</h3>
            <span>
              {formatTime(meeting.starts_at)} · about {meeting.minutes_until} min
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
        <button onClick={() => setShowControls((value) => !value)} type="button">
          {showControls ? "Hide attention controls" : "Attention controls"}
        </button>
        <small>
          Notify-now is NavoX&apos;s highest in-app priority tier; no OS push
          transport is implied.
        </small>
      </div>

      {showControls && preference && (
        <form className={styles.controls} onSubmit={savePreferences}>
          <label>
            Quiet hours start
            <input
              onChange={(event) =>
                setPreference({ ...preference, quiet_hours_start: event.target.value })
              }
              type="time"
              value={preference.quiet_hours_start}
            />
          </label>
          <label>
            Quiet hours end
            <input
              onChange={(event) =>
                setPreference({ ...preference, quiet_hours_end: event.target.value })
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
