import type { AssistantErrorCode } from "@navox/contracts";

const STATUS: Record<AssistantErrorCode, number> = {
  invalid_request: 400,
  unauthorized: 401,
  forbidden: 403,
  not_found: 404,
  expired: 404,
  conflict: 409,
  unsupported: 422,
  unavailable: 503,
  misconfigured: 503,
};

const RETRYABLE: Record<AssistantErrorCode, boolean> = {
  invalid_request: false,
  unauthorized: false,
  forbidden: false,
  not_found: false,
  expired: false,
  conflict: false,
  unsupported: false,
  unavailable: true,
  misconfigured: true,
};

/** Typed failure surfaced to the API layer. Nothing here is a client authority. */
export class AssistantError extends Error {
  readonly code: AssistantErrorCode;
  readonly status: number;
  readonly retryable: boolean;
  /**
   * Internal, non-serialized discriminator for callers that must react to one
   * exact upstream condition (for example a planner with no qualified
   * provider). It never reaches a client response body.
   */
  readonly reason: string | null;

  constructor(
    code: AssistantErrorCode,
    message: string,
    options?: { retryable?: boolean; status?: number; reason?: string },
  ) {
    super(message);
    this.name = "AssistantError";
    this.code = code;
    this.status = options?.status ?? STATUS[code];
    this.retryable = options?.retryable ?? RETRYABLE[code];
    this.reason = options?.reason ?? null;
  }
}

export function isAssistantError(value: unknown): value is AssistantError {
  return value instanceof AssistantError;
}

/** Collapses an unexpected throw into a qualified, retryable failure. */
export function toAssistantError(value: unknown): AssistantError {
  if (isAssistantError(value)) return value;
  // Database, network and adapter errors can contain SQL, hosts or credentials.
  // Keep those details out of the public assistant response.
  return new AssistantError(
    "unavailable",
    "The assistant is unavailable right now.",
    {
      retryable: true,
    },
  );
}
