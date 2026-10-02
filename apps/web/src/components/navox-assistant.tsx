"use client";

import {
  initialVoiceState,
  type VoiceSessionState,
} from "@navox/assistant-runtime/voice";
import type {
  AssistantBlock,
  AssistantCitation,
  AssistantGoalKind,
  AssistantGoalStatus,
  AssistantGoalView,
  AssistantSourceExcerpt,
  AssistantTurnView,
  AssistantVoiceState,
} from "@navox/contracts";
import {
  type FormEvent,
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AssistantClientError,
  AssistantRequestIdError,
  AssistantTurnLedger,
  browserTimezone,
  deleteAssistantSession,
  dispatchAssistantGoal,
  forgetAssistantSession,
  loadAssistantEvidence,
  loadAssistantGoals,
  resumeOrCreateAssistantSession,
  submitAssistantTurn,
  synthesizeAssistantSpeech,
  transcribeAssistantSpeech,
} from "../lib/assistant-client";
import {
  canSpeakTurn,
  createAssistantTurnRunner,
  createVoiceSession,
  speechStartForTurn,
  type VoiceSession,
  voiceControls,
} from "../lib/assistant-controller";
import {
  createSpeechAdapter,
  type SpeechAdapter,
  type SynthesizeSpeech,
  type TranscribeSpeech,
} from "../lib/assistant-speech";
import {
  type BrowserWakeWordAdapter,
  createBrowserWakeWordAdapter,
} from "../lib/assistant-wake";
import { safeSourceUrl } from "../lib/search";
import {
  AssistantEmailActions,
  activeDraftSpeechKey,
  type DraftSpeechRequest,
} from "./assistant-email-actions";
import styles from "./navox-assistant.module.css";

export function assistantVoiceLabel(state: AssistantVoiceState): string {
  switch (state) {
    case "LISTENING":
      return "Recording. Press the microphone again to send it, or Stop to cancel.";
    case "TRANSCRIBING":
      return "Transcribing your question.";
    case "THINKING":
      return "Checking your NavoX information.";
    case "SPEAKING":
      return "Speaking the answer. Starting a new question interrupts it.";
    case "MUTED":
      return "Spoken answers are muted. Unmute to hear the next answer.";
    case "STOPPED":
      return "Stopped. The microphone and any spoken answer are released.";
    case "UNSUPPORTED":
      return "Microphone input is unavailable in this browser. Type your question instead.";
    default:
      return "";
  }
}

function citationLabel(citation: AssistantCitation): string {
  const resource =
    citation.external_resource_id ?? citation.evidence_id ?? "record";
  return `${citation.provider} · ${citation.source_type} · ${resource}`;
}

function CitationText({ citation }: { citation: AssistantCitation }) {
  const href =
    citation.source_type === "NEWS_CLAIM"
      ? safeSourceUrl(citation.external_resource_id)
      : null;
  return href ? (
    <a
      className={styles.sourceLink}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      referrerPolicy="no-referrer"
    >
      {citationLabel(citation)}
      <span className={styles.srOnly}> (opens in a new tab)</span>
    </a>
  ) : (
    citationLabel(citation)
  );
}

/** Stable, index-free keys; identical entries still get a unique suffix. */
function uniqueKeys(bases: string[]): string[] {
  const seen = new Map<string, number>();
  return bases.map((base) => {
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    return count === 0 ? base : `${base}#${count}`;
  });
}

function citationKeys(citations: AssistantCitation[]): string[] {
  return uniqueKeys(
    citations.map(
      (citation) =>
        `${citation.provider}:${citation.source_type}:${
          citation.external_resource_id ?? citation.evidence_id ?? "record"
        }`,
    ),
  );
}

function blockBase(block: AssistantBlock): string {
  switch (block.kind) {
    case "ANSWER":
      return `answer:${block.text.slice(0, 48)}`;
    case "NOTICE":
      return `notice:${block.state}:${block.text.slice(0, 48)}`;
    case "ITEM":
      return `item:${block.item.id}`;
    case "DETAILS":
      return `details:${block.lines[0] ?? ""}`;
    case "CITATIONS":
      return `citations:${block.citations[0]?.evidence_id ?? "none"}`;
    case "SUGGESTIONS":
      return `suggestions:${block.queries[0] ?? ""}`;
    case "MEETING_BRIEFING":
      return `meeting:${block.meeting.commitment_id}`;
    case "EVIDENCE":
      return `evidence:${block.evidence_id}`;
    case "NAVIGATION":
      return `navigation:${block.evidence_id}`;
    case "CLASS_NAVIGATION":
      return `class-navigation:${block.connection_id}:${block.resource_id}`;
  }
}

function blockBases(blocks: AssistantBlock[]): string[] {
  return blocks.map(blockBase);
}

interface EvidenceState {
  status: "loading" | "ready" | "unavailable";
  excerpts: AssistantSourceExcerpt[];
  message: string | null;
}

/**
 * Re-reads one saved evidence selector and renders the current, re-authorized
 * excerpts. The durable turn holds selectors and version metadata only, so a
 * revoked or changed source removes its content instead of replaying it.
 */
function AssistantEvidenceBlock({
  sessionId,
  turnId,
  block,
}: {
  sessionId?: string;
  turnId?: string;
  block: Extract<AssistantBlock, { kind: "EVIDENCE" }>;
}) {
  const [state, setState] = useState<EvidenceState>({
    status: "loading",
    excerpts: [],
    message: null,
  });

  useEffect(() => {
    if (!sessionId || !turnId) {
      setState({
        status: "unavailable",
        excerpts: [],
        message: "Reopen this conversation to load the saved source.",
      });
      return;
    }
    const controller = new AbortController();
    setState({ status: "loading", excerpts: [], message: null });
    void (async () => {
      try {
        const evidence = await loadAssistantEvidence(
          sessionId,
          turnId,
          block.evidence_id,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setState({
          status: "ready",
          excerpts: evidence.excerpts,
          message: null,
        });
      } catch (error) {
        if (controller.signal.aborted) return;
        setState({
          status: "unavailable",
          excerpts: [],
          message:
            error instanceof Error
              ? error.message
              : "The saved source is no longer available.",
        });
      }
    })();
    return () => controller.abort();
  }, [sessionId, turnId, block.evidence_id]);

  if (state.status === "loading") {
    return (
      <p className={styles.itemMeta} role="status">
        Loading the saved source...
      </p>
    );
  }
  if (state.status === "unavailable") {
    return (
      <p className={styles.notice} data-state="UNAVAILABLE">
        {state.message ?? "The saved source is no longer available."}
      </p>
    );
  }
  const excerptKeys = uniqueKeys(
    state.excerpts.map(
      (excerpt) => `${excerpt.source}:${excerpt.text.slice(0, 48)}`,
    ),
  );
  return (
    <ul className={styles.evidence}>
      {state.excerpts.map((excerpt, index) => (
        <li key={excerptKeys[index] ?? excerpt.source}>
          <span className={styles.evidenceSource}>
            {excerpt.source === "subject" ? "Subject" : "Content"}
          </span>{" "}
          {excerpt.text}
        </li>
      ))}
    </ul>
  );
}

export function AssistantBlockView({
  block,
  sessionId,
  turnId,
}: {
  block: AssistantBlock;
  sessionId?: string;
  turnId?: string;
}) {
  switch (block.kind) {
    case "ANSWER":
      return <p className={styles.answer}>{block.text}</p>;
    case "CLASS_NAVIGATION":
      return (
        <a
          className={styles.sourceLink}
          href={`/api/v1/assistant/class-navigation?connection_id=${encodeURIComponent(block.connection_id)}&resource_id=${encodeURIComponent(block.resource_id)}`}
          target="_blank"
          rel="noopener noreferrer"
          referrerPolicy="no-referrer"
        >
          {block.label}
          <span className={styles.srOnly}> (opens in a new tab)</span>
        </a>
      );
    case "NOTICE":
      return (
        <p className={styles.notice} data-state={block.state}>
          {block.text}
        </p>
      );
    case "ITEM":
      return (
        <article className={styles.item}>
          <p className={styles.itemTitle}>
            {block.item.type === "NEWS_STORY" ? (
              <a
                className={styles.sourceLink}
                href={`/news/stories/${encodeURIComponent(block.item.id)}`}
              >
                {block.item.title}
              </a>
            ) : (
              block.item.title
            )}
          </p>
          <p className={styles.itemMeta}>
            <span>{block.item.type}</span>
            <span aria-hidden="true">·</span>
            <span>{block.item.status}</span>
            {block.item.band && (
              <>
                <span aria-hidden="true">·</span>
                <span>{block.item.band}</span>
              </>
            )}
          </p>
          {block.item.description && (
            <p className={styles.itemBody}>{block.item.description}</p>
          )}
          {block.item.due_at && (
            <p className={styles.itemBody}>Due {block.item.due_at}</p>
          )}
          {block.item.sources.length > 0 && (
            <ul className={styles.citations}>
              {block.item.sources.map((citation, index) => (
                <li
                  key={
                    citationKeys(block.item.sources)[index] ?? citation.provider
                  }
                >
                  <CitationText citation={citation} />
                </li>
              ))}
            </ul>
          )}
        </article>
      );
    case "DETAILS":
      return (
        <ul className={styles.details}>
          {block.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      );
    case "EVIDENCE": {
      return (
        <AssistantEvidenceBlock
          sessionId={sessionId}
          turnId={turnId}
          block={block}
        />
      );
    }
    case "NAVIGATION":
      return (
        <a
          className={styles.navigationLink}
          href={block.href}
          target="_blank"
          rel="noopener noreferrer"
          referrerPolicy="no-referrer"
        >
          {block.label}
          <span className={styles.srOnly}> (opens in a new tab)</span>
        </a>
      );
    case "CITATIONS": {
      const keys = citationKeys(block.citations);
      return (
        <ul className={styles.citations}>
          {block.citations.map((citation, index) => (
            <li key={keys[index] ?? citation.provider}>
              <CitationText citation={citation} />
            </li>
          ))}
        </ul>
      );
    }
    case "SUGGESTIONS":
      return (
        <ul className={styles.details}>
          {block.queries.map((query) => (
            <li key={query}>{query}</li>
          ))}
        </ul>
      );
    case "MEETING_BRIEFING": {
      const prepPointKeys = uniqueKeys(block.meeting.prep_points);
      return (
        <article className={styles.item} aria-label="Meeting preparation">
          <p className={styles.itemTitle}>{block.meeting.title}</p>
          <p className={styles.itemMeta}>
            Starts in about {block.meeting.minutes_until} minutes ·{" "}
            {new Date(block.meeting.starts_at).toUTCString()}
          </p>
          {block.meeting.description && (
            <p className={styles.itemBody}>{block.meeting.description}</p>
          )}
          <ul className={styles.details}>
            {block.meeting.prep_points.map((point, index) => (
              <li key={prepPointKeys[index] ?? point}>{point}</li>
            ))}
          </ul>
          {block.meeting.related_commitments.length > 0 && (
            <>
              <p className={styles.itemMeta}>Related commitments</p>
              <ul className={styles.details}>
                {block.meeting.related_commitments.map((related) => (
                  <li key={related.id}>
                    {related.title} · {related.status}
                  </li>
                ))}
              </ul>
            </>
          )}
        </article>
      );
    }
  }
}

export function AssistantTurnViewBlock({
  turn,
  onSpeak,
  onSelectEmail,
  onSpeakDraft,
  onStopDraftSpeech,
  draftSpeakingKey,
  sessionId,
}: {
  turn: AssistantTurnView;
  onSpeak?: (turn: AssistantTurnView) => void;
  onSelectEmail?: (turn: AssistantTurnView, resourceId: string) => void;
  onSpeakDraft?: (request: DraftSpeechRequest) => void;
  onStopDraftSpeech?: () => void;
  draftSpeakingKey?: string | null;
  sessionId?: string;
}) {
  const blocks = turn.presentation?.blocks ?? [];
  const keys = uniqueKeys(blockBases(blocks));
  const speakable = canSpeakTurn(turn);
  return (
    <li
      className={styles.turn}
      data-modality={turn.modality}
      data-state={turn.state}
    >
      <p className={styles.question}>{turn.question}</p>
      {turn.presentation ? (
        <div className={styles.blocks}>
          {blocks.map((block, index) => (
            <Fragment key={keys[index] ?? "block"}>
              <AssistantBlockView
                block={block}
                sessionId={sessionId}
                turnId={turn.id}
              />
              {onSelectEmail &&
                turn.state === "CLARIFY" &&
                turn.decision?.reason === "email.search.ambiguous" &&
                block.kind === "ITEM" &&
                block.item.type === "EMAIL" && (
                  <button
                    type="button"
                    className={styles.selectEmail}
                    aria-label={`Select email: ${block.item.title} (${block.item.id})`}
                    onClick={() => onSelectEmail(turn, block.item.id)}
                  >
                    Select this email
                  </button>
                )}
            </Fragment>
          ))}
        </div>
      ) : null}
      {onSpeak && speakable ? (
        <button
          type="button"
          className={styles.speakButton}
          onClick={() => onSpeak(turn)}
          aria-label={`Speak the answer to "${turn.question}"`}
          title="Speak the answer"
        >
          <SpeakerIcon />
          <span className={styles.srOnly}>Speak</span>
        </button>
      ) : null}
      {sessionId && (
        <AssistantEmailActions
          sessionId={sessionId}
          turn={turn}
          speakDraft={onSpeakDraft}
          stopDraftSpeech={onStopDraftSpeech}
          speakingKey={draftSpeakingKey}
        />
      )}
    </li>
  );
}

function MicIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      focusable="false"
    >
      <path
        d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3Zm-7 9a7 7 0 0 0 6 6.93V21h2v-2.07A7 7 0 0 0 19 12h-2a5 5 0 0 1-10 0H5Z"
        fill="currentColor"
      />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      focusable="false"
    >
      <rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" />
    </svg>
  );
}

function SpeakerIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      focusable="false"
    >
      <path
        d="M4 9h3l4-3.5v13L7 15H4V9Zm12.5 3a4.5 4.5 0 0 0-2-3.74v7.48A4.5 4.5 0 0 0 16.5 12Z"
        fill="currentColor"
      />
    </svg>
  );
}

function VolumeIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      focusable="false"
    >
      <path
        d="M4 9h3l4-3.5v13L7 15H4V9Zm11 3a3 3 0 0 0-1.5-2.6v5.2A3 3 0 0 0 15 12Z"
        fill="currentColor"
      />
      <path
        d="M15.5 5.6v2.1a6 6 0 0 1 0 8.6v2.1a8 8 0 0 0 0-12.8Z"
        fill="currentColor"
      />
    </svg>
  );
}

function MutedIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      focusable="false"
    >
      <path d="M4 9h3l4-3.5v13L7 15H4V9Z" fill="currentColor" />
      <path d="M3.6 2.2 20.4 19l-1.4 1.4L2.2 3.6Z" fill="currentColor" />
    </svg>
  );
}

/** Voice Mode is a preference, so its on and off shapes read differently. */
function VoiceModeIcon({ enabled }: { enabled: boolean }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
    >
      <path d="M4 4.75h16v11.5H9.5L5 19.5V4.75Z" />
      {enabled ? (
        <path d="M9.5 9.5v3M12 8.5v5M14.5 9.5v3" />
      ) : (
        <path d="M4.5 3.5 19.5 20" />
      )}
    </svg>
  );
}

function failureMessage(error: unknown): string {
  if (error instanceof AssistantClientError) return error.message;
  if (error instanceof AssistantRequestIdError) return error.message;
  return "The assistant could not answer right now. Please try again.";
}

/** Operator-facing labels for a goal's truthful state; no raw code is shown. */
export const GOAL_KIND_LABELS: Record<AssistantGoalKind, string> = {
  BRIEFING: "Briefing",
  MEETING_PREP: "Meeting prep",
  COMMUNICATION_ACTION: "Communication action",
};

export const GOAL_STATUS_LABELS: Record<AssistantGoalStatus, string> = {
  PENDING: "Not verified yet",
  RUNNING: "In progress",
  WAITING_FOR_USER: "Waiting for you",
  WAITING_FOR_EXTERNAL: "Waiting for verification",
  COMPLETED: "Verified",
  FAILED: "Not verified",
};

const GOAL_DETAIL_TEXT: Record<string, string> = {
  "goal.created": "Queued for verification.",
  "goal.dispatch_unconfigured":
    "Durable verification is not configured in this deployment.",
  "goal.dispatch_failed": "Verification could not start.",
  "goal.dispatched": "Verification is running.",
  "goal.verified": "Confirmed by the owning service.",
  "goal.turn_not_grounded": "The saved answer was not a grounded result.",
  "goal.turn_unreadable": "The saved answer could not be re-validated.",
  "goal.action_waiting_for_approval": "Waiting for your approval.",
  "goal.action_in_flight": "The action is in progress.",
  "goal.action_executed_unverified":
    "Executed, waiting for independent verification.",
  "goal.action_failed": "The action did not complete.",
  "goal.action_uncertain": "The outcome is uncertain.",
  "goal.action_ledger_inconsistent": "The action record could not be verified.",
  "goal.action_status_unrecognized": "The action status could not be verified.",
  "goal.verification_deadline_reached":
    "Verification timed out before an outcome was recorded.",
  "goal.window_ended_waiting_for_user":
    "Verification paused while waiting for your approval.",
  "goal.window_ended_waiting_for_external":
    "Verification paused while waiting for an external result.",
  "goal.window_ended_in_flight":
    "Verification paused while the action was still in progress.",
};

function goalDetailText(detail: string | null): string | null {
  if (!detail) return null;
  return GOAL_DETAIL_TEXT[detail] ?? null;
}

/**
 * The bounded, per-session goal states. It shows only what the server
 * recorded: a goal is never described as verified here unless the owning
 * service verified it.
 */
function AssistantGoalList({
  goals,
  busy,
  onRetry,
}: {
  goals: readonly AssistantGoalView[];
  busy: boolean;
  onRetry: (goalId: string) => void;
}) {
  if (goals.length === 0) return null;
  return (
    <ul className={styles.goals} aria-label="Verification goals">
      {goals.map((goal) => (
        <li
          key={goal.id}
          className={styles.goal}
          data-goal-status={goal.status}
        >
          <span className={styles.goalKind}>{GOAL_KIND_LABELS[goal.kind]}</span>
          <span className={styles.goalStatus}>
            {GOAL_STATUS_LABELS[goal.status]}
          </span>
          {goalDetailText(goal.detail) && (
            <span className={styles.goalDetail}>
              {goalDetailText(goal.detail)}
            </span>
          )}
          {(goal.dispatch_state === "DISPATCH_FAILED" ||
            goal.detail?.startsWith("goal.window_ended_")) && (
            <button
              type="button"
              className={styles.goalRetry}
              onClick={() => onRetry(goal.id)}
              disabled={busy}
            >
              Retry verification
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function browserSessionStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function NavoXAssistant() {
  type HandsFreePhase =
    | "OFF"
    | "PREPARING"
    | "WAKE_LISTENING"
    | "CONVERSATION"
    | "PAUSED";
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<AssistantTurnView[]>([]);
  const [goals, setGoals] = useState<AssistantGoalView[]>([]);
  const [text, setText] = useState("");
  const [voice, setVoice] = useState<VoiceSessionState>(initialVoiceState);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [connecting, setConnecting] = useState(true);
  const [handsFreePhase, setHandsFreePhase] = useState<HandsFreePhase>("OFF");
  const [speakingDraftKey, setSpeakingDraftKey] = useState<string | null>(null);
  const ledger = useRef(new AssistantTurnLedger());
  const adapterRef = useRef<SpeechAdapter | null>(null);
  const voiceRef = useRef<VoiceSession | null>(null);
  const wakeRef = useRef<BrowserWakeWordAdapter | null>(null);
  const handsFreeEnabledRef = useRef(false);
  const wakeGenerationRef = useRef(0);
  const submitTranscriptRef = useRef<(transcript: string) => void>(() => {});
  const pendingVoiceToken = useRef(0);
  const sessionIdRef = useRef<string | null>(null);

  const adapter = useCallback((): SpeechAdapter => {
    if (!adapterRef.current) {
      adapterRef.current = createSpeechAdapter(
        typeof window === "undefined" ? {} : window,
      );
    }
    return adapterRef.current;
  }, []);

  /**
   * The page binds the session to the clip upload. The adapter owns the abort
   * signal, so an unset session is a typed failure rather than a request.
   */
  const transcribe = useCallback<TranscribeSpeech>((wav, signal) => {
    const session = sessionIdRef.current;
    if (!session) {
      return Promise.reject(
        new Error("Start a conversation before recording. Type your question."),
      );
    }
    return transcribeAssistantSpeech(session, wav, signal);
  }, []);

  /**
   * The page binds the session to saved-turn speech. The adapter owns the
   * abort signal, so an unset session is a typed failure rather than a request.
   */
  const synthesize = useCallback<SynthesizeSpeech>((turnId, signal) => {
    const session = sessionIdRef.current;
    if (!session) {
      return Promise.reject(
        new Error("Start a conversation before playing an answer."),
      );
    }
    return synthesizeAssistantSpeech(session, turnId, signal);
  }, []);

  /**
   * One voice session per page. It is created on the client, so a browser
   * without microphone support reports the typed fallback before any click.
   */
  const voiceSession = useCallback((): VoiceSession => {
    if (!voiceRef.current) {
      voiceRef.current = createVoiceSession({
        adapter: adapter(),
        transcribe,
        synthesize,
        onState: setVoice,
        onNotice: setNotice,
        onTranscript: (request) => {
          submitTranscriptRef.current(request.text);
        },
      });
    }
    return voiceRef.current;
  }, [adapter, synthesize, transcribe]);

  const openSession = useCallback(async () => {
    setConnecting(true);
    try {
      const session = await resumeOrCreateAssistantSession(
        browserSessionStorage(),
      );
      setSessionId(session.id);
      setTurns(session.turns);
      setNotice(null);
    } catch (error) {
      setNotice(failureMessage(error));
      setSessionId(null);
    } finally {
      setConnecting(false);
    }
  }, []);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  /**
   * The server owns each goal's state. This only re-reads it, and a failed
   * refresh leaves the last known state on screen instead of inventing a new
   * one.
   */
  const refreshGoals = useCallback(async () => {
    const session = sessionIdRef.current;
    if (!session) return;
    try {
      setGoals(await loadAssistantGoals(session));
    } catch {
      // Leave the last known state; the next recorded turn refreshes it.
    }
  }, []);

  useEffect(() => {
    void openSession();
    voiceSession();
  }, [openSession, voiceSession]);

  useEffect(() => {
    if (!sessionId) return;
    void refreshGoals();
    const refreshTimer = window.setInterval(() => {
      void refreshGoals();
    }, 30_000);
    return () => window.clearInterval(refreshTimer);
  }, [refreshGoals, sessionId]);

  const runner = useMemo(
    () =>
      createAssistantTurnRunner({
        submit: submitAssistantTurn,
        ledger: ledger.current,
        timezone: browserTimezone,
        onTurn: (turn) => {
          setTurns((previous) => [...previous, turn]);
          setText("");
          if (turn.modality === "VOICE") voiceRef.current?.answerReady();
          // A recorded turn may have created its bounded goal.
          void refreshGoals();
        },
        onNotice: (message, modality) => {
          setNotice(message);
          if (modality === "VOICE") voiceRef.current?.turnFailed(message);
        },
        onBusy: setBusy,
        // Only a live voice turn may speak. The token refuses an answer the
        // operator already stopped, muted or replaced.
        onSpeak: (turn, explicit) => {
          const session = voiceRef.current;
          if (!session) return;
          const start = speechStartForTurn(session.current(), explicit);
          if (start === "MANUAL") session.speakManually(turn);
          if (start === "AUTOMATIC")
            session.speakAutomatic(turn, pendingVoiceToken.current);
        },
        messageForError: failureMessage,
      }),
    [refreshGoals],
  );

  const sendTurn = useCallback(
    async (
      modality: "TEXT" | "VOICE",
      question: string,
      referents: string[] = [],
    ) => {
      const trimmed = question.trim();
      if (!sessionId || !trimmed || busy) return;
      setNotice(null);
      if (modality === "VOICE") {
        // Open the turn before the request so its answer carries this token.
        pendingVoiceToken.current = voiceSession().beginTurn();
        voiceSession().markSubmitted();
      }
      await runner.run({ sessionId, modality, text: trimmed, referents });
    },
    [busy, runner, sessionId, voiceSession],
  );

  useEffect(() => {
    // The adapter owns the recognition callbacks; the page owns the submit.
    submitTranscriptRef.current = (transcript: string) => {
      void sendTurn("VOICE", transcript);
    };
  }, [sendTurn]);

  const retryGoal = useCallback(async (goalId: string) => {
    try {
      const result = await dispatchAssistantGoal(goalId);
      setGoals((previous) =>
        previous.map((goal) => (goal.id === goalId ? result.goal : goal)),
      );
      if (!result.dispatched)
        setNotice("Verification could not be restarted right now.");
    } catch (error) {
      setNotice(failureMessage(error));
    }
  }, []);

  const startWakeListening = useCallback(async () => {
    if (!handsFreeEnabledRef.current || !sessionIdRef.current) return;
    if (typeof document !== "undefined" && document.hidden) {
      setHandsFreePhase("PAUSED");
      return;
    }
    const generation = ++wakeGenerationRef.current;
    setHandsFreePhase("PREPARING");
    await wakeRef.current?.stop();
    if (generation !== wakeGenerationRef.current) return;
    const wake = createBrowserWakeWordAdapter({
      onError: (message) => {
        if (generation !== wakeGenerationRef.current) return;
        handsFreeEnabledRef.current = false;
        setHandsFreePhase("OFF");
        setNotice(message);
      },
    });
    wakeRef.current = wake;
    if (!wake.supported || !adapter().captureSupported) {
      handsFreeEnabledRef.current = false;
      setHandsFreePhase("OFF");
      setNotice(
        "Hands-Free needs on-device wake recognition and a microphone. Use the microphone button or type instead.",
      );
      return;
    }
    try {
      await wake.start((event) => {
        if (
          !handsFreeEnabledRef.current ||
          generation !== wakeGenerationRef.current
        )
          return;
        setHandsFreePhase("CONVERSATION");
        voiceSession().resumeForWake();
        voiceSession().setVoiceMode(true);
        // The local wake detector can include a same-utterance suffix. It is
        // ordinary question text, never an approval or action authority.
        submitTranscriptRef.current(event.request_text ?? "Hey NavoX");
      });
      if (
        handsFreeEnabledRef.current &&
        generation === wakeGenerationRef.current &&
        wake.active
      )
        setHandsFreePhase("WAKE_LISTENING");
    } catch (error) {
      if (generation !== wakeGenerationRef.current) return;
      handsFreeEnabledRef.current = false;
      setHandsFreePhase("OFF");
      setNotice(
        error instanceof Error
          ? error.message
          : "Hands-Free could not start on this device.",
      );
    }
  }, [adapter, voiceSession]);

  const disableHandsFree = useCallback(() => {
    handsFreeEnabledRef.current = false;
    wakeGenerationRef.current += 1;
    void wakeRef.current?.stop();
    voiceRef.current?.stop();
    setHandsFreePhase("OFF");
  }, []);

  const toggleHandsFree = useCallback(() => {
    if (handsFreeEnabledRef.current) {
      disableHandsFree();
      return;
    }
    handsFreeEnabledRef.current = true;
    void startWakeListening();
  }, [disableHandsFree, startWakeListening]);

  useEffect(() => {
    if (!handsFreeEnabledRef.current || handsFreePhase !== "CONVERSATION")
      return;
    if (voice.state === "SPEAKING") {
      voiceSession().armBargeIn();
    } else if ((voice.state === "IDLE" || voice.state === "MUTED") && !busy) {
      voiceSession().listenAutomatically();
    } else if (voice.state === "STOPPED" && !busy) {
      void startWakeListening();
    }
  }, [busy, handsFreePhase, startWakeListening, voice.state, voiceSession]);

  useEffect(() => {
    const onVisibility = () => {
      if (!handsFreeEnabledRef.current) return;
      if (document.hidden) {
        wakeGenerationRef.current += 1;
        void wakeRef.current?.stop();
        voiceRef.current?.stop();
        setHandsFreePhase("PAUSED");
      } else {
        void startWakeListening();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [startWakeListening]);

  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      void sendTurn("TEXT", text);
    },
    [sendTurn, text],
  );

  const controls = voiceControls(voice);
  /** The microphone control never offers a second finish during an upload. */
  const microphoneLabel = controls.transcribing
    ? "Transcribing the recording"
    : controls.capturing
      ? "Finish recording and transcribe"
      : "Start recording";

  /** The explicit Read aloud control exists only while the session is unmuted. */
  const speak = useCallback(
    (target: AssistantTurnView) => voiceSession().speakManually(target),
    [voiceSession],
  );

  /**
   * Explicit read-aloud of one saved draft version. The component binds the
   * selector to its own synthesize closure; the server still derives the text,
   * and mute, Stop and unmount cancel playback through the same voice session.
   */
  const speakDraft = useCallback(
    (request: DraftSpeechRequest) => {
      setSpeakingDraftKey(request.key);
      voiceSession().speakSaved({
        synthesize: request.synthesize,
        onEnd: () => {
          setSpeakingDraftKey((key) => (key === request.key ? null : key));
          request.onEnd();
        },
        onError: (reason) => {
          setSpeakingDraftKey((key) => (key === request.key ? null : key));
          request.onError(reason);
        },
      });
    },
    [voiceSession],
  );

  /**
   * The voice controller owns playback. A global Stop, Mute, barge-in, a
   * finished read or a failed start leaves nothing to claim as speaking, so the
   * draft control can never keep showing "Stop reading" on its own.
   */
  useEffect(() => {
    setSpeakingDraftKey((key) => activeDraftSpeechKey(key, controls.speaking));
  }, [controls.speaking]);

  const stopDraftSpeech = useCallback(() => {
    voiceRef.current?.cancelSpeech();
    setSpeakingDraftKey(null);
  }, []);

  const toggleMicrophone = useCallback(() => {
    if (handsFreeEnabledRef.current && handsFreePhase === "WAKE_LISTENING") {
      wakeGenerationRef.current += 1;
      void wakeRef.current?.stop();
      setHandsFreePhase("CONVERSATION");
    }
    voiceSession().toggleListening();
  }, [handsFreePhase, voiceSession]);

  const toggleMute = useCallback(() => {
    voiceSession().toggleMuted();
  }, [voiceSession]);

  const toggleVoiceMode = useCallback(() => {
    voiceSession().setVoiceMode(!voiceSession().current().voiceMode);
  }, [voiceSession]);

  const stopEverything = useCallback(() => {
    voiceSession().stop();
  }, [voiceSession]);

  const clearConversation = useCallback(async () => {
    const current = sessionId;
    // Abandon in-flight work and audio before the session identity changes.
    disableHandsFree();
    runner.invalidate();
    voiceRef.current?.dispose();
    voiceRef.current = null;
    setTurns([]);
    setText("");
    setNotice(null);
    setVoice(initialVoiceState());
    // A fresh session reports an unsupported browser again and refuses any
    // callback still in flight from the cleared one.
    voiceSession();
    forgetAssistantSession(browserSessionStorage());
    if (!current) {
      await openSession();
      return;
    }
    try {
      await deleteAssistantSession(current);
    } catch (error) {
      setNotice(failureMessage(error));
    }
    await openSession();
  }, [disableHandsFree, openSession, runner, sessionId, voiceSession]);

  useEffect(
    () => () => {
      handsFreeEnabledRef.current = false;
      wakeGenerationRef.current += 1;
      void wakeRef.current?.stop();
      runner.invalidate();
      voiceRef.current?.dispose();
      voiceRef.current = null;
    },
    [runner],
  );

  const status = assistantVoiceLabel(voice.state);

  return (
    <section className={styles.assistant} aria-label="NavoX assistant">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>NAVOX / ASSISTANT</p>
          <h1>Ask about your day</h1>
        </div>
        <button
          type="button"
          onClick={() => void clearConversation()}
          disabled={connecting}
        >
          Clear conversation
        </button>
      </header>

      <p className={styles.policy}>
        Answers use your saved Today state and connected sources you can access.
        Questions and answers expire after 30 days of session access, and
        expired rows are purged on a bounded schedule. Clear conversation
        removes this history right away. The microphone records after you press
        it or explicitly enable Hands-Free. Hands-Free wake detection stays on
        this device while this page is open; finished question clips go to the
        NavoX speech gateway for transcription.
      </p>

      {notice && (
        <p className={styles.error} role="alert">
          {notice}
        </p>
      )}

      <ol className={styles.transcript} aria-live="polite">
        {turns.map((turn) => (
          <AssistantTurnViewBlock
            key={turn.id}
            turn={turn}
            sessionId={sessionId ?? undefined}
            onSpeak={
              controls.readAloudAvailable
                ? (target) => speak(target)
                : undefined
            }
            onSelectEmail={(source, resourceId) => {
              void sendTurn("TEXT", "Select an email", [source.id, resourceId]);
            }}
            onSpeakDraft={controls.readAloudAvailable ? speakDraft : undefined}
            onStopDraftSpeech={stopDraftSpeech}
            draftSpeakingKey={speakingDraftKey}
          />
        ))}
      </ol>

      <AssistantGoalList
        goals={goals}
        busy={busy}
        onRetry={(goalId) => {
          void retryGoal(goalId);
        }}
      />

      {connecting && <p className={styles.status}>Starting a conversation…</p>}
      {!connecting && turns.length === 0 && (
        <p className={styles.status}>
          Ask what you are missing today, or press the microphone to record a
          question.
        </p>
      )}
      {handsFreePhase !== "OFF" && (
        <p className={styles.status} data-hands-free-state={handsFreePhase}>
          {handsFreePhase === "WAKE_LISTENING"
            ? 'Hands-Free on · waiting for "Hey NavoX".'
            : handsFreePhase === "PREPARING"
              ? "Preparing on-device wake recognition…"
              : handsFreePhase === "PAUSED"
                ? "Hands-Free paused while this page is hidden."
                : "Hands-Free conversation active · microphone and speech state are shown below."}
        </p>
      )}
      {status && (
        <p className={styles.status} data-voice-state={voice.state}>
          {handsFreePhase === "CONVERSATION" && voice.state === "LISTENING"
            ? "Listening for your next question. Speak naturally, or press Stop."
            : status}
        </p>
      )}
      {!controls.voiceModeEnabled && (
        <p className={styles.status} data-voice-mode="off">
          Voice Mode is off. Answers are shown on screen; press Read aloud on
          one to hear it.
        </p>
      )}

      <form className={styles.form} onSubmit={onSubmit}>
        <label className={styles.field}>
          <span>Question</span>
          <input
            type="text"
            value={text}
            maxLength={500}
            placeholder="What am I missing today?"
            onChange={(event) => setText(event.target.value)}
            disabled={connecting || !sessionId}
          />
        </label>
        <button
          type="submit"
          disabled={connecting || !sessionId || busy || !text.trim()}
        >
          {busy ? "Checking…" : "Send"}
        </button>
        <button
          type="button"
          onClick={toggleHandsFree}
          aria-pressed={handsFreePhase !== "OFF"}
          aria-label={
            handsFreePhase === "OFF"
              ? "Turn Hands-Free on"
              : "Turn Hands-Free off"
          }
          disabled={
            connecting || !sessionId || (handsFreePhase === "OFF" && busy)
          }
        >
          {handsFreePhase === "OFF" ? "Hands-Free On" : "Hands-Free Off"}
        </button>
        <button
          type="button"
          className={styles.iconButton}
          onClick={toggleMicrophone}
          aria-pressed={controls.listening}
          aria-label={microphoneLabel}
          title={microphoneLabel}
          disabled={
            connecting ||
            !sessionId ||
            busy ||
            controls.microphoneDisabled ||
            controls.transcribing
          }
        >
          {controls.capturing ? <StopIcon /> : <MicIcon />}
        </button>
        <button
          type="button"
          className={styles.iconButton}
          onClick={toggleVoiceMode}
          aria-pressed={controls.voiceModeEnabled}
          aria-label={
            controls.voiceModeEnabled
              ? "Turn Voice Mode off; answers stay on screen"
              : "Turn Voice Mode on; spoken questions are answered aloud"
          }
          title={
            controls.voiceModeEnabled
              ? "Voice Mode on — spoken questions are answered aloud"
              : "Voice Mode off — answers stay on screen"
          }
          disabled={connecting || !sessionId}
        >
          <VoiceModeIcon enabled={controls.voiceModeEnabled} />
        </button>
        <button
          type="button"
          className={styles.iconButton}
          onClick={toggleMute}
          aria-pressed={controls.muted}
          aria-label={
            controls.muted ? "Unmute spoken answers" : "Mute spoken answers"
          }
          title={
            controls.muted ? "Unmute spoken answers" : "Mute spoken answers"
          }
          disabled={connecting || !sessionId}
        >
          {controls.muted ? <MutedIcon /> : <VolumeIcon />}
        </button>
        <button
          type="button"
          className={styles.iconButton}
          onClick={stopEverything}
          aria-label={
            handsFreePhase === "CONVERSATION"
              ? "End conversation and return to wake listening"
              : "Stop listening and speech"
          }
          title={
            handsFreePhase === "CONVERSATION"
              ? "End conversation and return to wake listening"
              : "Stop listening and speech"
          }
          disabled={
            !controls.stopAvailable && handsFreePhase !== "CONVERSATION"
          }
        >
          <SpeakerIcon />
          <span className={styles.srOnly}>Stop</span>
        </button>
      </form>

      {voice.state === "UNSUPPORTED" && (
        <p className={styles.status}>
          Typed input is fully available; the microphone is not.
        </p>
      )}
    </section>
  );
}
