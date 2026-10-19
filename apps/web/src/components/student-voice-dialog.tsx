"use client";

import {
  initialVoiceState,
  type VoiceSessionState,
} from "@navox/assistant-runtime/voice";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteAssistantSession,
  MAX_SPEECH_AUDIO_BYTES,
  newAssistantRequestId,
  transcribeAssistantSpeech,
} from "../lib/assistant-client";
import {
  createVoiceSession,
  type VoiceSession,
} from "../lib/assistant-controller";
import { createSpeechAdapter } from "../lib/assistant-speech";
import {
  createStudentPushToTalk,
  type StudentQuickAskAnswer,
  studentClassDraftKey,
  studentVoiceRequest,
} from "../lib/student-voice";
import styles from "./student-voice-launcher.module.css";

function Mic() {
  return (
    <svg
      width="19"
      height="19"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <rect
        x="9"
        y="2"
        width="6"
        height="13"
        rx="3"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <path
        d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}

async function privateSpeech(
  answerId: string,
  sessionId: string,
  signal: AbortSignal,
): Promise<Uint8Array<ArrayBuffer>> {
  const response = await fetch(
    `/api/v1/student-voice/answers/${encodeURIComponent(answerId)}/speech`,
    {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
      signal,
    },
  );
  if (!response.ok) {
    const data = (await response.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    throw new Error(
      data?.error?.message ??
        "Spoken answers are unavailable. Read the answer on screen.",
    );
  }
  if (
    response.headers.get("content-type")?.split(";")[0] !== "audio/mpeg" ||
    !response.body
  )
    throw new Error("This spoken answer could not be played.");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    if (!value) continue;
    size += value.length;
    if (size > MAX_SPEECH_AUDIO_BYTES) {
      await reader.cancel();
      throw new Error("This spoken answer is too large. Read it on screen.");
    }
    chunks.push(value);
  }
  if (!size) throw new Error("This spoken answer could not be played.");
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}

export function StudentQuickAsk({
  accountKey,
  onClose,
}: {
  accountKey?: string;
  onClose(): void;
}) {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const ownerRef = useRef<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<StudentQuickAskAnswer | null>(null);
  const answerRef = useRef<StudentQuickAskAnswer | null>(null);
  const [save, setSave] = useState(false);
  const [busy, setBusy] = useState(false);
  const [checkingOwner, setCheckingOwner] = useState(false);
  const checkingOwnerRef = useRef(false);
  const [error, setError] = useState("");
  const [voice, setVoice] = useState<VoiceSessionState>(initialVoiceState);
  const voiceRef = useRef<VoiceSession | null>(null);
  const sendRef = useRef<
    (text: string, modality: "TEXT" | "VOICE") => Promise<void>
  >(async () => {});
  const requestRef = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const savedRef = useRef(false);

  const forgetAnswer = useCallback(
    (current: StudentQuickAskAnswer | null, session: string | null) => {
      if (current?.receipt_id && session)
        void studentVoiceRequest(
          `/api/v1/student-voice/answers/${encodeURIComponent(current.receipt_id)}`,
          "DELETE",
          { session_id: session },
        ).catch(() => {});
    },
    [],
  );

  const stop = useCallback(() => {
    generation.current += 1;
    requestRef.current?.abort();
    voiceRef.current?.stop();
    setBusy(false);
  }, []);

  const clearPrivateView = useCallback(() => {
    stop();
    forgetAnswer(answerRef.current, sessionRef.current);
    answerRef.current = null;
    setAnswer(null);
    setQuestion("");
  }, [forgetAnswer, stop]);

  const speak = useCallback((current: StudentQuickAskAnswer) => {
    const session = sessionRef.current;
    if (!session || !current.receipt_id) return;
    voiceRef.current?.speakSaved({
      synthesize: (signal) =>
        current.receipt_id
          ? privateSpeech(current.receipt_id, session, signal)
          : Promise.reject(new Error("Ask again to hear this answer.")),
      onEnd: () => {},
      onError: setError,
    });
  }, []);

  const send = useCallback(
    async (raw: string, modality: "TEXT" | "VOICE") => {
      const text = raw.trim();
      const session = sessionRef.current;
      if (
        !session ||
        !text ||
        text.length > 500 ||
        busy ||
        checkingOwnerRef.current
      )
        return;
      const token = ++generation.current;
      requestRef.current?.abort();
      const controller = new AbortController();
      requestRef.current = controller;
      forgetAnswer(answerRef.current, session);
      answerRef.current = null;
      setAnswer(null);
      setQuestion(text);
      setError("");
      setBusy(true);
      if (modality === "VOICE") {
        voiceRef.current?.beginTurn();
        voiceRef.current?.markSubmitted();
      }
      try {
        const next = await studentVoiceRequest<StudentQuickAskAnswer>(
          "/api/v1/student-voice/ask",
          "POST",
          {
            session_id: session,
            request_id: newAssistantRequestId(),
            text,
            modality,
            save,
          },
          controller.signal,
        );
        if (token !== generation.current) return;
        answerRef.current = next;
        setAnswer(next);
        savedRef.current ||= next.saved;
        voiceRef.current?.answerReady();
        if (modality === "VOICE") speak(next);
      } catch (cause) {
        if (token !== generation.current) return;
        const message =
          cause instanceof Error
            ? cause.message
            : "This quick ask could not finish. Try typing instead.";
        setError(message);
        voiceRef.current?.turnFailed(message);
      } finally {
        if (token === generation.current) setBusy(false);
      }
    },
    [busy, forgetAnswer, save, speak],
  );

  useEffect(() => {
    sendRef.current = send;
  }, [send]);

  useEffect(() => {
    let live = true;
    const controller = new AbortController();
    const activeDialog = dialog.current;
    activeDialog?.showModal();
    activeDialog?.querySelector<HTMLElement>("#student-voice-title")?.focus();
    const adapter = createSpeechAdapter(window);
    const nextVoice = createVoiceSession({
      adapter,
      transcribe: (wav, signal) => {
        const current = sessionRef.current;
        return current
          ? transcribeAssistantSpeech(current, wav, signal)
          : Promise.reject(new Error("Start a new quick ask."));
      },
      synthesize: async () => {
        throw new Error("Choose the answer to read aloud.");
      },
      onState: setVoice,
      onNotice: setError,
      onTranscript: ({ text }) => {
        void sendRef.current(text, "VOICE");
      },
    });
    voiceRef.current = nextVoice;
    void studentVoiceRequest<{ session_id: string; account_key: string }>(
      "/api/v1/student-voice/session",
      "POST",
      {},
      controller.signal,
    )
      .then((session) => {
        if (!live) {
          void deleteAssistantSession(session.session_id).catch(() => {});
          return;
        }
        if (accountKey && session.account_key !== accountKey) {
          void deleteAssistantSession(session.session_id).catch(() => {});
          throw new Error(
            "Your account changed. Close this quick ask and open it again.",
          );
        }
        ownerRef.current = session.account_key;
        sessionRef.current = session.session_id;
        setSessionId(session.session_id);
      })
      .catch((cause: unknown) => {
        if (live)
          setError(
            cause instanceof Error
              ? cause.message
              : "Quick ask could not start.",
          );
      });
    const recheckOwner = () => {
      if (!ownerRef.current) return;
      // Clear and stop before this asynchronous check, including shells without account props.
      clearPrivateView();
      checkingOwnerRef.current = true;
      setCheckingOwner(true);
      void studentVoiceRequest<{ account_key: string }>(
        "/api/v1/student-voice/account",
        "GET",
        undefined,
        controller.signal,
      )
        .then((current) => {
          if (!live) return;
          checkingOwnerRef.current = false;
          setCheckingOwner(false);
          if (current.account_key === ownerRef.current) return;
          sessionRef.current = null;
          setSessionId(null);
          setError(
            "Your account changed. Close this quick ask and open it again.",
          );
        })
        .catch(() => {
          if (live) {
            checkingOwnerRef.current = false;
            setCheckingOwner(false);
            sessionRef.current = null;
            setSessionId(null);
            setError("Sign in again, then open a new quick ask.");
          }
        });
    };
    window.addEventListener("focus", recheckOwner);
    return () => {
      live = false;
      activeDialog?.close();
      controller.abort();
      generation.current += 1;
      requestRef.current?.abort();
      nextVoice.dispose();
      voiceRef.current = null;
      window.removeEventListener("focus", recheckOwner);
      const session = sessionRef.current;
      forgetAnswer(answerRef.current, session);
      if (session && !savedRef.current)
        void deleteAssistantSession(session).catch(() => {});
    };
  }, [accountKey, clearPrivateView, forgetAnswer]);

  useEffect(() => {
    const keys = createStudentPushToTalk({
      canStart: () =>
        !!sessionRef.current &&
        !checkingOwnerRef.current &&
        !busy &&
        !["LISTENING", "TRANSCRIBING", "THINKING"].includes(
          voiceRef.current?.current().state ?? "",
        ),
      start: () => voiceRef.current?.listen(),
      finish: () => {
        voiceRef.current?.toggleListening();
        // Releasing Space before microphone permission resolves cancels that attempt.
        if (voiceRef.current?.current().state === "LISTENING")
          voiceRef.current.stop();
      },
      cancel: stop,
    });
    const hidden = () => {
      if (document.hidden) {
        keys.cancel();
        clearPrivateView();
      }
    };
    const blur = () => {
      keys.cancel();
      clearPrivateView();
    };
    window.addEventListener("keydown", keys.keyDown);
    window.addEventListener("keyup", keys.keyUp);
    window.addEventListener("blur", blur);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      keys.cancel();
      window.removeEventListener("keydown", keys.keyDown);
      window.removeEventListener("keyup", keys.keyUp);
      window.removeEventListener("blur", blur);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, [busy, clearPrivateView, stop]);

  const reviewClass = () => {
    if (!answer?.class_draft || !ownerRef.current) return;
    try {
      window.sessionStorage.setItem(
        studentClassDraftKey(ownerRef.current),
        JSON.stringify({
          accountKey: ownerRef.current,
          transcript: answer.class_draft,
          createdAt: new Date().toISOString(),
        }),
      );
    } catch {
      setError(
        "This browser could not carry the note over. Open Timetable and enter the class details.",
      );
      return;
    }
    stop();
    onClose();
    router.push("/classes?add=1");
  };

  const openSaved = () => {
    if (!sessionRef.current) return;
    try {
      window.sessionStorage.setItem(
        "navox.assistant.session.v1",
        sessionRef.current,
      );
    } catch {
      setError(
        "This browser could not open the saved conversation. Keep this window open to manage it.",
      );
      return;
    }
    stop();
    onClose();
    router.push("/navox");
  };

  const clear = async () => {
    stop();
    setError("");
    const session = sessionRef.current;
    if (!session) return;
    try {
      await deleteAssistantSession(session);
      forgetAnswer(answerRef.current, session);
      savedRef.current = false;
      onClose();
    } catch {
      setError(
        "The conversation could not be deleted. Keep this window open and try again.",
      );
    }
  };

  const recording = voice.state === "LISTENING";
  const working = busy || checkingOwner || voice.state === "TRANSCRIBING";
  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby="student-voice-title"
      onCancel={onClose}
    >
      <header className={styles.header}>
        <div>
          <span className={styles.eyebrow}>Your timetable, out loud</span>
          <h2 id="student-voice-title" tabIndex={-1}>
            Ask NavoX
          </h2>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close quick ask"
          className={styles.close}
        >
          ×
        </button>
      </header>
      <p className={styles.intro}>
        Find your current class, next room, or the rest of your day.
      </p>
      <div className={styles.prompts}>
        {[
          "What class do I have now?",
          "Where is my next class?",
          "What classes do I have tomorrow?",
        ].map((text) => (
          <button
            type="button"
            key={text}
            disabled={!sessionId || working || recording}
            onClick={() => {
              void send(text, "TEXT");
            }}
          >
            {text}
          </button>
        ))}
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void send(question, "TEXT");
        }}
      >
        <label htmlFor="student-voice-question" className={styles.label}>
          Your question
        </label>
        <textarea
          id="student-voice-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          maxLength={500}
          disabled={!sessionId || working || recording}
          placeholder="What class do I have at 2 pm tomorrow?"
          rows={2}
        />
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.talk}
            disabled={!sessionId || working || !voice.microphoneSupported}
            aria-pressed={recording}
            onClick={() => voiceRef.current?.toggleListening()}
          >
            <Mic />
            {recording ? "Send recording" : "Talk"}
          </button>
          <button
            type="submit"
            disabled={!sessionId || working || recording || !question.trim()}
          >
            {working ? "Checking…" : "Ask"}
          </button>
          {(recording || working || voice.state === "SPEAKING") && (
            <button type="button" onClick={stop}>
              Stop
            </button>
          )}
        </div>
      </form>
      <p className={styles.status} aria-live="polite">
        {recording
          ? "Listening. Press Send recording when you finish."
          : voice.state === "TRANSCRIBING"
            ? "Transcribing your question…"
            : voice.state === "SPEAKING"
              ? "Speaking. Press Stop to end playback."
              : checkingOwner
                ? "Checking the current account…"
                : !sessionId && !error
                  ? "Opening a private quick ask…"
                  : "On a keyboard, hold Space away from text fields to talk. Release to ask."}
      </p>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {answer && (
        <section
          className={styles.answer}
          aria-label="NavoX answer"
          aria-live="polite"
        >
          <p>{answer.answer}</p>
          {answer.receipt_id ? (
            <button
              type="button"
              onClick={() => speak(answer)}
              disabled={working || recording}
            >
              Read aloud
            </button>
          ) : null}
          {answer.class_draft && (
            <button type="button" onClick={reviewClass}>
              Review class details
            </button>
          )}
          {answer.saved && (
            <span className={styles.saved}>Saved to this conversation.</span>
          )}
        </section>
      )}
      <label className={styles.save}>
        <input
          type="checkbox"
          checked={save}
          disabled={working || recording}
          onChange={(event) => setSave(event.target.checked)}
        />
        <span>Save new questions and answers</span>
      </label>
      <p className={styles.privacy}>
        {save
          ? "New questions and answers are saved for up to 30 days. Turning this off does not delete earlier saved questions."
          : "Your quick asks are not saved as transcripts. NavoX does not save recordings. Private spoken answers expire from server memory within 10 minutes."}
      </p>
      <footer className={styles.footer}>
        <Link
          href="/classes"
          onClick={() => {
            stop();
            onClose();
          }}
        >
          Open Timetable
        </Link>
        {savedRef.current && (
          <button type="button" onClick={openSaved}>
            Open saved conversation
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            void clear();
          }}
          disabled={!sessionId}
        >
          Delete this conversation
        </button>
      </footer>
    </dialog>
  );
}
