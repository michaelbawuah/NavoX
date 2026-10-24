"use client";

import Link from "next/link";
import {
  type KeyboardEvent,
  type ReactNode,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { VoxCallController, VoxCallState } from "../lib/vox-call";
import styles from "./vox-call-controls.module.css";
import { useVoxCall } from "./vox-call-provider";

type IconName = "microphone" | "speaker" | "captions" | "hang-up" | "minimize";

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, string> = {
    microphone:
      "M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0V5Zm-3 7a6 6 0 0 0 12 0M12 18v4m-4 0h8",
    speaker: "M4 9h4l5-4v14l-5-4H4V9Zm12-1a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14",
    captions: "M4 5h16v14H4V5Zm6 5a2 2 0 1 0 0 4m7-4a2 2 0 1 0 0 4",
    "hang-up": "M4 15v3h4v-3l-2-1a13 13 0 0 1 12 0l-2 1v3h4v-3c0-7-16-7-16 0Z",
    minimize: "M5 12h14",
  };
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      className={styles.icon}
    >
      <path
        d={paths[name]}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Wave({ activity = "quiet" }: { activity?: VoxCallState["activity"] }) {
  return (
    <span className={styles.wave} data-activity={activity} aria-hidden="true">
      <span />
      <span />
      <span />
      <span />
      <span />
    </span>
  );
}

function duration(elapsedMs: number) {
  const seconds = Math.max(0, Math.floor(elapsedMs / 1_000));
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  return hours
    ? `${hours}:${String(minutes % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`
    : `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function phaseLabel(state: VoxCallState) {
  switch (state.phase) {
    case "connecting":
      return "Connecting";
    case "reconnecting":
      return "Reconnecting";
    case "standby":
      return "Standing by";
    case "ended":
      return "Call ended";
    case "unavailable":
      return "Vox calls are not available yet";
    case "active":
      if (state.speakerEnabled && state.speakerBlocked)
        return "Enable sound to hear NavoX";
      if (state.microphoneMuted && state.activity !== "speaking")
        return "Microphone muted";
      switch (state.activity) {
        case "listening":
          return "Listening";
        case "thinking":
          return "Thinking";
        case "speaking":
          return "Speaking";
        case "quiet":
          return "Ready when you are";
      }
      return "Ready when you are";
    default:
      return "Talk with NavoX";
  }
}

function phaseDetail(state: VoxCallState) {
  switch (state.phase) {
    case "connecting":
      return "Waiting for the call connection.";
    case "reconnecting":
      return "Audio is paused while the connection returns.";
    case "standby":
      if (state.microphoneMuted)
        return "Microphone muted. Tap to resume when you are ready.";
      if (state.wakeStatus === "listening")
        return "Audio transmission is paused. Say “Hey NavoX” or tap to continue. Keep NavoX open.";
      if (state.wakeStatus === "starting")
        return "Preparing local wake listening. You can tap to continue.";
      return "Audio transmission is paused. Local wake is unavailable in this browser. Tap to continue.";
    case "ended":
      if (state.endReason === "cleanup-incomplete")
        return "The call ended, but cleanup could not be confirmed. Reload NavoX before starting another call.";
      return state.endReason === "connection-failed"
        ? "The connection could not be completed. You can try again."
        : "Your call has finished.";
    default:
      return "Your microphone and speaker have separate controls.";
  }
}

// Render only simple emphasis; all other provider text remains escaped React text.
// Split citation ranges first so formatting never changes provider offsets.
function webText(text: string): ReactNode[] {
  let offset = 0;
  return text.split(/(\*\*[^*\n]+\*\*)/g).map((part) => {
    const key = offset;
    offset += part.length;
    return part.startsWith("**") && part.endsWith("**") ? (
      <strong key={key}>{part.slice(2, -2)}</strong>
    ) : (
      part
    );
  });
}

function WebLookup({ state }: { state: VoxCallState }) {
  const result = state.ownerVerified ? state.webLookup : null;
  if (!result || state.phase === "ended") return null;
  if (result.status !== "complete")
    return (
      <p
        role="status"
        className={styles.hint}
        data-vox-web-status={result.status}
      >
        {result.status === "searching"
          ? "Checking the web…"
          : "The online lookup could not be verified."}
      </p>
    );
  const characters = Array.from(result.text);
  const content: ReactNode[] = [];
  let offset = 0;
  for (const [index, source] of result.citations.entries()) {
    content.push(webText(characters.slice(offset, source.start).join("")));
    content.push(
      <a
        key={index}
        href={source.url}
        target="_blank"
        rel="noopener noreferrer"
      >
        {source.title}
      </a>,
    );
    offset = source.end;
  }
  content.push(webText(characters.slice(offset).join("")));
  return (
    <section
      className={styles.webResult}
      aria-label="Web lookup sources"
      data-vox-web-status="complete"
    >
      <p className={styles.eyebrow}>From the web</p>
      <p>{content}</p>
    </section>
  );
}

function CallDialog({
  controller,
  state,
  chatHref,
  onMinimize,
  onDone,
  onStart,
  usageNotice,
}: {
  controller: VoxCallController;
  state: VoxCallState;
  chatHref: string;
  onMinimize: () => void;
  onDone: () => void;
  onStart: () => void;
  usageNotice?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const returnTarget = useRef<HTMLElement | null>(null);
  const id = useId();
  const ended = state.phase === "ended";
  const cleanupIncomplete = state.endReason === "cleanup-incomplete";
  function dismiss() {
    if (ended) onDone();
    else onMinimize();
    queueMicrotask(() => {
      if (returnTarget.current?.isConnected) returnTarget.current.focus();
    });
  }
  useLayoutEffect(() => {
    const element = dialog.current;
    if (!element) return;
    const active = document.activeElement;
    if (
      active instanceof HTMLElement &&
      active.hasAttribute("data-vox-launcher")
    )
      returnTarget.current = active;
    if (typeof element.showModal === "function") element.showModal();
    else element.setAttribute("open", "");
    element.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      if (typeof element.close === "function" && element.open) element.close();
      else element.removeAttribute("open");
    };
  }, []);

  function keyboard(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      dismiss();
    } else if (event.key === "Tab") {
      const focusable = [
        ...(dialog.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]),a[href]",
        ) ?? []),
      ];
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
  }

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-detail`}
      aria-modal="true"
      onKeyDown={keyboard}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
    >
      <header className={styles.dialogHeader}>
        <div>
          <p className={styles.eyebrow}>A little company for your day</p>
          <h2 id={`${id}-title`}>NavoX Vox</h2>
        </div>
        <button
          type="button"
          className={styles.iconButton}
          onClick={dismiss}
          aria-label={ended ? "Close call details" : "Minimize call"}
        >
          <Icon name="minimize" />
        </button>
      </header>
      <div className={styles.callVisual} data-phase={state.phase}>
        <div className={styles.orbit}>
          <Wave
            activity={state.phase === "active" ? state.activity : "quiet"}
          />
        </div>
      </div>
      <div className={styles.callStatus}>
        <p
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className={styles.status}
        >
          {phaseLabel(state)}
        </p>
        {state.startedAt !== null && (
          <time
            role="timer"
            aria-live="off"
            className={styles.timer}
            aria-label="Call duration"
            dateTime={`PT${Math.floor(state.elapsedMs / 1_000)}S`}
          >
            {duration(state.elapsedMs)}
          </time>
        )}
        <p id={`${id}-detail`} className={styles.detail}>
          {phaseDetail(state)}
        </p>
      </div>
      <WebLookup state={state} />
      {usageNotice && (
        <p role="status" data-vox-usage="true" className={styles.hint}>
          {usageNotice}
        </p>
      )}
      {state.wakeInputStatus && (
        <p role="status" className={styles.hint}>
          {state.wakeInputStatus === "queued"
            ? "Your question is queued in this conversation."
            : "Conversation resumed. Your question could not be queued; please say it again."}
        </p>
      )}
      {!ended ? (
        <>
          {state.phase === "standby" && (
            <button
              type="button"
              className={styles.resume}
              onClick={() => controller.resume()}
            >
              Resume conversation
            </button>
          )}
          {state.phase === "active" &&
            state.speakerEnabled &&
            state.speakerBlocked && (
              <div className={styles.soundNotice}>
                <p>Your browser paused sound.</p>
                <button
                  type="button"
                  onClick={() => controller.setSpeakerEnabled(true)}
                >
                  Enable sound
                </button>
              </div>
            )}
          <fieldset className={styles.controls} aria-label="Call controls">
            <button
              type="button"
              className={styles.control}
              aria-pressed={state.microphoneMuted}
              aria-label={
                state.microphoneMuted ? "Unmute microphone" : "Mute microphone"
              }
              onClick={() =>
                controller.setMicrophoneMuted(!state.microphoneMuted)
              }
            >
              <Icon name="microphone" />
              <span className={styles.controlLabel}>Microphone</span>
              <small>{state.microphoneMuted ? "Muted" : "On"}</small>
            </button>
            <button
              type="button"
              className={styles.control}
              aria-pressed={state.speakerEnabled}
              aria-label={
                state.speakerEnabled ? "Turn speaker off" : "Turn speaker on"
              }
              onClick={() =>
                controller.setSpeakerEnabled(!state.speakerEnabled)
              }
            >
              <Icon name="speaker" />
              <span className={styles.controlLabel}>Speaker</span>
              <small>{state.speakerEnabled ? "On" : "Off"}</small>
            </button>
            <button
              type="button"
              className={styles.control}
              aria-label="Captions unavailable"
              aria-describedby={`${id}-captions-note`}
              disabled
            >
              <Icon name="captions" />
              <span className={styles.controlLabel}>Captions</span>
              <small>Unavailable</small>
            </button>
          </fieldset>
          <p id={`${id}-captions-note`} className={styles.captionNote}>
            Live captions are not available yet.
          </p>
          <button
            type="button"
            className={styles.hangUp}
            onClick={() => void controller.hangUp()}
          >
            <Icon name="hang-up" />
            Hang up
          </button>
        </>
      ) : (
        <div className={styles.endedActions}>
          {cleanupIncomplete ? (
            <button
              type="button"
              className={styles.resume}
              onClick={() => window.location.reload()}
            >
              Reload NavoX
            </button>
          ) : (
            <button type="button" className={styles.resume} onClick={onStart}>
              Start another call
            </button>
          )}
          <button type="button" className={styles.secondary} onClick={dismiss}>
            Done
          </button>
        </div>
      )}
      <Link
        className={styles.chatLink}
        href={chatHref}
        onClick={ended ? onDone : onMinimize}
      >
        Continue in typed chat <span aria-hidden="true">↗</span>
      </Link>
    </dialog>
  );
}

/** A route-owned entry only; the root controls own the one call dialog. */
export function VoxHomeLauncher({
  chatHref = "/navox",
}: {
  chatHref?: string;
}) {
  const call = useVoxCall();
  const id = useId();
  const [startError, setStartError] = useState(false);
  const state = call?.state;
  const verified = state?.owner !== null && state?.ownerVerified === true;
  const ongoing =
    state !== undefined &&
    ["connecting", "reconnecting", "active", "standby"].includes(state.phase);
  const cleanupIncomplete = state?.endReason === "cleanup-incomplete";
  const unavailable = !call?.available || state?.phase === "unavailable";
  const label = cleanupIncomplete
    ? "Reload NavoX"
    : ongoing
      ? "Return to Vox call"
      : "Talk with NavoX";
  const status = !verified
    ? "Checking your sign-in…"
    : cleanupIncomplete
      ? "Reload needed"
      : unavailable
        ? "Vox calls are not available yet"
        : state?.endReason === "connection-failed"
          ? "The call could not connect"
          : state
            ? phaseLabel(state)
            : "Talk with NavoX";
  const detail = !verified
    ? "Your call can start after your sign-in is checked."
    : cleanupIncomplete
      ? "Call cleanup could not be confirmed. Reload before starting another call."
      : unavailable
        ? "You can still ask NavoX in typed chat."
        : ongoing && state
          ? phaseDetail(state)
          : state?.endReason === "connection-failed"
            ? "Try again when you’re ready, or continue in typed chat."
            : "A question, an idea, or a moment to think.";

  async function openCall() {
    if (!call || !verified) return;
    if (cleanupIncomplete) {
      window.location.reload();
      return;
    }
    if (unavailable) return;
    setStartError(false);
    if (ongoing) {
      call.controller.setMinimized(false);
      return;
    }
    try {
      await call.controller.start();
    } catch {
      setStartError(true);
    }
  }

  return (
    <section
      className={`${styles.widget} ${styles.homeEntry}`}
      aria-label="Vox for your day"
    >
      <button
        type="button"
        className={styles.homeLauncher}
        data-vox-launcher="home"
        aria-label={label}
        aria-haspopup={cleanupIncomplete ? undefined : "dialog"}
        aria-describedby={`${id}-status ${id}-detail`}
        title="Talk with NavoX"
        disabled={!verified || (unavailable && !cleanupIncomplete)}
        onClick={() => void openCall()}
      >
        <span className={styles.homeWave}>
          <Wave activity={verified && ongoing ? state?.activity : "quiet"} />
        </span>
        <span className={styles.homeWords}>
          <span className={styles.homeEyebrow}>Talk with NavoX</span>
          <strong>Vox</strong>
          <span
            id={`${id}-status`}
            className={styles.homeStatus}
            role="status"
            aria-live="polite"
          >
            {status}
            {verified &&
              ongoing &&
              state?.microphoneMuted &&
              state.activity === "speaking" &&
              " · Microphone muted"}
            {verified &&
              ongoing &&
              state?.speakerEnabled === false &&
              " · Speaker off"}
          </span>
        </span>
        <span className={styles.homeArrow} aria-hidden="true">
          ↗
        </span>
      </button>
      <div className={styles.homeFooter}>
        <p id={`${id}-detail`}>{detail}</p>
        <Link href={chatHref}>
          Use typed chat <span aria-hidden="true">↗</span>
        </Link>
      </div>
      {startError && verified && !ongoing && !cleanupIncomplete && (
        <p role="alert" className={styles.hint}>
          The call could not be started. Please try again.
        </p>
      )}
    </section>
  );
}

/** Mount once inside the app-scoped provider; route children never own the call. */
export function VoxCallControls({
  variant = "floating",
  chatHref = "/navox",
}: {
  variant?: "home" | "floating";
  chatHref?: string;
}) {
  const call = useVoxCall();
  const launcher = useRef<HTMLButtonElement>(null);
  const [dismissedCallId, setDismissedCallId] = useState<string | null>(null);
  const [startError, setStartError] = useState(false);
  const className = `${styles.widget} ${variant === "floating" ? styles.floating : styles.home}`;
  const verified = call?.state.ownerVerified === true;
  const cleanupIncomplete = call?.state.endReason === "cleanup-incomplete";
  const detailsId = cleanupIncomplete
    ? `cleanup:${call?.state.callId ?? "blocked"}`
    : (call?.state.callId ?? null);

  function returnFocus() {
    queueMicrotask(() => launcher.current?.focus());
  }
  function minimize() {
    call?.controller.setMinimized(true);
    returnFocus();
  }
  function done() {
    setDismissedCallId(detailsId);
    returnFocus();
  }
  async function start() {
    if (
      !call?.available ||
      !call.state.ownerVerified ||
      call.state.endReason === "cleanup-incomplete"
    )
      return;
    setStartError(false);
    try {
      await call.controller.start();
    } catch {
      setStartError(true);
    }
  }

  if (!call?.state.owner) return null;

  if (!verified) {
    return (
      <section className={className} aria-label="Vox">
        <button type="button" className={styles.launcher} disabled>
          <Wave />
          <span>Vox</span>
        </button>
        <p role="status" className={styles.hint}>
          Checking your sign-in…
        </p>
      </section>
    );
  }
  const { state, controller, available } = call;
  const ongoing = ["connecting", "reconnecting", "active", "standby"].includes(
    state.phase,
  );
  const hasDetails = detailsId !== null && detailsId !== dismissedCallId;
  const open = hasDetails && !state.minimized;
  const label = cleanupIncomplete
    ? "Reload NavoX"
    : ongoing
      ? "Return to Vox call"
      : "Talk with NavoX";

  return (
    <section className={className} aria-label="Vox">
      <button
        ref={launcher}
        type="button"
        className={styles.launcher}
        data-vox-launcher={variant}
        aria-label={label}
        aria-haspopup={cleanupIncomplete ? undefined : "dialog"}
        aria-expanded={cleanupIncomplete ? undefined : open}
        disabled={!available && !cleanupIncomplete}
        onClick={() => {
          if (cleanupIncomplete) window.location.reload();
          else if (ongoing) controller.setMinimized(false);
          else void start();
        }}
      >
        <Wave activity={ongoing ? state.activity : "quiet"} />
        <span>
          <strong>Vox</strong>
          <small>
            {cleanupIncomplete
              ? "Reload needed"
              : ongoing
                ? phaseLabel(state)
                : "Talk with NavoX"}
          </small>
        </span>
        {ongoing && state.startedAt !== null && (
          <span className={styles.dockTimer} aria-hidden="true">
            {duration(state.elapsedMs)}
          </span>
        )}
      </button>
      {cleanupIncomplete && !open ? (
        <p className={styles.hint}>
          Call cleanup could not be confirmed. Reload before starting another
          call. <Link href={chatHref}>Use typed chat</Link>
        </p>
      ) : !available ? (
        <p className={styles.hint}>
          Vox calls are not available yet.{" "}
          <Link href={chatHref}>Use typed chat</Link>
        </p>
      ) : !ongoing && !open ? (
        <p className={styles.hint}>A conversation, at your pace.</p>
      ) : null}
      {startError && !cleanupIncomplete && (
        <p role="alert" className={styles.hint}>
          The call could not be started. Please try again.
        </p>
      )}
      {!open && call.usageNotice?.message && (
        <p role="status" data-vox-usage="true" className={styles.hint}>
          {call.usageNotice.message}
        </p>
      )}
      {open && (
        <CallDialog
          controller={controller}
          state={state}
          chatHref={chatHref}
          onMinimize={minimize}
          onDone={done}
          onStart={() => void start()}
          usageNotice={call.usageNotice?.message}
        />
      )}
    </section>
  );
}
