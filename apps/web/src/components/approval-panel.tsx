"use client";

import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import styles from "./today-workspace.module.css";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const gmailSendScope = "https://www.googleapis.com/auth/gmail.send";

interface CommitmentOption {
  id: string;
  title: string;
  status: string;
}

interface GoogleConnection {
  id: string;
  provider: "google";
  status: string;
  granted_scopes: string[];
  last_checked_at: string | null;
  last_error: string | null;
}

interface ApprovalRecord {
  id: string;
  version: number;
  status: string;
  action_payload_hash: string;
  expires_at: string;
  approved_at: string | null;
  rejected_at: string | null;
  consumed_at: string | null;
  superseded_at: string | null;
}

interface ExternalAction {
  id: string;
  commitment_id: string | null;
  plan_id: string;
  provider: string;
  action_type: string;
  risk_level: string;
  requires_approval: boolean;
  status: string;
  payload: {
    connection_id?: string;
    sender?: string;
    to?: string;
    subject?: string;
    body_text?: string;
    post_send_state?: string;
  };
  payload_hash: string;
  result: {
    message_id?: string;
    thread_id?: string | null;
    verification?: string;
  };
  policy_reason: string | null;
  created_at: string;
  started_at: string | null;
  executed_at: string | null;
  verified_at: string | null;
  approval: ApprovalRecord | null;
  workflow_status: string | null;
}

interface ApprovalPanelProps {
  agentPaused: boolean;
  commitments: CommitmentOption[];
  connections: GoogleConnection[];
  onStateChanged: () => Promise<void>;
}

async function apiError(response: Response): Promise<string> {
  const body = (await response.json().catch(() => null)) as {
    detail?: string | { message?: string };
  } | null;
  if (typeof body?.detail === "string") {
    return body.detail;
  }
  return body?.detail?.message ?? "NavoX could not complete that request.";
}

function terminal(status: string): boolean {
  return [
    "completed",
    "uncertain",
    "rejected",
    "expired",
    "blocked",
    "failed",
  ].includes(status);
}

function expiryLabel(value: string | undefined): string {
  if (!value) {
    return "No active approval";
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

export function ApprovalPanel({
  agentPaused,
  commitments,
  connections,
  onStateChanged,
}: ApprovalPanelProps) {
  const gmailConnections = useMemo(
    () =>
      connections.filter(
        (connection) =>
          connection.status === "active" &&
          connection.granted_scopes.includes(gmailSendScope),
      ),
    [connections],
  );
  const connectionToUpgrade = connections.find(
    (connection) =>
      connection.status === "active" &&
      !connection.granted_scopes.includes(gmailSendScope),
  );

  const [commitmentId, setCommitmentId] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [recipient, setRecipient] = useState("");
  const [subject, setSubject] = useState("");
  const [bodyText, setBodyText] = useState("");
  const [postSendState, setPostSendState] = useState("waiting");
  const [activeAction, setActiveAction] = useState<ExternalAction | null>(null);
  const [recentActions, setRecentActions] = useState<ExternalAction[]>([]);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!commitmentId && commitments.length > 0) {
      setCommitmentId(commitments[0].id);
    }
  }, [commitmentId, commitments]);

  useEffect(() => {
    if (!connectionId && gmailConnections.length > 0) {
      setConnectionId(gmailConnections[0].id);
    }
  }, [connectionId, gmailConnections]);

  const refreshActions = useCallback(async () => {
    try {
      const response = await fetch(`${apiBaseUrl}/actions?limit=8`, {
        credentials: "include",
      });
      if (!response.ok) {
        return;
      }
      const actions = (await response.json()) as ExternalAction[];
      setRecentActions(
        actions.filter((action) => action.action_type === "gmail.send"),
      );
      setActiveAction((current) => {
        if (current) {
          return actions.find((action) => action.id === current.id) ?? current;
        }
        return actions.find((action) => !terminal(action.status)) ?? null;
      });
    } catch {
      // The approval card remains usable with its last persisted snapshot.
    }
  }, []);

  useEffect(() => {
    void refreshActions();
  }, [refreshActions]);

  useEffect(() => {
    if (!activeAction || terminal(activeAction.status)) {
      return;
    }
    const timer = window.setTimeout(() => {
      void refreshActions();
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [activeAction, refreshActions]);

  async function enableGmail(connectionIdToUpgrade: string) {
    setBusy("grant");
    setError("");
    try {
      const response = await fetch(
        `${apiBaseUrl}/connections/google/${connectionIdToUpgrade}/gmail-send/start`,
        { credentials: "include" },
      );
      if (!response.ok) {
        setError(await apiError(response));
        return;
      }
      const payload = (await response.json()) as { authorization_url: string };
      window.location.assign(payload.authorization_url);
    } catch {
      setError("NavoX could not start the Gmail permission request.");
    } finally {
      setBusy("");
    }
  }

  async function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("prepare");
    setError("");
    try {
      const response = await fetch(
        `${apiBaseUrl}/commitments/${commitmentId}/actions/gmail-send/prepare`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            request_id: crypto.randomUUID(),
            connection_id: connectionId,
            to: recipient,
            subject,
            body_text: bodyText,
            post_send_state: postSendState,
          }),
        },
      );
      if (!response.ok) {
        setError(await apiError(response));
        return;
      }
      const action = (await response.json()) as ExternalAction;
      setActiveAction(action);
      setEditing(false);
      await refreshActions();
    } catch {
      setError("NavoX could not prepare that email.");
    } finally {
      setBusy("");
    }
  }

  async function decide(decision: "approve" | "reject") {
    if (!activeAction) {
      return;
    }
    setBusy(decision);
    setError("");
    try {
      const response = await fetch(
        `${apiBaseUrl}/actions/${activeAction.id}/${decision}`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id: crypto.randomUUID() }),
        },
      );
      if (!response.ok) {
        setError(await apiError(response));
        return;
      }
      setActiveAction((await response.json()) as ExternalAction);
      await onStateChanged();
      await refreshActions();
    } catch {
      setError(`NavoX could not ${decision} that action.`);
    } finally {
      setBusy("");
    }
  }

  function beginEdit() {
    if (!activeAction) {
      return;
    }
    setRecipient(activeAction.payload.to ?? "");
    setSubject(activeAction.payload.subject ?? "");
    setBodyText(activeAction.payload.body_text ?? "");
    setPostSendState(activeAction.payload.post_send_state ?? "waiting");
    setEditing(true);
  }

  async function saveEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeAction) {
      return;
    }
    setBusy("edit");
    setError("");
    try {
      const response = await fetch(
        `${apiBaseUrl}/actions/${activeAction.id}/edit`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            to: recipient,
            subject,
            body_text: bodyText,
            post_send_state: postSendState,
          }),
        },
      );
      if (!response.ok) {
        setError(await apiError(response));
        return;
      }
      setActiveAction((await response.json()) as ExternalAction);
      setEditing(false);
      await refreshActions();
    } catch {
      setError("NavoX could not update the prepared email.");
    } finally {
      setBusy("");
    }
  }

  const approvalPending =
    activeAction?.status === "awaiting_approval" &&
    activeAction.approval?.status === "pending";

  return (
    <section className={`${styles.controlCard} ${styles.approvalCard}`}>
      <div className={styles.controlHeading}>
        <p>External action</p>
        <span className={styles.controlMeta}>Exact approval · R3</span>
      </div>
      <h2>Gmail send</h2>

      {error && (
        <p className={styles.inlineError} role="alert">
          {error}
        </p>
      )}

      {gmailConnections.length === 0 ? (
        <div className={styles.permissionGate}>
          <strong>Gmail sending is off.</strong>
          <p>
            Google identity access does not include email authority. Enable only
            the send scope when you want NavoX to prepare approved email
            actions.
          </p>
          {connectionToUpgrade ? (
            <button
              disabled={busy === "grant"}
              onClick={() => void enableGmail(connectionToUpgrade.id)}
              type="button"
            >
              {busy === "grant" ? "Opening Google…" : "Enable Gmail sending"}
            </button>
          ) : (
            <span>Connect Google first.</span>
          )}
        </div>
      ) : activeAction && !editing ? (
        <div className={styles.approvalPanel} aria-live="polite">
          <div className={styles.approvalTopline}>
            <div>
              <span className={styles.riskBadge}>R3</span>
              <strong>{activeAction.status.replaceAll("_", " ")}</strong>
            </div>
            <small>
              {activeAction.approval?.status === "pending"
                ? `Expires ${expiryLabel(activeAction.approval.expires_at)}`
                : activeAction.approval?.status.replaceAll("_", " ")}
            </small>
          </div>

          <dl className={styles.actionPayload}>
            <div>
              <dt>From</dt>
              <dd>{activeAction.payload.sender}</dd>
            </div>
            <div>
              <dt>To</dt>
              <dd>{activeAction.payload.to}</dd>
            </div>
            <div>
              <dt>Subject</dt>
              <dd>{activeAction.payload.subject}</dd>
            </div>
          </dl>
          <div className={styles.emailBody}>
            <span>Exact body</span>
            <p>{activeAction.payload.body_text}</p>
          </div>
          <div className={styles.approvalHash}>
            <span>Approved payload fingerprint</span>
            <code>{activeAction.payload_hash}</code>
          </div>

          {activeAction.result.message_id && (
            <div className={styles.verificationReceipt}>
              <strong>Verified by Gmail</strong>
              <span>Message {activeAction.result.message_id}</span>
            </div>
          )}
          {activeAction.status === "uncertain" && (
            <p className={styles.manualReview}>
              Gmail&apos;s outcome could not be confirmed. NavoX will not retry
              automatically; review Sent mail before taking another action.
            </p>
          )}

          {approvalPending && (
            <div className={styles.approvalActions}>
              <button
                disabled={busy !== "" || agentPaused}
                onClick={() => void decide("approve")}
                type="button"
              >
                {busy === "approve" ? "Approving…" : "Approve exact send"}
              </button>
              <button disabled={busy !== ""} onClick={beginEdit} type="button">
                Revise
              </button>
              <button
                disabled={busy !== ""}
                onClick={() => void decide("reject")}
                type="button"
              >
                Reject
              </button>
            </div>
          )}
          {terminal(activeAction.status) && (
            <button
              className={styles.secondaryAction}
              onClick={() => setActiveAction(null)}
              type="button"
            >
              Prepare another email
            </button>
          )}
        </div>
      ) : (
        <form
          className={styles.approvalForm}
          onSubmit={editing ? saveEdit : prepare}
        >
          {!editing && (
            <>
              <label>
                Commitment
                <select
                  disabled={commitments.length === 0}
                  onChange={(event) => setCommitmentId(event.target.value)}
                  required
                  value={commitmentId}
                >
                  {commitments.map((commitment) => (
                    <option key={commitment.id} value={commitment.id}>
                      {commitment.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Google connection
                <select
                  onChange={(event) => setConnectionId(event.target.value)}
                  required
                  value={connectionId}
                >
                  {gmailConnections.map((connection) => (
                    <option key={connection.id} value={connection.id}>
                      Gmail-enabled Google account
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          <label>
            Recipient
            <input
              onChange={(event) => setRecipient(event.target.value)}
              required
              type="email"
              value={recipient}
            />
          </label>
          <label>
            Subject
            <input
              maxLength={256}
              onChange={(event) => setSubject(event.target.value)}
              required
              value={subject}
            />
          </label>
          <label>
            Message
            <textarea
              maxLength={50000}
              onChange={(event) => setBodyText(event.target.value)}
              required
              rows={6}
              value={bodyText}
            />
          </label>
          <label>
            After verified send
            <select
              onChange={(event) => setPostSendState(event.target.value)}
              value={postSendState}
            >
              <option value="waiting">Mark commitment waiting</option>
              <option value="completed">Mark commitment complete</option>
              <option value="unchanged">Leave commitment unchanged</option>
            </select>
          </label>
          <div className={styles.approvalActions}>
            <button
              disabled={
                busy !== "" ||
                agentPaused ||
                commitments.length === 0 ||
                !connectionId
              }
              type="submit"
            >
              {busy === "prepare" || busy === "edit"
                ? "Saving exact action…"
                : editing
                  ? "Save revision"
                  : "Prepare for approval"}
            </button>
            {editing && (
              <button
                disabled={busy !== ""}
                onClick={() => setEditing(false)}
                type="button"
              >
                Cancel
              </button>
            )}
          </div>
          {!editing && commitments.length === 0 && (
            <small>
              Add or confirm a commitment before preparing an email.
            </small>
          )}
        </form>
      )}

      {recentActions.length > 1 && (
        <div className={styles.recentActions}>
          <span className={styles.recentActionsLabel}>Recent Gmail actions</span>
          {recentActions.slice(0, 4).map((action) => (
            <button
              key={action.id}
              onClick={() => {
                setActiveAction(action);
                setEditing(false);
              }}
              type="button"
            >
              <b className={styles.recentActionTitle}>
                {action.payload.subject ?? "Prepared email"}
              </b>
              <small className={styles.recentActionStatus}>
                {action.status.replaceAll("_", " ")}
              </small>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
