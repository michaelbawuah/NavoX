/** Bounds shared by the validators, the SQL migration and the API handlers. */
export const LIMITS = {
  /** Longest operator question accepted in a turn. Mirrors SPEC-002 `/today/query`. */
  maxQuestionLength: 500,
  /** Longest answer text retained and rendered for one turn. */
  maxAnswerLength: 4000,
  /** Longest spoken text offered to the browser speech adapter. */
  maxSpeechLength: 600,
  /** Bounded plan size. M1 plans one route; the ceiling keeps later phases honest. */
  maxIntents: 4,
  /** Recent user turns this runtime may send to SPEC-005 for follow-up planning. */
  maxPlanReferences: 4,
  /** Longest clarification question accepted from a plan. */
  maxClarificationLength: 240,
  /** Longest entity/time expression copied from the operator's own words. */
  maxIntentSlotLength: 200,
  /** Matching sources rendered for one connected read-only answer. */
  maxEmailResults: 5,
  /** Turns retained per session before the runtime refuses to grow it further. */
  maxTurnsPerSession: 200,
  /** Hard database backstop for the per-session sequence. */
  maxSequence: 2000,
  /** Structured detail lines surfaced from the owning service. */
  maxDetails: 12,
  /** Item blocks rendered for one answer. */
  maxItems: 50,
  /** Citation selectors rendered for one answer. */
  maxCitations: 50,
  /** Clarify suggestions echoed from the owning service. */
  maxSuggestions: 10,
  /** Client referents accepted but unused in M1. */
  maxReferents: 8,
  /** Retention window for M1 question and Today answer text. */
  retentionDays: 30,
  /** Upstream request timeout. */
  requestTimeoutMs: 10_000,
  /** How long a duplicate waits for the owning request to finish. */
  claimWaitMs: 5_000,
  /** Poll interval while waiting for a claimed request to resolve. */
  claimPollMs: 25,
  /** A pending claim older than this is treated as abandoned and taken over. */
  claimStaleMs: 30_000,
  /** Sessions removed per bounded purge statement. */
  purgeBatchSize: 500,
  /** Default cadence for the bounded retention purge. */
  purgeIntervalMs: 21_600_000,
} as const;
