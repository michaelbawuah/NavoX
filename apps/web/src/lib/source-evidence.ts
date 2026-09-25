export interface EvidenceExcerpt {
  source: "subject" | "content";
  text: string;
}

export class SourceEvidenceError extends Error {}

const evidenceErrors: Record<number, string> = {
  401: "Sign in again to view this source.",
  403: "Resume NavoX and reconnect Gmail read access to view this source.",
  404: "This source reference is no longer available.",
  409: "The saved evidence could not be verified against the current email. Sync Gmail again.",
  410: "This email is no longer available in Gmail.",
  429: "Gmail is temporarily rate limited. Try again later.",
  504: "The source request timed out. Try again.",
};

export async function readSourceEvidence(
  apiBaseUrl: string,
  evidenceId: string,
  signal: AbortSignal,
): Promise<EvidenceExcerpt[]> {
  const response = await fetch(
    `${apiBaseUrl}/intelligence/evidence/${encodeURIComponent(evidenceId)}`,
    { credentials: "include", cache: "no-store", signal },
  );
  if (!response.ok) {
    throw new SourceEvidenceError(
      evidenceErrors[response.status] ??
        "The source could not be loaded. Try again later.",
    );
  }
  const data: unknown = await response.json();
  if (!data || typeof data !== "object" || !("excerpts" in data)) {
    throw new SourceEvidenceError("The source response could not be verified.");
  }
  const { excerpts } = data;
  if (
    !("evidence_id" in data) ||
    data.evidence_id !== evidenceId ||
    !Array.isArray(excerpts) ||
    excerpts.length < 1 ||
    excerpts.length > 8 ||
    !excerpts.every(
      (excerpt) =>
        excerpt &&
        (excerpt.source === "subject" || excerpt.source === "content") &&
        typeof excerpt.text === "string" &&
        [...excerpt.text].length > 0 &&
        [...excerpt.text].length <= 512,
    )
  ) {
    throw new SourceEvidenceError("The source response could not be verified.");
  }
  // A repeated span needs only one displayed quote.
  return excerpts.filter(
    (excerpt, index) =>
      excerpts.findIndex(
        (other) =>
          other.source === excerpt.source && other.text === excerpt.text,
      ) === index,
  );
}
