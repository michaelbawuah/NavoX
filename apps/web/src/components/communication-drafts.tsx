"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type EmailConnection,
  type EmailReplySource,
  eligibleReplyCommitment,
  emailSendOutcome,
  manualEmailApprovalPayload,
  manualEmailReviewMatches,
  mayStartAnotherEmail,
  replyPreparationPayload,
  replyRecipient,
  replySubject,
  selectReplyConnection,
  selectReplySource,
} from "../lib/email-workflow";
import workflowStyles from "./communication-drafts.module.css";
import { SavedMemoryPicker } from "./personal-memory";
import { WatchSentEmail } from "./thread-watch-panel";
import styles from "./today-workspace.module.css";

const api =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
type Version = {
  version: number;
  to: string[];
  subject: string;
  body: string;
  created_by: string;
  provider: string | null;
};
type Draft = {
  id: string;
  commitment_id: string | null;
  source_id: string;
  current_version: number;
  status: string;
  action_id: string | null;
  versions: Version[];
};
type SendAction = {
  id: string;
  status: string;
  commitment_id?: string | null;
  verified_at?: string | null;
  payload_hash: string;
  payload: {
    connection_id?: string;
    sender: string;
    to: string;
    subject: string;
    body_text: string;
    draft_id?: string;
    draft_version?: number;
  };
  result: { message_id?: string; thread_id?: string | null };
};

async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`${api}${path}`, {
    method,
    credentials: "include",
    signal: AbortSignal.timeout(method === "POST" ? 65_000 : 10_000),
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
          : "NavoX could not complete this request.",
    );
  return data as T;
}

export function draftApprovalPayload(action: SendAction) {
  if (
    !/^[a-f0-9]{64}$/.test(action.payload_hash) ||
    !Number.isInteger(action.payload.draft_version) ||
    (action.payload.draft_version ?? 0) < 1 ||
    !action.payload.draft_id
  ) {
    throw new Error("Reload the current draft before approving.");
  }
  return {
    request_id: crypto.randomUUID(),
    expected_payload_hash: action.payload_hash,
    draft_version: action.payload.draft_version,
  };
}

export function draftReviewMatches(
  draft: Draft | null,
  action: SendAction | null,
  recipient: string,
  subject: string,
  body: string,
): boolean {
  return (
    !!draft &&
    !!action &&
    action.payload.draft_id === draft.id &&
    action.payload.draft_version === draft.current_version &&
    action.payload.to === recipient.trim() &&
    action.payload.subject === subject.trim() &&
    action.payload.body_text === body.trim()
  );
}

export function CommunicationDrafts({
  commitments,
  connections,
  paused,
  onStateChanged,
  selectedCommitmentId,
  selectionKey,
  onDraftCount,
}: {
  commitments: { id: string; title: string; description?: string | null }[];
  connections: EmailConnection[];
  paused: boolean;
  onStateChanged: () => Promise<void>;
  selectedCommitmentId?: string | null;
  selectionKey?: string | number;
  onDraftCount?: (count: number) => void;
}) {
  const [commitmentId, setCommitmentId] = useState(
    eligibleReplyCommitment(commitments, selectedCommitmentId)?.id ??
      (!selectedCommitmentId && commitments.length === 1
        ? commitments[0].id
        : ""),
  );
  const [saved, setSaved] = useState<Draft[]>([]);
  useEffect(() => {
    onDraftCount?.(
      saved.filter(
        (row) => !["sent", "completed", "discarded"].includes(row.status),
      ).length,
    );
  }, [saved, onDraftCount]);
  const [sources, setSources] = useState<EmailReplySource[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [recipient, setRecipient] = useState("");
  const [instructions, setInstructions] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [action, setAction] = useState<SendAction | null>(null);
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [manual, setManual] = useState(false);
  const [pendingSelection, setPendingSelection] = useState("");
  const sourceContextId = useRef("");
  const handledSelection = useRef("");
  const prepareRequest = useRef<{ key: string; id: string } | null>(null);
  const notifiedCompletion = useRef("");
  const available = connections.filter(
    (c) =>
      c.status === "active" &&
      c.granted_scopes.includes("https://www.googleapis.com/auth/gmail.send"),
  );
  const current = draft?.versions.at(-1);
  const dirty =
    current !== undefined &&
    (current.subject !== subject.trim() ||
      current.body !== body.trim() ||
      current.to[0] !== recipient.trim());

  const selectedCommitment = draft
    ? (draft.commitment_id ?? "")
    : (eligibleReplyCommitment(commitments, commitmentId)?.id ?? "");
  const requestContext = eligibleReplyCommitment(
    commitments,
    selectedCommitment,
  );
  const selectedSourceContext = selectReplySource(
    sources,
    draft?.source_id ?? sourceId,
  );
  const selectedSource = selectedSourceContext?.id ?? "";
  const selectedConnection =
    selectReplyConnection(
      connections,
      manual ? undefined : selectedSourceContext,
      connectionId,
    )?.id ?? "";

  const clearDraftForRequest = useCallback((id: string) => {
    setCommitmentId(id);
    setDraft(null);
    setAction(null);
    setApproved(false);
    setRecipient("");
    setSubject("");
    setBody("");
    setInstructions("");
    setConnectionId("");
    setManual(false);
    setPendingSelection("");
    setError("");
    sourceContextId.current = "";
  }, []);

  useEffect(() => {
    const signature = JSON.stringify([selectedCommitmentId, selectionKey]);
    if (handledSelection.current === signature) return;
    const requested = eligibleReplyCommitment(
      commitments,
      selectedCommitmentId,
    );
    if (!requested) return;
    handledSelection.current = signature;
    if (requested.id === selectedCommitment) return;
    if (draft || action || recipient || instructions || body) {
      setPendingSelection(requested.id);
      return;
    }
    clearDraftForRequest(requested.id);
    // A new selection is explicit intent to open a request, never to generate.
    // Keep unsaved edits until the user chooses to switch.
  }, [
    selectedCommitmentId,
    selectionKey,
    commitments,
    selectedCommitment,
    draft,
    action,
    recipient,
    instructions,
    body,
    clearDraftForRequest,
  ]);
  useEffect(() => {
    let disposed = false;
    void request<Draft[]>("/communication-drafts")
      .then((rows) => {
        if (!disposed) setSaved(rows);
      })
      .catch(() => {
        if (!disposed) setError("Saved drafts could not be loaded.");
      });
    return () => {
      disposed = true;
    };
  }, []);
  useEffect(() => {
    let disposed = false;
    setSources([]);
    setSourceId("");
    setSourcesLoading(!!selectedCommitment);
    if (selectedCommitment)
      void request<typeof sources>(
        `/communication-drafts/sources/${selectedCommitment}`,
      )
        .then((rows) => {
          if (!disposed) setSources(rows);
        })
        .catch(() => {
          if (!disposed) setError("The email sources could not be loaded.");
        })
        .finally(() => {
          if (!disposed) setSourcesLoading(false);
        });
    return () => {
      disposed = true;
    };
  }, [selectedCommitment]);

  useEffect(() => {
    if (!selectedSourceContext || draft || action || manual) return;
    if (sourceContextId.current === selectedSourceContext.id) return;
    sourceContextId.current = selectedSourceContext.id;
    setRecipient((value) => value || replyRecipient(selectedSourceContext));
    setSubject((value) => value || replySubject(selectedSourceContext));
    setConnectionId("");
    setApproved(false);
  }, [selectedSourceContext, draft, action, manual]);

  useEffect(() => {
    if (!action || !["approved", "executing"].includes(action.status)) return;
    let disposed = false;
    const timer = setTimeout(async () => {
      try {
        const result = await request<SendAction>(`/actions/${action.id}`);
        if (!disposed) {
          setAction(result);
        }
      } catch {
        if (!disposed)
          setError(
            "Could not refresh the send status. Check again before sending another email.",
          );
      }
    }, 2000);
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [action]);

  useEffect(() => {
    if (
      action?.status !== "completed" ||
      !action.verified_at ||
      notifiedCompletion.current === action.id
    )
      return;
    notifiedCompletion.current = action.id;
    void onStateChanged().catch(() =>
      setError(
        "The email was verified, but your daily view could not refresh. Refresh the page to update it.",
      ),
    );
  }, [action, onStateChanged]);

  function show(next: Draft) {
    const version = next.versions.at(-1);
    setDraft(next);
    setCommitmentId(next.commitment_id ?? "");
    setSourceId(next.source_id);
    setConnectionId("");
    sourceContextId.current = "";
    setManual(false);
    setAction(null);
    setApproved(false);
    setSaved((rows) => [next, ...rows.filter((row) => row.id !== next.id)]);
    if (version) {
      setRecipient(version.to[0]);
      setSubject(version.subject);
      setBody(version.body);
    }
  }
  async function perform(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Please try again.");
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (
      !action ||
      !["approved", "queued", "executing", "uncertain"].includes(action.status)
    )
      return;
    let live = true;
    const timer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      void request<SendAction>(`/actions/${action.id}`)
        .then((next) => {
          if (live) setAction(next);
        })
        .catch(() => {});
    }, 5000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [action]);
  const reviewMatches = manual
    ? manualEmailReviewMatches(
        action,
        selectedCommitment,
        recipient,
        subject,
        body,
      )
    : draftReviewMatches(draft, action, recipient, subject, body);
  const outcome = emailSendOutcome(action);
  const mayStartAnother = mayStartAnotherEmail(action);

  async function review() {
    if (!draft) return;
    if (draft.action_id) {
      setAction(await request<SendAction>(`/actions/${draft.action_id}`));
      return;
    }
    const key = `${draft.id}:${draft.current_version}:${selectedConnection}`;
    if (prepareRequest.current?.key !== key) {
      prepareRequest.current = { key, id: crypto.randomUUID() };
    }
    const next = await request<SendAction>(
      `/communication-drafts/${draft.id}/prepare`,
      "POST",
      replyPreparationPayload(
        draft,
        selectedConnection,
        prepareRequest.current.id,
      ),
    );
    setAction(next);
    setDraft({ ...draft, action_id: next.id });
  }

  async function reviewManualEmail() {
    if (action) {
      setAction(
        await request<SendAction>(`/actions/${action.id}/edit`, "POST", {
          to: recipient.trim(),
          subject: subject.trim(),
          body_text: body.trim(),
          post_send_state: "waiting",
        }),
      );
      setApproved(false);
      return;
    }
    const key = JSON.stringify([
      selectedCommitment,
      selectedConnection,
      recipient.trim(),
      subject.trim(),
      body.trim(),
    ]);
    if (prepareRequest.current?.key !== key)
      prepareRequest.current = { key, id: crypto.randomUUID() };
    setAction(
      await request<SendAction>(
        `/commitments/${selectedCommitment}/actions/gmail-send/prepare`,
        "POST",
        {
          request_id: prepareRequest.current.id,
          connection_id: selectedConnection,
          to: recipient.trim(),
          subject: subject.trim(),
          body_text: body.trim(),
          post_send_state: "waiting",
        },
      ),
    );
    setApproved(false);
  }

  return (
    <section className={styles.controlCard}>
      <h2>Draft a reply</h2>
      <p>
        Start with a request, tell NavoX what you want to say, and review the
        exact message before sending.
      </p>
      {requestContext && (
        <div className={workflowStyles.requestContext}>
          <span>Replying to your saved request</span>
          <strong>{requestContext.title}</strong>
          {requestContext.description && <p>{requestContext.description}</p>}
        </div>
      )}
      {pendingSelection && (
        <div className={workflowStyles.notice}>
          <p>Another request is selected. Your current email is still here.</p>
          <button
            type="button"
            disabled={busy || !mayStartAnother}
            onClick={() => clearDraftForRequest(pendingSelection)}
          >
            Discard edits and open selected request
          </button>
          {action && <p>Check the current send outcome before switching.</p>}
        </div>
      )}
      {error && (
        <p role="alert" className={styles.inlineError}>
          {error}
        </p>
      )}
      <p>
        Nothing is sent until you approve and tap Send. Use only reasons and
        facts you have provided.
      </p>
      {commitmentId && !selectedCommitment && !draft && (
        <p role="alert">
          This request is no longer available. Your text is still here. Choose
          another request before preparing an email.
        </p>
      )}
      {saved.length > 0 && (
        <details>
          <summary>Saved drafts</summary>
          {saved.map((item) => (
            <button
              type="button"
              key={item.id}
              disabled={busy || dirty || !mayStartAnother}
              onClick={() =>
                void perform(async () => {
                  const next = await request<Draft>(
                    `/communication-drafts/${item.id}`,
                  );
                  show(next);
                  if (next.action_id)
                    setAction(
                      await request<SendAction>(`/actions/${next.action_id}`),
                    );
                })
              }
            >
              {item.versions.at(-1)?.subject || "Untitled draft"} · version{" "}
              {item.current_version}
            </button>
          ))}
        </details>
      )}
      {(draft || manual) && (
        <button
          type="button"
          disabled={busy || dirty || !mayStartAnother}
          onClick={() => clearDraftForRequest(selectedCommitment)}
        >
          New draft
        </button>
      )}
      <div className={styles.approvalForm}>
        {!draft && (
          <label>
            Request
            <select
              value={selectedCommitment}
              disabled={busy || !!action}
              onChange={(event) => {
                const next = event.target.value;
                if (next === selectedCommitment) return;
                if (recipient || subject || instructions || body)
                  setPendingSelection(next);
                else clearDraftForRequest(next);
              }}
            >
              <option value="" disabled>
                Choose the request to answer
              </option>
              {commitments.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
          </label>
        )}
        {!draft && !manual && (
          <label>
            Email source
            <select
              value={selectedSource}
              disabled={busy || !!action || sourcesLoading}
              onChange={(event) => {
                setSourceId(event.target.value);
                setRecipient("");
                setSubject("");
                setConnectionId("");
                setApproved(false);
              }}
            >
              <option value="" disabled>
                Choose a Gmail source
              </option>
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.subject || source.label || source.provider}
                </option>
              ))}
            </select>
          </label>
        )}
        {!manual && sourcesLoading && (
          <p role="status">Finding this request’s email…</p>
        )}
        {!draft && !manual && !sourcesLoading && sources.length === 0 && (
          <p>
            This request has no readable Gmail message available for drafting.
            You can write an email yourself below.
          </p>
        )}
        {!draft && !manual && sources.length > 1 && !selectedSource && (
          <p>Choose the source message so NavoX uses the right conversation.</p>
        )}
        {!draft && !manual && selectedSource && !recipient && (
          <p>Enter the recipient’s email address. NavoX will not guess it.</p>
        )}
        <label>
          Recipient
          <input
            type="email"
            value={recipient}
            disabled={
              busy || (!!action && action.status !== "awaiting_approval")
            }
            onChange={(event) => {
              setRecipient(event.target.value);
              setApproved(false);
            }}
          />
        </label>
        {!manual && (
          <>
            <label>
              What should the reply say?
              <textarea
                maxLength={4000}
                rows={3}
                value={instructions}
                disabled={
                  busy ||
                  paused ||
                  (!!action && action.status !== "awaiting_approval")
                }
                onChange={(event) => setInstructions(event.target.value)}
                placeholder="For example: Thank Maya and ask whether Friday afternoon works."
              />
            </label>
            <SavedMemoryPicker
              value={instructions}
              onChange={setInstructions}
              maxLength={4000}
              disabled={
                busy ||
                paused ||
                (!!action && action.status !== "awaiting_approval")
              }
              kinds={["reply_length", "tone", "signoff"]}
            />
          </>
        )}
        {!manual && (
          <button
            type="button"
            disabled={
              busy ||
              paused ||
              !instructions.trim() ||
              !recipient.trim() ||
              !selectedCommitment ||
              (!draft && !selectedSource) ||
              (!!action && action.status !== "awaiting_approval") ||
              dirty
            }
            onClick={() =>
              void perform(async () => {
                if (draft)
                  show(
                    await request<Draft>(
                      `/communication-drafts/${draft.id}/regenerate`,
                      "POST",
                      { expected_version: draft.current_version, instructions },
                    ),
                  );
                else
                  show(
                    await request<Draft>("/communication-drafts", "POST", {
                      commitment_id: selectedCommitment,
                      source_id: selectedSource,
                      recipient,
                      instructions,
                    }),
                  );
              })
            }
          >
            {busy
              ? "Working…"
              : draft
                ? "Generate another version"
                : "Generate draft"}
          </button>
        )}
        {!draft && !action && (
          <button
            type="button"
            disabled={busy || paused}
            onClick={() => setManual((value) => !value)}
          >
            {manual ? "Use NavoX to draft" : "Write email yourself"}
          </button>
        )}
        {manual && (
          <>
            <p>
              Your own email. It will be sent as a new message after exact
              approval; it is not generated by NavoX.
            </p>
            <label>
              Subject
              <input
                maxLength={256}
                value={subject}
                disabled={
                  busy || (!!action && action.status !== "awaiting_approval")
                }
                onChange={(event) => {
                  setSubject(event.target.value);
                  setApproved(false);
                }}
              />
            </label>
            <label>
              Message
              <textarea
                maxLength={50000}
                rows={7}
                value={body}
                disabled={
                  busy || (!!action && action.status !== "awaiting_approval")
                }
                onChange={(event) => {
                  setBody(event.target.value);
                  setApproved(false);
                }}
              />
            </label>
            {action?.status === "awaiting_approval" && (
              <button
                type="button"
                disabled={
                  busy ||
                  paused ||
                  reviewMatches ||
                  !recipient.trim() ||
                  !subject.trim() ||
                  !body.trim()
                }
                onClick={() => void perform(reviewManualEmail)}
              >
                Save changes for approval
              </button>
            )}
            {!action && (
              <>
                <label>
                  Send from
                  <select
                    value={selectedConnection}
                    disabled={busy}
                    onChange={(event) => setConnectionId(event.target.value)}
                  >
                    <option value="" disabled>
                      Choose your Gmail account
                    </option>
                    {available.map((connection) => (
                      <option key={connection.id} value={connection.id}>
                        {connection.external_email || "Google account"}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  disabled={
                    busy ||
                    paused ||
                    !selectedCommitment ||
                    !selectedConnection ||
                    !recipient.trim() ||
                    !subject.trim() ||
                    !body.trim()
                  }
                  onClick={() => void perform(reviewManualEmail)}
                >
                  Review exact email
                </button>
                {available.length === 0 && (
                  <p>Enable Gmail sending in Connections to send this email.</p>
                )}
              </>
            )}
          </>
        )}
        {draft && (
          <>
            {!action && (
              <p role="status">Draft ready · review your message below.</p>
            )}
            <p>
              Version {draft.current_version} ·{" "}
              {current?.created_by === "AI"
                ? "Generated by NavoX"
                : "Your revision"}
            </p>
            <label>
              Subject
              <input
                maxLength={256}
                value={subject}
                disabled={
                  busy || (!!action && action.status !== "awaiting_approval")
                }
                onChange={(event) => {
                  setSubject(event.target.value);
                  setApproved(false);
                }}
              />
            </label>
            <label>
              Message
              <textarea
                maxLength={50000}
                rows={7}
                value={body}
                disabled={
                  busy || (!!action && action.status !== "awaiting_approval")
                }
                onChange={(event) => {
                  setBody(event.target.value);
                  setApproved(false);
                }}
              />
            </label>
            <button
              type="button"
              disabled={
                busy ||
                paused ||
                !dirty ||
                !subject.trim() ||
                !body.trim() ||
                (!!action && action.status !== "awaiting_approval")
              }
              onClick={() =>
                void perform(async () =>
                  show(
                    await request<Draft>(
                      `/communication-drafts/${draft.id}`,
                      "PATCH",
                      {
                        expected_version: draft.current_version,
                        to: [recipient],
                        subject,
                        body,
                      },
                    ),
                  ),
                )
              }
            >
              Save revision
            </button>
            {dirty && (
              <p>
                Save this revision before review. Changes require a new
                approval.
              </p>
            )}
            <div>
              <button
                type="button"
                disabled={
                  busy ||
                  paused ||
                  dirty ||
                  (!!action && action.status !== "awaiting_approval")
                }
                onClick={() =>
                  void perform(async () =>
                    show(
                      await request<Draft>(
                        `/communication-drafts/${draft.id}/regenerate`,
                        "POST",
                        {
                          expected_version: draft.current_version,
                          instructions:
                            "Make this draft shorter. Preserve its verified facts and the student's edits. Do not invent personal reasons, availability, promises or deadlines.",
                        },
                      ),
                    ),
                  )
                }
              >
                Make it shorter
              </button>
              <button
                type="button"
                disabled={
                  busy || (!!action && action.status !== "awaiting_approval")
                }
                onClick={() =>
                  void perform(async () => {
                    await request(
                      `/communication-drafts/${draft.id}?expected_version=${draft.current_version}`,
                      "DELETE",
                    );
                    setSaved((rows) => rows.filter((r) => r.id !== draft.id));
                    clearDraftForRequest(selectedCommitment);
                  })
                }
              >
                Discard draft
              </button>
            </div>
            <details>
              <summary>Version history (up to 100 recent versions)</summary>
              {draft.versions.map((v) => (
                <div key={v.version}>
                  <strong>
                    Version {v.version} ·{" "}
                    {v.created_by === "AI" ? "AI draft" : "Your revision"}
                  </strong>
                  <p>{v.subject}</p>
                  <p style={{ whiteSpace: "pre-wrap" }}>{v.body}</p>
                </div>
              ))}
            </details>
            {!action && (
              <>
                <label>
                  Send from
                  <select
                    value={selectedConnection}
                    onChange={(event) => setConnectionId(event.target.value)}
                  >
                    <option value="" disabled>
                      Choose your Gmail account
                    </option>
                    {available
                      .filter(
                        (connection) =>
                          !selectedSourceContext?.connection_id ||
                          connection.id === selectedSourceContext.connection_id,
                      )
                      .map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.external_email ?? "Google account"}
                        </option>
                      ))}
                  </select>
                </label>
                <button
                  type="button"
                  disabled={busy || paused || dirty || !selectedConnection}
                  onClick={() => void perform(review)}
                >
                  Review exact send
                </button>
                {available.length === 0 && (
                  <p>Enable Gmail sending below to send this draft.</p>
                )}
              </>
            )}
          </>
        )}
        {action && (
          <div className={styles.approvalPanel}>
            <div
              role="status"
              className={workflowStyles.outcome}
              data-tone={outcome.tone}
            >
              <strong>{outcome.label}</strong>
              <p>{outcome.detail}</p>
            </div>
            <p>
              From: {action.payload.sender}
              <br />
              To: {action.payload.to}
            </p>
            <strong>{action.payload.subject}</strong>
            <p style={{ whiteSpace: "pre-wrap" }}>{action.payload.body_text}</p>
            {action.status === "awaiting_approval" && (
              <>
                <label>
                  <input
                    type="checkbox"
                    checked={approved}
                    disabled={busy || dirty || !reviewMatches}
                    onChange={(event) => setApproved(event.target.checked)}
                  />{" "}
                  I approve sending this exact email now.
                </label>
                <button
                  type="button"
                  disabled={
                    busy || paused || dirty || !approved || !reviewMatches
                  }
                  onClick={() =>
                    void perform(async () =>
                      setAction(
                        await request<SendAction>(
                          manual
                            ? `/actions/${action.id}/approve`
                            : `/communication-drafts/${draft?.id}/approve`,
                          "POST",
                          manual
                            ? manualEmailApprovalPayload(action)
                            : draftApprovalPayload(action),
                        ),
                      ),
                    )
                  }
                >
                  Send
                </button>
              </>
            )}
            {action.status === "completed" &&
              action.verified_at &&
              action.result.message_id && (
                <p>Gmail confirmed the sent message.</p>
              )}
            <WatchSentEmail
              action={action}
              connections={connections}
              paused={paused}
            />
          </div>
        )}
      </div>
    </section>
  );
}
