import { createHash } from "node:crypto";
import type { AssistantModality } from "@navox/contracts";
import { AssistantError } from "./errors";
import { LIMITS } from "./limits";

export interface TurnFingerprintInput {
  text: string;
  modality: AssistantModality;
  timezone: string | null;
  referents: readonly string[];
}

/** Stable fingerprint of the client-supplied payload behind one request ID. */
export function fingerprintTurn(input: TurnFingerprintInput): string {
  const canonical = JSON.stringify([
    input.text,
    input.modality,
    input.timezone ?? null,
    [...input.referents],
  ]);
  return createHash("sha256").update(canonical).digest("hex");
}

export type ReplayOutcome = "replay" | "conflict";

/** An identical retry replays; a changed payload behind the same ID is refused. */
export function resolveReplay(
  existingFingerprint: string,
  incomingFingerprint: string,
): ReplayOutcome {
  return existingFingerprint === incomingFingerprint ? "replay" : "conflict";
}

export interface RetentionWindow {
  createdAt: string;
  expiresAt: string;
}

export function retentionWindow(
  now: Date,
  days: number = LIMITS.retentionDays,
): RetentionWindow {
  const expiresAt = new Date(now.getTime() + days * 24 * 60 * 60 * 1000);
  return { createdAt: now.toISOString(), expiresAt: expiresAt.toISOString() };
}

/** Refuses to grow a session past its turn budget before any upstream work. */
export function assertTurnBudget(nextSequence: number): void {
  if (nextSequence > LIMITS.maxTurnsPerSession) {
    throw new AssistantError(
      "unsupported",
      `This conversation reached its ${LIMITS.maxTurnsPerSession}-turn limit. Start a new one.`,
    );
  }
}
