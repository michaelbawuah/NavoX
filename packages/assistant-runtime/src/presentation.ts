import type {
  AssistantBlock,
  AssistantPresentation,
  AssistantPresentationPlan,
  CapabilityDecision,
} from "@navox/contracts";
import { LIMITS } from "./limits";
import { clampText, parsePresentationPlan } from "./validate";

function speechSource(
  decision: CapabilityDecision,
  blocks: AssistantBlock[],
): string | null {
  // Speech is only a rendered offer, never authority: an answer that would
  // need approval is shown on screen and stays silent.
  if (decision.requires_approval) return null;
  if (
    decision.response_state !== "READY" &&
    decision.response_state !== "CLARIFY"
  )
    return null;
  const answer = blocks.find((block) => block.kind === "ANSWER");
  if (answer && answer.kind === "ANSWER" && answer.text.trim())
    return answer.text;
  const notice = blocks.find((block) => block.kind === "NOTICE");
  if (notice && notice.kind === "NOTICE" && notice.text.trim())
    return notice.text;
  return null;
}

/**
 * Decides rendering and speech. A typed turn never speaks: the presentation
 * choice follows the submitted modality and carries no authority.
 */
export function buildPresentationPlan(input: {
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
  presentation: AssistantPresentation;
}): AssistantPresentationPlan {
  const source = speechSource(input.decision, input.blocks);
  const speak = input.presentation !== "TEXT" && source !== null;
  return parsePresentationPlan({
    presentation: input.presentation,
    speak,
    speech_text:
      speak && source !== null
        ? clampText(source, LIMITS.maxSpeechLength)
        : null,
    blocks: input.blocks,
  });
}
