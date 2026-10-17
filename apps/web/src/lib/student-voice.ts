/** Browser-only handoff and keyboard controls. No recording starts on navigation. */
export function studentClassDraftKey(accountKey: string): string {
  return `navox.student.class-draft.v1:${accountKey}`;
}

export function consumeStudentClassDraft(
  storage: Pick<Storage, "getItem" | "removeItem">,
  accountKey: string,
  now = Date.now(),
): string | null {
  try {
    const key = studentClassDraftKey(accountKey);
    const raw = storage.getItem(key);
    storage.removeItem(key);
    if (!raw || raw.length > 2500) return null;
    const data: unknown = JSON.parse(raw);
    if (!data || typeof data !== "object") return null;
    const value = data as Record<string, unknown>;
    const created =
      typeof value.createdAt === "string"
        ? Date.parse(value.createdAt)
        : Number.NaN;
    if (
      value.accountKey !== accountKey ||
      typeof value.transcript !== "string" ||
      !value.transcript.trim() ||
      value.transcript.length > 500 ||
      !Number.isFinite(created) ||
      created > now ||
      now - created >= 10 * 60 * 1000
    )
      return null;
    return value.transcript.trim();
  } catch {
    return null;
  }
}

interface SpaceEvent {
  code?: string;
  key: string;
  repeat?: boolean;
  altKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  isComposing?: boolean;
  defaultPrevented?: boolean;
  target: EventTarget | null;
  preventDefault(): void;
}

export interface PushToTalkControls {
  canStart(): boolean;
  start(): void;
  finish(): void;
  cancel(): void;
}

/** Space retains native typing/button behavior. A held key owns exactly one recording. */
export function createStudentPushToTalk(controls: PushToTalkControls) {
  let held = false;
  const editable = (target: EventTarget | null) => {
    const element = target as {
      closest?: (selector: string) => unknown;
      isContentEditable?: boolean;
    } | null;
    return (
      !!element?.isContentEditable ||
      !!element?.closest?.(
        'input,textarea,select,button,a[href],summary,[contenteditable]:not([contenteditable="false"]),[role="textbox"],[role="button"],[role="slider"],[role="combobox"],[role="menuitem"]',
      )
    );
  };
  return {
    keyDown(event: SpaceEvent) {
      if (
        (event.code !== "Space" && event.key !== " ") ||
        event.defaultPrevented ||
        event.repeat ||
        event.isComposing ||
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        editable(event.target) ||
        held ||
        !controls.canStart()
      )
        return;
      event.preventDefault();
      held = true;
      controls.start();
    },
    keyUp(event: SpaceEvent) {
      if (!held || (event.code !== "Space" && event.key !== " ")) return;
      event.preventDefault();
      held = false;
      controls.finish();
    },
    cancel() {
      if (!held) return;
      held = false;
      controls.cancel();
    },
    active: () => held,
  };
}

export interface StudentQuickAskAnswer {
  kind: "lookup" | "clarify" | "class_draft";
  answer: string;
  saved: boolean;
  receipt_id: string | null;
  session_id?: string;
  turn_id?: string;
  class_draft?: string;
}

export async function studentVoiceRequest<T>(
  path: string,
  method: "POST" | "GET" | "DELETE",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "include",
    cache: "no-store",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const data = (await response.json().catch(() => null)) as {
      error?: { message?: string };
    } | null;
    throw new Error(
      data?.error?.message ??
        "This quick ask could not finish. Type your question or open Timetable.",
    );
  }
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}
