export interface EmailReplySource {
  id: string;
  external_id: string;
  provider: string;
  connection_id?: string | null;
  subject?: string | null;
  sender?: string | null;
  label?: string;
}

export interface EmailConnection {
  id: string;
  status: string;
  granted_scopes: string[];
  external_email?: string | null;
}

export function eligibleReplyCommitment<T extends { id: string }>(
  commitments: T[],
  requestedId: string | null | undefined,
): T | undefined {
  return requestedId
    ? commitments.find((item) => item.id === requestedId)
    : undefined;
}

export function selectReplySource(
  sources: EmailReplySource[],
  selectedId: string,
): EmailReplySource | undefined {
  if (selectedId) return sources.find((source) => source.id === selectedId);
  return sources.length === 1 ? sources[0] : undefined;
}

export function selectReplyConnection(
  connections: EmailConnection[],
  source: EmailReplySource | undefined,
  selectedId: string,
): EmailConnection | undefined {
  const eligible = connections.filter(
    (connection) =>
      connection.status === "active" &&
      connection.granted_scopes.includes(
        "https://www.googleapis.com/auth/gmail.send",
      ),
  );
  if (selectedId) {
    if (source?.connection_id && source.connection_id !== selectedId)
      return undefined;
    return eligible.find((connection) => connection.id === selectedId);
  }
  if (source?.connection_id)
    return eligible.find(
      (connection) => connection.id === source.connection_id,
    );
  return eligible.length === 1 ? eligible[0] : undefined;
}

// Context is a convenience, never an instruction to send or an inferred address.
export function replyRecipient(source: EmailReplySource | undefined): string {
  const value = source?.sender?.trim() ?? "";
  return /^[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+$/.test(value) ? value : "";
}

export function replySubject(source: EmailReplySource | undefined): string {
  const value = source?.subject?.trim() ?? "";
  if (!value || /[\r\n]/.test(value)) return "";
  return (/^re:/i.test(value) ? value : `Re: ${value}`).slice(0, 256);
}

export interface EmailSendOutcome {
  label: string;
  detail: string;
  tone: "ready" | "progress" | "success" | "attention";
}

export function emailSendOutcome(
  action: { status: string; verified_at?: string | null } | null,
): EmailSendOutcome {
  if (!action)
    return {
      label: "Draft ready",
      detail: "Review the recipient and message before preparing approval.",
      tone: "ready",
    };
  if (action.status === "completed" && action.verified_at)
    return {
      label: "Sent and verified",
      detail: "Gmail confirmed the sent message.",
      tone: "success",
    };
  if (["approved", "executing"].includes(action.status))
    return {
      label: "Sending",
      detail:
        "Your approved message is being sent. Keep this draft to check the result.",
      tone: "progress",
    };
  if (action.status === "awaiting_approval")
    return {
      label: "Needs approval",
      detail:
        "Nothing has been sent. Approve only the exact email shown below.",
      tone: "ready",
    };
  if (["uncertain", "completed"].includes(action.status))
    return {
      label: "Needs review",
      detail:
        "Sending could not be verified. Check Gmail Sent mail and refresh this status. Do not send another copy.",
      tone: "attention",
    };
  return {
    label: "Needs your help",
    detail:
      action.status === "failed"
        ? "The send failed. Check the account connection and Gmail Sent mail before preparing a new message."
        : "This approval is no longer available. Refresh the status and review the message before starting again.",
    tone: "attention",
  };
}

export function manualEmailApprovalPayload(action: { payload_hash: string }) {
  if (!/^[a-f0-9]{64}$/.test(action.payload_hash))
    throw new Error("Reload the current email before approving.");
  return {
    request_id: crypto.randomUUID(),
    expected_payload_hash: action.payload_hash,
  };
}

export function manualEmailReviewMatches(
  action: {
    commitment_id?: string | null;
    payload: {
      draft_id?: string;
      to: string;
      subject: string;
      body_text: string;
    };
  } | null,
  commitmentId: string,
  recipient: string,
  subject: string,
  body: string,
): boolean {
  return (
    !!action &&
    !!commitmentId &&
    action.commitment_id === commitmentId &&
    !action.payload.draft_id &&
    action.payload.to === recipient.trim() &&
    action.payload.subject === subject.trim() &&
    action.payload.body_text === body.trim()
  );
}

export function mayStartAnotherEmail(
  action: { status: string; verified_at?: string | null } | null,
): boolean {
  return (
    !action ||
    ["failed", "rejected", "expired", "blocked"].includes(action.status) ||
    (action.status === "completed" && !!action.verified_at)
  );
}

export function replyPreparationPayload(
  draft: { current_version: number },
  connectionId: string,
  requestId: string,
) {
  if (
    !connectionId ||
    !requestId ||
    !Number.isInteger(draft.current_version) ||
    draft.current_version < 1
  )
    throw new Error("Reload the current draft and choose its Gmail account.");
  return {
    expected_version: draft.current_version,
    connection_id: connectionId,
    request_id: requestId,
    reply_to_source: true,
  };
}
