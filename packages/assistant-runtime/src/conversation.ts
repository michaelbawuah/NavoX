import { AssistantError } from "./errors";
import type { AssistantTurnRecord } from "./store";
import { isRecord, isUuid } from "./validate";

export const CONVERSATION_LIMITS = {
  maxReferences: 4,
  maxQuestionLength: 500,
  maxAnswerLength: 3000,
} as const;

export interface ConversationReference {
  task_id: string;
  question: string;
  answer: string;
}

export interface ConversationAnswer {
  task_id: string;
  trace_id: string;
  session_id: string;
  turn_sequence: number;
  answer: string;
  actions_executed: false;
}

const ENVELOPE_FIELDS = [
  "task_id",
  "trace_id",
  "session_id",
  "turn_sequence",
  "answer",
  "actions_executed",
] as const;

function invalid(): never {
  throw new AssistantError(
    "invalid_request",
    "The assistant returned an unverified conversation answer.",
  );
}

function boundedText(value: unknown, maximum: number): value is string {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.length <= maximum &&
    !Array.from(value).some(
      (character) =>
        character.charCodeAt(0) < 32 && !"\t\n\r".includes(character),
    )
  );
}

/** A model response is text only; no extra field can supply action authority. */
export function parseConversationAnswer(
  payload: unknown,
  sessionId: string,
): ConversationAnswer {
  if (!isRecord(payload)) invalid();
  if (
    Object.keys(payload).length !== ENVELOPE_FIELDS.length ||
    Object.keys(payload).some(
      (field) => !(ENVELOPE_FIELDS as readonly string[]).includes(field),
    ) ||
    !isUuid(payload.task_id) ||
    !isUuid(payload.trace_id) ||
    !isUuid(payload.session_id) ||
    payload.session_id !== sessionId ||
    !Number.isSafeInteger(payload.turn_sequence) ||
    (payload.turn_sequence as number) < 1 ||
    payload.actions_executed !== false ||
    !boundedText(payload.answer, CONVERSATION_LIMITS.maxAnswerLength) ||
    payload.answer !== payload.answer.trim()
  )
    invalid();
  return {
    task_id: payload.task_id,
    trace_id: payload.trace_id,
    session_id: payload.session_id,
    turn_sequence: payload.turn_sequence as number,
    answer: payload.answer,
    actions_executed: false,
  };
}

/**
 * Only verified conversation answers from this saved session become context.
 * Connected-source answers, drafts, evidence and rich cards are never copied.
 * The backend independently re-reads each task and verifies its exact text.
 */
export function conversationReferences(
  turns: readonly AssistantTurnRecord[],
  sessionId: string,
): ConversationReference[] {
  const references: ConversationReference[] = [];
  const seen = new Set<string>();
  for (const turn of turns) {
    const taskId = turn.decision.reason.match(
      /^conversation\.answer:([0-9a-f-]+)$/iu,
    )?.[1];
    const [block] = turn.presentation.blocks;
    if (
      turn.session_id !== sessionId ||
      turn.state !== "READY" ||
      !taskId ||
      !isUuid(taskId) ||
      seen.has(taskId) ||
      turn.decision.kind !== "PRESENT" ||
      turn.decision.capability_id !== null ||
      turn.decision.target !== null ||
      turn.decision.requires_approval ||
      turn.decision.action_state !== "NONE" ||
      turn.decision.action_id !== null ||
      turn.decision.response_state !== "READY" ||
      turn.action_refs.length > 0 ||
      turn.plan.intents.length !== 1 ||
      turn.plan.intents[0]?.kind !== "assistant.delivery" ||
      turn.plan.intents[0]?.capability_id !== null ||
      turn.presentation.blocks.length !== 1 ||
      block?.kind !== "ANSWER" ||
      turn.response_text !== block.text ||
      !boundedText(turn.question, CONVERSATION_LIMITS.maxQuestionLength) ||
      !boundedText(block.text, CONVERSATION_LIMITS.maxAnswerLength)
    )
      continue;
    seen.add(taskId);
    references.push({
      task_id: taskId,
      question: turn.question,
      answer: block.text,
    });
  }
  return references.slice(-CONVERSATION_LIMITS.maxReferences);
}
