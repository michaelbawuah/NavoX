"use client";

import { useEffect, useRef, useState } from "react";
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
  commitment_id: string;
  source_id: string;
  current_version: number;
  status: string;
  action_id: string | null;
  versions: Version[];
};
type SendAction = {
  id: string;
  status: string;
  payload_hash: string;
  payload: {
    sender: string;
    to: string;
    subject: string;
    body_text: string;
    draft_id: string;
    draft_version: number;
  };
  result: { message_id?: string };
};

async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`${api}${path}`, {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok)
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : "NavoX could not complete this request.",
    );
  return data as T;
}

export function draftApprovalPayload(action: SendAction) {
  if (
    !/^[a-f0-9]{64}$/.test(action.payload_hash) ||
    !Number.isInteger(action.payload.draft_version) ||
    action.payload.draft_version < 1 ||
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
}: {
  commitments: { id: string; title: string }[];
  connections: {
    id: string;
    status: string;
    granted_scopes: string[];
    external_email?: string | null;
  }[];
  paused: boolean;
  onStateChanged: () => Promise<void>;
}) {
  const [commitmentId, setCommitmentId] = useState("");
  const [saved, setSaved] = useState<Draft[]>([]);
  const [sources, setSources] = useState<
    { id: string; external_id: string; provider: string }[]
  >([]);
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
  const prepareRequest = useRef<{ key: string; id: string } | null>(null);
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

  const selectedCommitment = commitmentId || commitments[0]?.id || "";
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
    if (selectedCommitment)
      void request<typeof sources>(
        `/communication-drafts/sources/${selectedCommitment}`,
      )
        .then((rows) => {
          if (!disposed) setSources(rows);
        })
        .catch(() => {
          if (!disposed) setError("The email sources could not be loaded.");
        });
    return () => {
      disposed = true;
    };
  }, [selectedCommitment]);

  useEffect(() => {
    if (!action || !["approved", "executing"].includes(action.status)) return;
    let disposed = false;
    const timer = setTimeout(async () => {
      try {
        const result = await request<SendAction>(`/actions/${action.id}`);
        if (!disposed) {
          setAction(result);
          if (result.status === "completed") await onStateChanged();
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
  }, [action, onStateChanged]);

  function show(next: Draft) {
    const version = next.versions.at(-1);
    setDraft(next);
    setCommitmentId(next.commitment_id);
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
  const selectedSource = sourceId || sources[0]?.id || "";
  const selectedConnection = connectionId || available[0]?.id || "";
  const reviewMatches = draftReviewMatches(
    draft,
    action,
    recipient,
    subject,
    body,
  );

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
      {
        expected_version: draft.current_version,
        connection_id: selectedConnection,
        request_id: prepareRequest.current.id,
      },
    );
    setAction(next);
    setDraft({ ...draft, action_id: next.id });
  }

  return (
    <section className={styles.controlCard}>
      <h2>Draft a reply</h2>
      <p>
        Tell NavoX what you want to say, then edit the draft and approve the
        exact email before it is sent.
      </p>
      {error && (
        <p role="alert" className={styles.inlineError}>
          {error}
        </p>
      )}
      <p>
        This first version supports one recipient and plain text. It does not
        add attachments, CC, or BCC.
      </p>
      {saved.length > 0 && (
        <details>
          <summary>Saved drafts</summary>
          {saved.map((item) => (
            <button
              type="button"
              key={item.id}
              disabled={busy || dirty}
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
      {draft && (
        <button
          type="button"
          disabled={busy || dirty}
          onClick={() => {
            setDraft(null);
            setAction(null);
            setApproved(false);
            setRecipient("");
            setSubject("");
            setBody("");
            setInstructions("");
          }}
        >
          New draft
        </button>
      )}
      <div className={styles.approvalForm}>
        {!draft && (
          <label>
            Commitment
            <select
              value={selectedCommitment}
              onChange={(event) => setCommitmentId(event.target.value)}
            >
              {commitments.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
          </label>
        )}
        {!draft && (
          <label>
            Email source
            <select
              value={selectedSource}
              onChange={(event) => setSourceId(event.target.value)}
            >
              <option value="" disabled>
                Choose a Gmail source
              </option>
              {sources.map((source, index) => (
                <option key={source.id} value={source.id}>
                  {source.provider} source {index + 1} · {source.external_id}
                </option>
              ))}
            </select>
          </label>
        )}
        {!draft && sources.length === 0 && (
          <p>
            This commitment has no readable Gmail message available for
            drafting.
          </p>
        )}
        <label>
          Recipient
          <input
            type="email"
            value={recipient}
            onChange={(event) => {
              setRecipient(event.target.value);
              setApproved(false);
            }}
          />
        </label>
        <label>
          What should the reply say?
          <textarea
            maxLength={4000}
            rows={3}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            placeholder="For example: Thank Maya and ask whether Friday afternoon works."
          />
        </label>
        <button
          type="button"
          disabled={
            busy ||
            paused ||
            !instructions.trim() ||
            !recipient.trim() ||
            !selectedCommitment ||
            (!draft && !selectedSource) ||
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
        {draft && (
          <>
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
                onChange={(event) => {
                  setBody(event.target.value);
                  setApproved(false);
                }}
              />
            </label>
            <button
              type="button"
              disabled={
                busy || paused || !dirty || !subject.trim() || !body.trim()
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
                    {available.map((c) => (
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
            <strong>{action.status.replaceAll("_", " ")}</strong>
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
                          `/communication-drafts/${draft?.id}/approve`,
                          "POST",
                          draftApprovalPayload(action),
                        ),
                      ),
                    )
                  }
                >
                  Approve and send now
                </button>
              </>
            )}
            {action.result.message_id && (
              <p>Gmail confirmed the sent message.</p>
            )}
            {action.status === "uncertain" && (
              <p>
                The send could not be confirmed. Check Sent mail before trying
                again.
              </p>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void perform(async () =>
                  setAction(await request<SendAction>(`/actions/${action.id}`)),
                )
              }
            >
              Refresh status
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
