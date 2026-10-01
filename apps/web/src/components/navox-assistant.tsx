"use client";

import {
  initialVoiceState,
  type VoiceSessionState,
} from "@navox/assistant-runtime/voice";
import type {
  AssistantBlock,
  AssistantCitation,
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
  forgetAssistantSession,
  resumeOrCreateAssistantSession,
  submitAssistantTurn,
  transcribeAssistantSpeech,
} from "../lib/assistant-client";
import {
  canSpeakTurn,
  createAssistantTurnRunner,
  createVoiceSession,
  speechTextForTurn,
  type VoiceSession,
  voiceControls,
} from "../lib/assistant-controller";
import {
  createSpeechAdapter,
  type SpeechAdapter,
  type TranscribeSpeech,
} from "../lib/assistant-speech";
import { safeSourceUrl } from "../lib/search";
import { AssistantEmailActions } from "./assistant-email-actions";
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
    case "CLASS_NAVIGATION":
      return `class-navigation:${block.connection_id}:${block.resource_id}`;
  }
}

function blockBases(blocks: AssistantBlock[]): string[] {
  return blocks.map(blockBase);
}

export function AssistantBlockView({ block }: { block: AssistantBlock }) {
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
  sessionId,
}: {
  turn: AssistantTurnView;
  onSpeak?: (turn: AssistantTurnView) => void;
  onSelectEmail?: (turn: AssistantTurnView, resourceId: string) => void;
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
              <AssistantBlockView block={block} />
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
      {sessionId && <AssistantEmailActions sessionId={sessionId} turn={turn} />}
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

function failureMessage(error: unknown): string {
  if (error instanceof AssistantClientError) return error.message;
  if (error instanceof AssistantRequestIdError) return error.message;
  return "The assistant could not answer right now. Please try again.";
}

function browserSessionStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function NavoXAssistant() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<AssistantTurnView[]>([]);
  const [text, setText] = useState("");
  const [voice, setVoice] = useState<VoiceSessionState>(initialVoiceState);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [connecting, setConnecting] = useState(true);
  const ledger = useRef(new AssistantTurnLedger());
  const adapterRef = useRef<SpeechAdapter | null>(null);
  const voiceRef = useRef<VoiceSession | null>(null);
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
   * One voice session per page. It is created on the client, so a browser
   * without microphone support reports the typed fallback before any click.
   */
  const voiceSession = useCallback((): VoiceSession => {
    if (!voiceRef.current) {
      voiceRef.current = createVoiceSession({
        adapter: adapter(),
        transcribe,
        onState: setVoice,
        onNotice: setNotice,
        onTranscript: (request) => {
          submitTranscriptRef.current(request.text);
        },
      });
    }
    return voiceRef.current;
  }, [adapter, transcribe]);

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

  useEffect(() => {
    void openSession();
    voiceSession();
  }, [openSession, voiceSession]);

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
        },
        onNotice: (message, modality) => {
          setNotice(message);
          if (modality === "VOICE") voiceRef.current?.turnFailed(message);
        },
        onBusy: setBusy,
        // Only a live voice turn may speak. The token refuses an answer the
        // operator already stopped, muted or replaced.
        onSpeak: (speech) =>
          voiceRef.current?.speakAutomatic(speech, pendingVoiceToken.current),
        messageForError: failureMessage,
      }),
    [],
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
    (speechText: string) => voiceSession().speakManually(speechText),
    [voiceSession],
  );

  const toggleMicrophone = useCallback(() => {
    voiceSession().toggleListening();
  }, [voiceSession]);

  const toggleMute = useCallback(() => {
    voiceSession().toggleMuted();
  }, [voiceSession]);

  const stopEverything = useCallback(() => {
    voiceSession().stop();
  }, [voiceSession]);

  const clearConversation = useCallback(async () => {
    const current = sessionId;
    // Abandon in-flight work and audio before the session identity changes.
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
  }, [openSession, runner, sessionId, voiceSession]);

  useEffect(
    () => () => {
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
        removes this history right away. Recording starts only when you press
        the microphone control, and only the finished clip is sent for
        transcription.
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
                ? (target) => {
                    const speech = speechTextForTurn(target);
                    if (speech) speak(speech);
                  }
                : undefined
            }
            onSelectEmail={(source, resourceId) => {
              void sendTurn("TEXT", "Select an email", [source.id, resourceId]);
            }}
          />
        ))}
      </ol>

      {connecting && <p className={styles.status}>Starting a conversation…</p>}
      {!connecting && turns.length === 0 && (
        <p className={styles.status}>
          Ask what you are missing today, or press the microphone to record a
          question.
        </p>
      )}
      {status && (
        <p className={styles.status} data-voice-state={voice.state}>
          {status}
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
          aria-label="Stop listening and speech"
          title="Stop listening and speech"
          disabled={!controls.stopAvailable}
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
