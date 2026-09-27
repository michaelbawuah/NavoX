export type AssistanceDomain =
  | "assistant"
  | "planning"
  | "meeting_preparation"
  | "ranking";
export type AssistanceAnswer = { details: string[]; actions_executed: false };

export async function requestAssistance(
  api: string,
  input: {
    domain: AssistanceDomain;
    itemIds: string[];
    instructions: string;
    sessionId: string | null;
  },
  onSession: (id: string) => void,
): Promise<AssistanceAnswer> {
  if (
    input.itemIds.length < 1 ||
    input.itemIds.length > 12 ||
    new Set(input.itemIds).size !== input.itemIds.length ||
    !input.instructions.trim() ||
    input.instructions.length > 2000
  ) {
    throw new Error("Choose up to 12 distinct tasks and enter a request.");
  }
  let sessionId = input.sessionId;
  if (input.domain === "assistant" && sessionId === null) {
    const created = await fetch(`${api}/ai/sessions`, {
      method: "POST",
      credentials: "include",
    });
    if (!created.ok) {
      throw new Error("A conversation could not be started. Please try again.");
    }
    const session = await created.json();
    if (typeof session.id !== "string" || !session.id) {
      throw new Error("A conversation could not be started. Please try again.");
    }
    sessionId = session.id;
    onSession(session.id);
  }
  const response = await fetch(`${api}/ai/operations/${input.domain}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_ids: input.itemIds,
      instructions: input.instructions,
      target_id:
        input.domain === "planning" && input.itemIds.length === 1
          ? input.itemIds[0]
          : null,
      session_id: input.domain === "assistant" ? sessionId : null,
    }),
  });
  if (!response.ok) {
    throw new Error(
      response.status === 503
        ? "AI suggestions are not available for this request yet. You can still manage your tasks normally."
        : "The selected information is unavailable or changed. Refresh your tasks and try again.",
    );
  }
  const result = await response.json();
  if (
    result.actions_executed !== false ||
    !Array.isArray(result.details) ||
    result.details.some((line: unknown) => typeof line !== "string")
  ) {
    throw new Error("Suggestions could not be loaded. Please try again.");
  }
  return result;
}
