"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  isSyncTerminal,
  monitorSync,
  type SyncProgress,
  type SyncSource,
  syncErrorHelp,
  syncProgressMessage,
} from "../lib/sync-progress";
import styles from "./intelligence-controls.module.css";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

async function request<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    credentials: "include",
    cache: "no-store",
    signal,
    ...(body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      typeof payload?.detail === "string"
        ? payload.detail
        : "Please try again. NavoX could not complete that request.",
    );
  }
  return payload as T;
}

interface Connection {
  id: string;
  status: string;
  external_email: string | null;
  granted_scopes: string[];
}

interface IntelligenceStatus {
  observation_count: number;
  evidence_count: number;
  active_commitments: number;
  processing_ready?: boolean;
  readiness_message?: string;
}

export function SyncReadout({
  source,
  progress,
  pollingPaused,
  onCheck,
  onReconnect,
  reconnectDisabled,
}: {
  source: SyncSource;
  progress: SyncProgress;
  pollingPaused: boolean;
  onCheck: () => void;
  onReconnect: () => void;
  reconnectDisabled: boolean;
}) {
  const failed = ["failed", "cancelled", "timed_out"].includes(progress.status);
  const help = syncErrorHelp(source, progress.error?.code);
  const showHelp =
    progress.status === "failed" || progress.status === "retrying";
  return (
    <div className={styles.syncReadout}>
      <p
        className={failed ? styles.error : styles.message}
        role={failed ? "alert" : "status"}
      >
        {source === "gmail" ? "Gmail" : "Calendar"} ·{" "}
        {syncProgressMessage(progress)}
      </p>
      {showHelp && <p className={styles.copy}>{help.message}</p>}
      {pollingPaused && (
        <>
          <p className={styles.copy}>
            Automatic status checks are paused. Check this job before starting
            another sync.
          </p>
          <button type="button" className={styles.secondary} onClick={onCheck}>
            Check sync status
          </button>
        </>
      )}
      {showHelp && help.reconnect && (
        <button
          type="button"
          className={styles.secondary}
          disabled={reconnectDisabled}
          onClick={onReconnect}
        >
          Reconnect read access
        </button>
      )}
    </div>
  );
}

function SourceSync({
  connectionId,
  source,
  disabled,
  reconnectDisabled,
  onComplete,
  onReconnect,
}: {
  connectionId: string;
  source: SyncSource;
  disabled: boolean;
  reconnectDisabled: boolean;
  onComplete: () => Promise<void>;
  onReconnect: () => void;
}) {
  const [progress, setProgress] = useState<SyncProgress | null>(null);
  const [starting, setStarting] = useState(false);
  const [pollingPaused, setPollingPaused] = useState(false);
  const [monitorRequest, setMonitorRequest] = useState<{
    workflowId: string;
  } | null>(null);
  const [error, setError] = useState("");
  const mounted = useRef(false);
  const startingRequest = useRef(false);
  const requestId = useRef<string | null>(null);
  const onCompleteRef = useRef(onComplete);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);
  useEffect(() => {
    if (!monitorRequest) return;
    setPollingPaused(false);
    return monitorSync({
      workflowId: monitorRequest.workflowId,
      read: (id, signal) =>
        request(
          `/intelligence/sync/status?workflow_id=${encodeURIComponent(id)}`,
          undefined,
          signal,
        ),
      onProgress: setProgress,
      onPause: () => setPollingPaused(true),
      onTerminal: (result) => {
        if (result.status === "completed") {
          void onCompleteRef.current().catch(() => {
            if (mounted.current) {
              setError(
                "Sync finished. Use Refresh Today to reload your workspace.",
              );
            }
          });
        }
      },
    });
  }, [monitorRequest]);

  async function sync() {
    if (
      disabled ||
      startingRequest.current ||
      (progress && !isSyncTerminal(progress.status))
    ) {
      return;
    }
    startingRequest.current = true;
    setStarting(true);
    setError("");
    requestId.current ??= crypto.randomUUID();
    try {
      const result = await request<{ workflow_id: string }>(
        "/intelligence/sync",
        {
          connection_id: connectionId,
          source,
          request_id: requestId.current,
        },
      );
      if (typeof result.workflow_id !== "string" || !result.workflow_id) {
        throw new Error("Could not confirm this sync. Please try again.");
      }
      requestId.current = null;
      if (mounted.current) {
        setProgress({
          workflow_id: result.workflow_id,
          status: "queued",
          commitment_count: null,
          error: null,
        });
        setMonitorRequest({ workflowId: result.workflow_id });
      }
    } catch (cause) {
      if (mounted.current) {
        setError(
          cause instanceof Error ? cause.message : "Could not start sync.",
        );
      }
    } finally {
      startingRequest.current = false;
      if (mounted.current) setStarting(false);
    }
  }

  return (
    <div className={styles.syncSource}>
      <button
        type="button"
        className={styles.secondary}
        disabled={
          disabled ||
          starting ||
          (progress !== null && !isSyncTerminal(progress.status))
        }
        onClick={() => void sync()}
      >
        {starting ? "Starting " : "Sync "}
        {source === "gmail" ? "Gmail" : "Calendar"}
      </button>
      {progress && (
        <SyncReadout
          source={source}
          progress={progress}
          pollingPaused={pollingPaused}
          reconnectDisabled={reconnectDisabled}
          onCheck={() =>
            setMonitorRequest({ workflowId: progress.workflow_id })
          }
          onReconnect={onReconnect}
        />
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function IntelligenceControls({
  connections,
  paused,
  onRefresh,
}: {
  connections: Connection[];
  paused: boolean;
  onRefresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState<IntelligenceStatus | null>(null);
  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await request<IntelligenceStatus>("/intelligence/status"));
    } catch {
      /* Source controls remain usable when summary is unavailable. */
    }
  }, []);
  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  async function enable(connectionId: string) {
    setBusy(connectionId);
    setError("");
    try {
      const result = await request<{ authorization_url: string }>(
        `/connections/google/${connectionId}/intelligence/start`,
        {},
      );
      const target = new URL(result.authorization_url);
      if (
        target.protocol !== "https:" ||
        target.hostname !== "accounts.google.com"
      ) {
        throw new Error(
          "Google authorization could not be opened. Please try again.",
        );
      }
      window.location.assign(target.href);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not open Google authorization.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function refresh() {
    setBusy("refresh");
    setError("");
    try {
      await Promise.all([refreshStatus(), onRefresh()]);
    } catch {
      setError("Today could not refresh. Please try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className={styles.card} aria-labelledby="understanding-heading">
      <div className={styles.heading}>
        <span>Connected understanding</span>
        <span className={styles.badge}>You control access</span>
      </div>
      <h2 id="understanding-heading">Bring your work into focus</h2>
      <p className={styles.copy}>
        Let NavoX find requests, deadlines, meetings, and follow-ups in the
        Google sources you authorize. Review the source behind every suggestion.
      </p>
      {connections.length === 0 ? (
        <p className={styles.copy}>
          Connect your Google account below to choose what NavoX can read.
        </p>
      ) : (
        connections.map((connection) => {
          const gmail = connection.granted_scopes.includes(
            "https://www.googleapis.com/auth/gmail.readonly",
          );
          const calendar = connection.granted_scopes.some((scope) =>
            [
              "https://www.googleapis.com/auth/calendar.events.readonly",
              "https://www.googleapis.com/auth/calendar.readonly",
            ].includes(scope),
          );
          const canSync =
            connection.status === "active" &&
            status?.processing_ready !== false;
          return (
            <div className={styles.connection} key={connection.id}>
              <strong>{connection.external_email ?? "Google account"}</strong>
              <div className={styles.sourceState}>
                <span>
                  Gmail · {gmail ? "Read access granted" : "Not enabled"}
                </span>
                <span>
                  Calendar · {calendar ? "Read access granted" : "Not enabled"}
                </span>
              </div>
              {connection.status !== "active" && (
                <p className={styles.copy}>
                  Check this Google connection below before syncing. Its current
                  status is {connection.status.replaceAll("_", " ")}.
                </p>
              )}
              {(!gmail ||
                !calendar ||
                connection.status === "reauthorization_required") && (
                <>
                  <p className={styles.copy}>
                    This grants read access to email and calendar content for
                    operational understanding. Relevant content is processed by
                    your configured AI service. Email sending stays a separate
                    permission.
                  </p>
                  <button
                    type="button"
                    className={styles.primary}
                    disabled={busy !== null || paused}
                    onClick={() => void enable(connection.id)}
                  >
                    {busy === connection.id
                      ? "Opening Google…"
                      : connection.status === "reauthorization_required"
                        ? "Reconnect read access"
                        : "Enable Gmail & Calendar understanding"}
                  </button>
                </>
              )}
              <div className={styles.syncSources}>
                {gmail && (
                  <SourceSync
                    key={`${connection.id}:gmail`}
                    connectionId={connection.id}
                    source="gmail"
                    disabled={busy !== null || paused || !canSync}
                    reconnectDisabled={busy !== null || paused}
                    onComplete={async () => {
                      await Promise.all([refreshStatus(), onRefresh()]);
                    }}
                    onReconnect={() => void enable(connection.id)}
                  />
                )}
                {calendar && (
                  <SourceSync
                    key={`${connection.id}:calendar`}
                    connectionId={connection.id}
                    source="calendar"
                    disabled={busy !== null || paused || !canSync}
                    reconnectDisabled={busy !== null || paused}
                    onComplete={async () => {
                      await Promise.all([refreshStatus(), onRefresh()]);
                    }}
                    onReconnect={() => void enable(connection.id)}
                  />
                )}
              </div>
            </div>
          );
        })
      )}
      {paused && (
        <p className={styles.copy}>
          Understanding is paused with your agent. Resume it to process new
          sources.
        </p>
      )}
      {status && (
        <p className={styles.stats}>
          {status.active_commitments} active commitments ·{" "}
          {status.evidence_count} evidence references
        </p>
      )}
      {status?.processing_ready === false && (
        <p className={styles.copy}>
          {status.readiness_message ??
            "Source processing is not configured yet. Your saved commitments remain available."}
        </p>
      )}
      <button
        className={styles.secondary}
        disabled={busy !== null}
        onClick={() => void refresh()}
        type="button"
      >
        {busy === "refresh" ? "Refreshing…" : "Refresh Today"}
      </button>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

const feedbackOptions = [
  ["useful", "Useful"],
  ["not_useful", "Not useful"],
  ["incorrect", "Incorrect"],
  ["too_frequent", "Too frequent"],
  ["more_like_this", "More like this"],
  ["less_like_this", "Less like this"],
] as const;
type FeedbackType = (typeof feedbackOptions)[number][0];

export function IntelligenceFeedback({
  commitmentId,
  onRefresh,
}: {
  commitmentId: string;
  onRefresh: () => Promise<void>;
}) {
  const requests = useRef(new Map<FeedbackType, string>());
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<FeedbackType | null>(null);
  const [error, setError] = useState("");
  async function submit(feedbackType: FeedbackType) {
    setBusy(true);
    setError("");
    let requestId = requests.current.get(feedbackType);
    if (!requestId) {
      requestId = crypto.randomUUID();
      requests.current.set(feedbackType, requestId);
    }
    try {
      await request("/intelligence/feedback", {
        request_id: requestId,
        target_id: commitmentId,
        target_type: "commitment",
        feedback_type: feedbackType,
      });
      setSent(feedbackType);
      await onRefresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Feedback could not be saved.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className={styles.feedback}>
      <summary>Was this helpful?</summary>
      <p>
        Help NavoX improve what it surfaces. Your feedback never changes
        permissions.
      </p>
      <div className={styles.actions}>
        {feedbackOptions.map(([value, label]) => (
          <button
            key={value}
            type="button"
            disabled={busy || sent === value}
            aria-pressed={sent === value}
            onClick={() => void submit(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {sent && <p role="status">Feedback saved. Thank you.</p>}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </details>
  );
}

interface Preferences {
  timezone: string;
  clock_format: "12h" | "24h";
  temperature_unit: "celsius" | "fahrenheit";
  weather_visible: boolean;
  weather_city: string | null;
}
interface Weather {
  status: string;
  temperature: number | null;
  unit: "celsius" | "fahrenheit";
  description: string | null;
  city: string | null;
  observed_at: string | null;
}

const timezoneGroups = [
  {
    label: "North America",
    options: [
      ["America/New_York", "Eastern Time — New York (EST/EDT)"],
      ["America/Chicago", "Central Time — Chicago (CST/CDT)"],
      ["America/Denver", "Mountain Time — Denver (MST/MDT)"],
      ["America/Phoenix", "Arizona — Phoenix (MST)"],
      ["America/Los_Angeles", "Pacific Time — Los Angeles (PST/PDT)"],
      ["America/Anchorage", "Alaska Time — Anchorage (AKST/AKDT)"],
      ["Pacific/Honolulu", "Hawaii Time — Honolulu (HST)"],
      ["America/Toronto", "Canada Eastern — Toronto (EST/EDT)"],
      ["America/Mexico_City", "Mexico City"],
    ],
  },
  {
    label: "UTC, Europe & Africa",
    options: [
      ["UTC", "Coordinated Universal Time (UTC)"],
      ["Europe/London", "United Kingdom — London (GMT/BST)"],
      ["Europe/Paris", "Central Europe — Paris (CET/CEST)"],
      ["Europe/Athens", "Eastern Europe — Athens (EET/EEST)"],
      ["Africa/Accra", "Ghana — Accra (GMT)"],
      ["Africa/Lagos", "West Africa — Lagos (WAT)"],
      ["Africa/Johannesburg", "South Africa — Johannesburg (SAST)"],
    ],
  },
  {
    label: "Asia & Pacific",
    options: [
      ["Asia/Dubai", "United Arab Emirates — Dubai (GST)"],
      ["Asia/Kolkata", "India — Kolkata (IST)"],
      ["Asia/Singapore", "Singapore (SGT)"],
      ["Asia/Shanghai", "China — Shanghai (CST)"],
      ["Asia/Tokyo", "Japan — Tokyo (JST)"],
      ["Asia/Seoul", "South Korea — Seoul (KST)"],
      ["Australia/Sydney", "Australia — Sydney (AEST/AEDT)"],
      ["Pacific/Auckland", "New Zealand — Auckland (NZST/NZDT)"],
    ],
  },
] as const;

const knownTimezones: ReadonlySet<string> = new Set<string>(
  timezoneGroups.flatMap((group) => group.options.map(([value]) => value)),
);

function timezoneAbbreviation(timezone: string, now: Date | null): string {
  if (!now) return timezone;
  try {
    return (
      new Intl.DateTimeFormat("en-US", {
        timeZone: timezone,
        timeZoneName: "short",
      })
        .formatToParts(now)
        .find((part) => part.type === "timeZoneName")?.value ?? timezone
    );
  } catch {
    return timezone;
  }
}

export function WorkspaceReadout({
  now,
  timezone,
  preferences,
  weather,
}: {
  now: Date | null;
  timezone: string;
  preferences: Preferences | null;
  weather: Weather | null;
}) {
  const clockOptions = {
    timeZone: timezone,
    hour: "numeric",
    minute: "2-digit",
    hourCycle: preferences?.clock_format === "24h" ? "h23" : "h12",
  } as const;

  return (
    <>
      <div className={styles.contextHeading}>
        <span className={styles.contextLabel}>Today</span>
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <rect x="3" y="5" width="18" height="16" rx="3" />
          <path d="M7 3v4m10-4v4M3 11h18m-14 4h3m4 0h3" />
        </svg>
      </div>
      <p className={styles.contextDate}>
        {now
          ? new Intl.DateTimeFormat("en-US", {
              timeZone: timezone,
              weekday: "long",
              month: "long",
              day: "numeric",
            }).format(now)
          : "Your day at a glance"}
      </p>
      <div className={styles.timeReadout}>
        <span className={styles.clockLabel}>Your local time</span>
        <time dateTime={now?.toISOString()}>
          {now
            ? new Intl.DateTimeFormat("en-US", {
                ...clockOptions,
                second: "2-digit",
              }).format(now)
            : "Loading local time…"}
        </time>
        <span className={styles.timezone}>
          {timezoneAbbreviation(timezone, now)} ·{" "}
          {timezone.replaceAll("_", " ")}
        </span>
      </div>
      {preferences?.weather_visible && (
        <div className={styles.weather}>
          <div className={styles.weatherMain}>
            <span className={styles.weatherIcon} aria-hidden="true">
              <svg
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="M9 14.5V5a3 3 0 0 1 6 0v9.5a5 5 0 1 1-6 0Z" />
                <path d="M12 8v9m6-12h2m-2 4h2" />
                <circle cx="12" cy="18" r="1" />
              </svg>
            </span>
            <div className={styles.weatherReading}>
              <strong>
                {weather === null
                  ? "Loading weather…"
                  : weather.status === "ready" && weather.temperature !== null
                    ? `${Math.round(weather.temperature)}°${weather.unit === "fahrenheit" ? "F" : "C"}`
                    : "Weather unavailable"}
              </strong>
              {weather?.description && <span>{weather.description}</span>}
            </div>
          </div>
          <span className={styles.weatherLocation}>
            {weather?.city ?? preferences.weather_city ?? "Weather"}
          </span>
          {weather?.status === "ready" && (
            <div className={styles.weatherMeta}>
              {weather.observed_at && (
                <small>
                  As of{" "}
                  {new Intl.DateTimeFormat("en-US", clockOptions).format(
                    new Date(weather.observed_at),
                  )}
                </small>
              )}
              <a
                href="https://open-meteo.com/"
                target="_blank"
                rel="noreferrer"
              >
                Weather by Open-Meteo
              </a>
            </div>
          )}
        </div>
      )}
    </>
  );
}

export function WorkspaceContext({
  timezone,
  onTimezoneChange,
}: {
  timezone: string;
  onTimezoneChange: (value: string) => void;
}) {
  const [now, setNow] = useState<Date | null>(null);
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [draft, setDraft] = useState<Preferences | null>(null);
  const [weather, setWeather] = useState<Weather | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    setNow(new Date());
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    void request<Preferences>("/workspace/preferences")
      .then((value) => {
        if (!active) return;
        setPreferences(value);
        setDraft(value);
        onTimezoneChange(value.timezone);
      })
      .catch(() => {
        if (active)
          setError(
            "Display settings could not be loaded. Refresh to try again.",
          );
      });
    return () => {
      active = false;
    };
  }, [onTimezoneChange]);
  useEffect(() => {
    if (!preferences?.weather_visible || !preferences.weather_city) {
      setWeather(null);
      return;
    }
    let active = true;
    const load = () => {
      setWeather(null);
      void request<Weather>("/workspace/weather")
        .then((value) => {
          if (active) setWeather(value);
        })
        .catch(() => {
          if (active) {
            setWeather({
              status: "unavailable",
              temperature: null,
              unit: preferences.temperature_unit,
              description: "Weather lookup could not complete.",
              city: preferences.weather_city,
              observed_at: null,
            });
          }
        });
    };
    load();
    const timer = window.setInterval(load, 15 * 60 * 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [preferences]);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      new Intl.DateTimeFormat(undefined, { timeZone: draft.timezone });
      const saved = await request<Preferences>("/workspace/preferences", {
        ...draft,
        weather_city: draft.weather_city?.trim() || null,
      });
      setPreferences(saved);
      setDraft(saved);
      onTimezoneChange(saved.timezone);
      setMessage("Display preferences saved.");
    } catch (cause) {
      setError(
        cause instanceof RangeError
          ? "Enter a valid timezone, such as America/New_York."
          : cause instanceof Error
            ? cause.message
            : "Preferences could not be saved.",
      );
    } finally {
      setSaving(false);
    }
  }
  return (
    <section className={styles.context} aria-label="Local time and weather">
      <WorkspaceReadout
        now={now}
        timezone={timezone}
        preferences={preferences}
        weather={weather}
      />
      <details className={styles.settings}>
        <summary>Time & weather preferences</summary>
        {draft && (
          <form onSubmit={save} className={styles.preferences}>
            <label>
              Timezone
              <select
                required
                value={draft.timezone}
                onChange={(event) =>
                  setDraft({ ...draft, timezone: event.target.value })
                }
              >
                {!knownTimezones.has(draft.timezone) && (
                  <option value={draft.timezone}>
                    Saved timezone — {draft.timezone}
                  </option>
                )}
                {timezoneGroups.map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {group.options.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              <small>
                Choose your region. Eastern Time automatically changes between
                EST and EDT.
              </small>
            </label>
            <div className={styles.formRow}>
              <label>
                Clock
                <select
                  value={draft.clock_format}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      clock_format: event.target
                        .value as Preferences["clock_format"],
                    })
                  }
                >
                  <option value="12h">12 hour</option>
                  <option value="24h">24 hour</option>
                </select>
              </label>
              <label>
                Temperature
                <select
                  value={draft.temperature_unit}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      temperature_unit: event.target
                        .value as Preferences["temperature_unit"],
                    })
                  }
                >
                  <option value="celsius">Celsius · °C</option>
                  <option value="fahrenheit">Fahrenheit · °F</option>
                </select>
              </label>
            </div>
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={draft.weather_visible}
                onChange={(event) =>
                  setDraft({ ...draft, weather_visible: event.target.checked })
                }
              />
              Show current weather
            </label>
            {draft.weather_visible && (
              <label>
                City
                <input
                  required
                  maxLength={100}
                  value={draft.weather_city ?? ""}
                  placeholder="Ithaca, New York"
                  onChange={(event) =>
                    setDraft({ ...draft, weather_city: event.target.value })
                  }
                />
                <small>
                  Your city is used to look up weather. No precise location
                  tracking.
                </small>
              </label>
            )}
            <button className={styles.primary} type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save preferences"}
            </button>
          </form>
        )}
        {message && (
          <p className={styles.message} role="status">
            {message}
          </p>
        )}
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
      </details>
    </section>
  );
}
