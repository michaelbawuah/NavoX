import { randomUUID } from "node:crypto";
import {
  AssistantError,
  type AssistantScope,
  type StudentLookupRequest,
} from "@navox/assistant-runtime";

export const STUDENT_VOICE_TTL_MS = 10 * 60 * 1000;
const MAX_RECEIPTS = 128;
const MAX_PER_ACCOUNT = 8;
const MAX_TOTAL_BYTES = 256 * 1024;
const MAX_RECEIPT_BYTES = 8192;

export interface StudentVoiceReceipt {
  id: string;
  scope: AssistantScope;
  sessionId: string;
  lookup: StudentLookupRequest;
  text: string;
  answerDigest: string;
  expiresAt: number;
  playbackAttempts: number;
}

function sameScope(left: AssistantScope, right: AssistantScope) {
  return (
    left.user_id === right.user_id && left.workspace_id === right.workspace_id
  );
}

/** No transcripts/audio on disk. Process restart, expiry or eviction intentionally loses playback. */
export class StudentVoiceReceiptStore {
  private readonly entries = new Map<
    string,
    {
      receipt: StudentVoiceReceipt;
      bytes: number;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  private bytes = 0;
  constructor(private readonly now: () => number = Date.now) {}

  private remove(id: string) {
    const row = this.entries.get(id);
    if (row) {
      this.bytes -= row.bytes;
      clearTimeout(row.timer);
    }
    this.entries.delete(id);
  }

  private sweep() {
    for (const [id, row] of this.entries)
      if (row.receipt.expiresAt <= this.now()) this.remove(id);
  }

  put(
    input: Omit<StudentVoiceReceipt, "id" | "expiresAt" | "playbackAttempts">,
  ): StudentVoiceReceipt {
    this.sweep();
    if (!input.text || input.text.length > 600)
      throw new AssistantError(
        "invalid_request",
        "This answer is too long to read aloud. Read it on screen.",
      );
    const receipt: StudentVoiceReceipt = {
      ...input,
      scope: {
        user_id: input.scope.user_id,
        workspace_id: input.scope.workspace_id,
      },
      lookup: { ...input.lookup },
      id: randomUUID(),
      expiresAt: this.now() + STUDENT_VOICE_TTL_MS,
      playbackAttempts: 0,
    };
    const bytes = Buffer.byteLength(JSON.stringify(receipt), "utf8");
    if (bytes > MAX_RECEIPT_BYTES)
      throw new AssistantError(
        "unavailable",
        "The spoken answer could not be prepared.",
      );
    const owned = [...this.entries].filter(([, row]) =>
      sameScope(row.receipt.scope, input.scope),
    );
    while (owned.length >= MAX_PER_ACCOUNT) {
      const oldestOwned = owned.shift();
      if (oldestOwned) this.remove(oldestOwned[0]);
    }
    while (
      this.entries.size >= MAX_RECEIPTS ||
      this.bytes + bytes > MAX_TOTAL_BYTES
    ) {
      const oldest = this.entries.keys().next().value;
      if (!oldest) break;
      this.remove(oldest);
    }
    const timer = setTimeout(
      () => this.remove(receipt.id),
      STUDENT_VOICE_TTL_MS,
    );
    timer.unref?.();
    this.entries.set(receipt.id, { receipt, bytes, timer });
    this.bytes += bytes;
    return {
      ...receipt,
      scope: { ...receipt.scope },
      lookup: { ...receipt.lookup },
    };
  }

  read(
    id: string,
    scope: AssistantScope,
    sessionId: string,
    playback = false,
  ): StudentVoiceReceipt {
    this.sweep();
    const receipt = this.entries.get(id)?.receipt;
    if (
      !receipt ||
      !sameScope(receipt.scope, scope) ||
      receipt.sessionId !== sessionId
    )
      throw new AssistantError(
        "not_found",
        "This private spoken answer expired or is no longer on this server. Ask again; it was not saved.",
      );
    if (playback && receipt.playbackAttempts >= 3)
      throw new AssistantError(
        "unavailable",
        "This answer reached its playback limit. Ask again or read it on screen.",
      );
    if (playback) receipt.playbackAttempts += 1;
    return {
      ...receipt,
      scope: { ...receipt.scope },
      lookup: { ...receipt.lookup },
    };
  }

  forget(id: string, scope: AssistantScope, sessionId: string) {
    const receipt = this.entries.get(id)?.receipt;
    if (
      receipt &&
      sameScope(receipt.scope, scope) &&
      receipt.sessionId === sessionId
    )
      this.remove(id);
  }

  size() {
    this.sweep();
    return { count: this.entries.size, bytes: this.bytes };
  }
}

const shared = globalThis as typeof globalThis & {
  __navoxStudentVoiceReceipts?: StudentVoiceReceiptStore;
};
export function studentVoiceReceipts(): StudentVoiceReceiptStore {
  shared.__navoxStudentVoiceReceipts ??= new StudentVoiceReceiptStore();
  return shared.__navoxStudentVoiceReceipts;
}
