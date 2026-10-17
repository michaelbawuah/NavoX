"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import type { EmailConnection } from "../lib/email-workflow";
import {
  isActiveThreadWatch,
  type ReminderChoice,
  reminderAt,
  safeThreadProviderUrl,
  sortedThreadWatches,
  type ThreadWatch,
  threadWatchReplyTarget,
  threadWatchStatusLabel,
  threadWatchTime,
  type WatchableSendAction,
  watchEligibility,
} from "../lib/thread-watch";
import styles from "./thread-watch-panel.module.css";

const api =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const changedEvent = "navox:thread-watches-changed";

async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`${api}/thread-watches${path}`, {
    method,
    credentials: "include",
    cache: "no-store",
    signal: AbortSignal.timeout(method === "GET" ? 10_000 : 65_000),
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok)
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : typeof data?.detail?.message === "string"
          ? data.detail.message
          : "NavoX could not update this email watch. Try refreshing its status.",
    );
  return data as T;
}

function announceChange() {
  window.dispatchEvent(new Event(changedEvent));
}

function ReminderFields({
  choice,
  custom,
  disabled,
  onChoice,
  onCustom,
}: {
  choice: ReminderChoice;
  custom: string;
  disabled: boolean;
  onChoice: (value: ReminderChoice) => void;
  onCustom: (value: string) => void;
}) {
  const id = useId();
  const [localZone, setLocalZone] = useState("");
  useEffect(() => {
    setLocalZone(new Intl.DateTimeFormat().resolvedOptions().timeZone);
  }, []);
  return (
    <div className={styles.reminderFields}>
      <label htmlFor={`${id}-delay`}>
        If there is no reply, remind me
        <select
          id={`${id}-delay`}
          value={choice}
          disabled={disabled}
          onChange={(event) => onChoice(event.target.value as ReminderChoice)}
        >
          <option value="1">In 1 day</option>
          <option value="2">In 2 days</option>
          <option value="7">In 7 days</option>
          <option value="custom">At a date and time</option>
        </select>
      </label>
      {choice === "custom" && (
        <label htmlFor={`${id}-time`}>
          Reminder date and time
          <input
            id={`${id}-time`}
            type="datetime-local"
            value={custom}
            disabled={disabled}
            required
            onChange={(event) => onCustom(event.target.value)}
          />
          <small>
            Uses this device’s local time zone
            {localZone ? ` (${localZone})` : ""}. Choose a time within 30 days.
          </small>
        </label>
      )}
    </div>
  );
}

export function ThreadWatchCard({
  watch,
  paused = false,
  busy = false,
  eligibleCommitmentIds = [],
  onDraftReply,
  onAction,
  onReschedule,
}: {
  watch: ThreadWatch;
  paused?: boolean;
  busy?: boolean;
  eligibleCommitmentIds?: string[];
  onDraftReply?: (commitmentId: string) => void;
  onAction?: (
    id: string,
    action: "pause" | "resume" | "cancel" | "check",
  ) => void;
  onReschedule?: (id: string, remindAt: string) => Promise<void>;
}) {
  const [choice, setChoice] = useState<ReminderChoice>("2");
  const [custom, setCustom] = useState("");
  const [dateError, setDateError] = useState("");
  const providerUrl = safeThreadProviderUrl(watch.provider_url);
  const replyId = threadWatchReplyTarget(watch, eligibleCommitmentIds);
  const active = isActiveThreadWatch(watch);
  const canCheck = active && watch.status !== "paused";
  const needsHelp = ["check_failed", "needs_access"].includes(watch.status);

  async function reschedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setDateError("");
    try {
      const time = reminderAt(choice, custom, Date.now());
      await onReschedule?.(watch.id, time);
    } catch (error) {
      setDateError(
        error instanceof Error
          ? error.message
          : "Choose a valid reminder time.",
      );
    }
  }

  return (
    <article className={styles.watch} data-status={watch.status}>
      <div className={styles.watchHeading}>
        <span className={styles.status}>
          {threadWatchStatusLabel(watch.status)}
        </span>
        <span className={styles.recipient}>To {watch.recipient_email}</span>
      </div>
      <h3>{watch.subject || "Sent email"}</h3>
      <p className={styles.detail}>From {watch.sender_email}</p>
      <p className={styles.detail}>
        Reminder:{" "}
        <time dateTime={watch.remind_at}>
          {threadWatchTime(watch.remind_at)}
        </time>
      </p>
      {watch.last_checked_at ? (
        <p className={styles.detail}>
          Last checked{" "}
          <time dateTime={watch.last_checked_at}>
            {threadWatchTime(watch.last_checked_at)}
          </time>
        </p>
      ) : (
        <p className={styles.detail}>
          Not checked yet. NavoX has not confirmed whether a reply arrived.
        </p>
      )}
      {watch.status === "due" && (
        <p className={styles.note}>
          No reply was found when NavoX last checked this thread. Review Gmail
          before following up.
        </p>
      )}
      {watch.status === "replied" && watch.replied_at && (
        <p className={styles.note}>
          Reply detected{" "}
          <time dateTime={watch.replied_at}>
            {threadWatchTime(watch.replied_at)}
          </time>
          . This watch has stopped.
        </p>
      )}
      {watch.status === "paused" && (
        <p className={styles.note}>
          Checks are paused until you resume this watch.
        </p>
      )}
      {watch.status === "needs_access" && (
        <p className={styles.note}>
          Restore Gmail read access in <a href="/connections">Connections</a>,
          then check again. Reply status is unknown.
        </p>
      )}
      {watch.status === "check_failed" && (
        <p className={styles.note}>
          {watch.reason === "dispatch_failed"
            ? "The watch is saved, but its background check could not start. Try Check now."
            : "The latest check did not complete. Reply status is unknown; try Check now or review Gmail."}
        </p>
      )}
      <div className={styles.actions}>
        {providerUrl && (
          <a href={providerUrl} target="_blank" rel="noopener noreferrer">
            Open Gmail thread
          </a>
        )}
        {replyId && onDraftReply && (
          <button
            type="button"
            disabled={busy || paused}
            onClick={() => onDraftReply(replyId)}
          >
            Draft a reply
          </button>
        )}
        {onAction && canCheck && (
          <button
            type="button"
            disabled={busy || paused}
            onClick={() => onAction(watch.id, "check")}
          >
            {busy ? "Updating…" : needsHelp ? "Retry check" : "Check now"}
          </button>
        )}
        {onAction && active && (
          <>
            <button
              type="button"
              disabled={busy || (paused && watch.status === "paused")}
              onClick={() =>
                onAction(
                  watch.id,
                  watch.status === "paused" ? "resume" : "pause",
                )
              }
            >
              {watch.status === "paused" ? "Resume watch" : "Pause watch"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onAction(watch.id, "cancel")}
            >
              Cancel watch
            </button>
          </>
        )}
      </div>
      {active && onReschedule && (
        <details className={styles.reschedule}>
          <summary>Change reminder</summary>
          <form onSubmit={(event) => void reschedule(event)}>
            <ReminderFields
              choice={choice}
              custom={custom}
              disabled={busy || paused}
              onChoice={setChoice}
              onCustom={setCustom}
            />
            <button type="submit" disabled={busy || paused}>
              Save reminder
            </button>
            {dateError && (
              <p className={styles.notice} role="alert">
                {dateError}
              </p>
            )}
          </form>
        </details>
      )}
    </article>
  );
}

export function ThreadWatchPanel({
  paused = false,
  eligibleCommitmentIds = [],
  onDraftReply,
  compact = false,
  refreshKey,
}: {
  paused?: boolean;
  eligibleCommitmentIds?: string[];
  onDraftReply?: (commitmentId: string) => void;
  compact?: boolean;
  refreshKey?: string | number;
}) {
  const [items, setItems] = useState<ThreadWatch[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busyId, setBusyId] = useState("");
  const requestId = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++requestId.current;
    setLoading(true);
    try {
      const data = await request<{ items: ThreadWatch[] }>("");
      if (current !== requestId.current) return;
      setItems(data.items);
      setLoaded(true);
      setError("");
    } catch (error) {
      if (current === requestId.current)
        setError(
          error instanceof Error
            ? error.message
            : "Email watches could not be refreshed.",
        );
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    // A key change only reloads saved watch state, never checks Gmail.
    void refreshKey;
    void refresh();
    const listener = () => void refresh();
    window.addEventListener(changedEvent, listener);
    return () => {
      requestId.current++;
      window.removeEventListener(changedEvent, listener);
    };
  }, [refresh, refreshKey]);

  async function mutate(
    id: string,
    action: "pause" | "resume" | "cancel" | "check",
  ) {
    requestId.current++;
    setLoading(false);
    setBusyId(id);
    setError("");
    setMessage("");
    try {
      const updated = await request<ThreadWatch>(
        `/${encodeURIComponent(id)}/${action}`,
        "POST",
      );
      setItems((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setMessage(
        action === "check"
          ? "Check requested. Refresh saved status to see the result."
          : action === "cancel"
            ? "Email watch cancelled."
            : action === "pause"
              ? "Email watch paused."
              : "Email watch resumed.",
      );
      announceChange();
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "The email watch could not be updated.",
      );
    } finally {
      setBusyId("");
    }
  }

  async function reschedule(id: string, remindAt: string) {
    requestId.current++;
    setLoading(false);
    setBusyId(id);
    setError("");
    setMessage("");
    try {
      const updated = await request<ThreadWatch>(
        `/${encodeURIComponent(id)}`,
        "PATCH",
        { remind_at: remindAt },
      );
      setItems((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setMessage("Reminder updated.");
      announceChange();
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "The reminder could not be updated.",
      );
    } finally {
      setBusyId("");
    }
  }

  const ordered = sortedThreadWatches(items);
  const active = ordered.filter(isActiveThreadWatch);
  const history = ordered.filter((item) => !isActiveThreadWatch(item));
  const visible = compact
    ? active.filter((item) => item.status === "due").slice(0, 3)
    : active;
  if (compact && loaded && !loading && !error && visible.length === 0)
    return null;

  return (
    <section
      className={styles.panel}
      aria-label={compact ? "Email follow-up reminders" : "Email reply watches"}
      aria-busy={loading}
    >
      <div className={styles.heading}>
        <div>
          <p className={styles.kicker}>Follow through</p>
          <h2>{compact ? "Email follow-ups" : "Waiting for a reply"}</h2>
        </div>
        <button
          type="button"
          disabled={loading || !!busyId}
          onClick={() => void refresh()}
        >
          {loading ? "Loading…" : "Refresh status"}
        </button>
      </div>
      {!compact && (
        <p className={styles.intro}>
          Choose “Watch for a reply” after sending a verified email. NavoX
          checks only the threads you choose and keeps reminders here. It will
          not send a follow-up for you.
        </p>
      )}
      {paused && (
        <p className={styles.notice}>
          NavoX is paused. Resume it before starting or checking a watch. You
          can still pause or cancel watches.
        </p>
      )}
      {error && (
        <p className={styles.notice} role="alert">
          {loaded
            ? "Saved watches are shown below; they may be out of date. "
            : "Email watches could not be loaded. "}
          {error}
        </p>
      )}
      {message && (
        <p className={styles.note} role="status">
          {message}
        </p>
      )}
      {loading && !loaded && (
        <p className={styles.detail} role="status">
          Loading your saved email watches…
        </p>
      )}
      {!loading && loaded && !error && visible.length === 0 && !compact && (
        <p className={styles.empty}>
          No active email watches. Send and verify an email, then choose when
          you want a reminder if nobody replies.
        </p>
      )}
      <div className={styles.items}>
        {visible.map((watch) => (
          <ThreadWatchCard
            key={watch.id}
            watch={watch}
            paused={paused}
            busy={!!busyId}
            eligibleCommitmentIds={eligibleCommitmentIds}
            onDraftReply={onDraftReply}
            onAction={(id, action) => void mutate(id, action)}
            onReschedule={reschedule}
          />
        ))}
      </div>
      {compact && (
        <a className={styles.allWatches} href="/inbox#thread-watches">
          Review all email watches
        </a>
      )}
      {!compact && history.length > 0 && (
        <details className={styles.history}>
          <summary>Finished watches ({history.length})</summary>
          <div className={styles.items}>
            {history.map((watch) => (
              <ThreadWatchCard key={watch.id} watch={watch} />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

export function WatchSentEmail({
  action,
  connections,
  paused = false,
}: {
  action: WatchableSendAction;
  connections: EmailConnection[];
  paused?: boolean;
}) {
  if (watchEligibility(action, connections) === "unverified") return null;
  return (
    <WatchSentEmailControl
      key={action.id}
      action={action}
      connections={connections}
      paused={paused}
    />
  );
}

function WatchSentEmailControl({
  action,
  connections,
  paused,
}: {
  action: WatchableSendAction;
  connections: EmailConnection[];
  paused: boolean;
}) {
  const [existing, setExisting] = useState<ThreadWatch | null>(null);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [choice, setChoice] = useState<ReminderChoice>("2");
  const [custom, setCustom] = useState("");
  const pending = useRef<{
    selection: string;
    payload: { action_id: string; request_id: string; remind_at: string };
  } | null>(null);
  const requestId = useRef(0);
  const eligibility = watchEligibility(action, connections);
  const refresh = useCallback(async () => {
    const current = ++requestId.current;
    setLoading(true);
    try {
      const data = await request<{ items: ThreadWatch[] }>("");
      if (current !== requestId.current) return;
      setExisting(
        data.items.find((item) => item.action_id === action.id) ?? null,
      );
      setLoaded(true);
      setError("");
    } catch (error) {
      if (current === requestId.current)
        setError(
          error instanceof Error
            ? error.message
            : "The watch status could not be loaded.",
        );
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, [action.id]);
  useEffect(() => {
    void refresh();
    const listener = () => void refresh();
    window.addEventListener(changedEvent, listener);
    return () => {
      requestId.current++;
      window.removeEventListener(changedEvent, listener);
    };
  }, [refresh]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (eligibility !== "eligible" || paused || !loaded || existing) return;
    try {
      const selection = JSON.stringify([action.id, choice, custom]);
      if (!pending.current || pending.current.selection !== selection) {
        pending.current = {
          selection,
          payload: {
            action_id: action.id,
            request_id: crypto.randomUUID(),
            remind_at: reminderAt(choice, custom, Date.now()),
          },
        };
      }
      requestId.current++;
      setLoading(false);
      setBusy(true);
      const saved = await request<ThreadWatch>(
        "",
        "POST",
        pending.current.payload,
      );
      setExisting(saved);
      announceChange();
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "The watch could not be saved. Refresh its status before trying again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.sentWatch}>
      <h3>Keep track of the reply</h3>
      {existing ? (
        <p role="status">
          {threadWatchStatusLabel(existing.status)}.{" "}
          <a href="/inbox#thread-watches">
            Review this email watch in Follow-ups
          </a>
          .
        </p>
      ) : (
        <>
          <p>
            NavoX can remind you here if the recipient has not replied. No
            follow-up is sent automatically.
          </p>
          {eligibility === "missing_thread" && (
            <p>
              This sent message has no verified Gmail thread available to watch.
              Review replies in Gmail.
            </p>
          )}
          {eligibility === "needs_access" && (
            <p>
              Enable Gmail read access for this sending account in{" "}
              <a href="/connections">Connections</a> before watching for
              replies.
            </p>
          )}
          {eligibility === "eligible" && (
            <form onSubmit={(event) => void create(event)}>
              <ReminderFields
                choice={choice}
                custom={custom}
                disabled={busy || paused}
                onChoice={setChoice}
                onCustom={setCustom}
              />
              <button
                type="submit"
                disabled={busy || paused || loading || !loaded}
              >
                {busy ? "Saving watch…" : "Watch for a reply"}
              </button>
            </form>
          )}
        </>
      )}
      {paused && (
        <p className={styles.notice}>Resume NavoX before creating a watch.</p>
      )}
      {loading && <p role="status">Checking saved watch status…</p>}
      {error && (
        <p className={styles.notice} role="alert">
          {existing ? "Saved watch status may be out of date. " : ""}
          {error}
        </p>
      )}
      {error && (
        <button
          type="button"
          disabled={busy || loading}
          onClick={() => void refresh()}
        >
          Refresh watch status
        </button>
      )}
    </div>
  );
}
