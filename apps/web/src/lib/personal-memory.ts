export const memoryKinds = [
  "reply_length",
  "tone",
  "signoff",
  "study_hours",
  "project_fact",
] as const;

export type MemoryKind = (typeof memoryKinds)[number];
export interface PersonalMemory {
  id: string;
  kind: MemoryKind;
  label: string;
  content: string;
  enabled: boolean;
  provenance: "user_saved";
  created_at: string;
  updated_at: string;
}

export const memoryKindLabels: Record<MemoryKind, string> = {
  reply_length: "Reply length",
  tone: "Writing tone",
  signoff: "Email sign-off",
  study_hours: "Study hours",
  project_fact: "Project context",
};

export function readPersonalMemories(payload: unknown): PersonalMemory[] {
  if (!Array.isArray(payload) || payload.length > 30)
    throw new Error("Saved preferences could not be read. Please try again.");
  for (const row of payload) {
    if (
      !row ||
      typeof row !== "object" ||
      !memoryKinds.includes(row.kind) ||
      typeof row.id !== "string" ||
      typeof row.label !== "string" ||
      typeof row.content !== "string" ||
      typeof row.enabled !== "boolean" ||
      row.provenance !== "user_saved"
    )
      throw new Error("Saved preferences could not be read. Please try again.");
  }
  return payload as PersonalMemory[];
}

export function memoryInstruction(memory: PersonalMemory): string {
  switch (memory.kind) {
    case "reply_length":
      return (
        {
          short: "Keep replies short.",
          balanced: "Use balanced detail.",
          detailed: "Give detailed replies.",
        }[memory.content] ?? `${memory.label}: ${memory.content}`
      );
    case "tone":
      return `Use a ${memory.content} tone.`;
    case "signoff":
      return `Use this email sign-off: ${memory.content}`;
    case "study_hours":
      return `My preferred study hours: ${memory.content}`;
    case "project_fact":
      return `${memory.label}: ${memory.content}`;
  }
}

/** Add only an explicit selection, in full, to the visible user-authored text. */
export function appendMemorySelection(
  value: string,
  memories: PersonalMemory[],
  selectedIds: readonly string[],
  maxLength: number,
): { value: string; error?: string } {
  const selected = new Set(selectedIds);
  const seen = new Set<string>();
  const lines = memories
    .filter((row) => {
      if (!row.enabled || !selected.has(row.id) || seen.has(row.id))
        return false;
      seen.add(row.id);
      return true;
    })
    .map(memoryInstruction);
  if (!lines.length)
    return { value, error: "Choose at least one saved preference." };
  const block = `Saved context:\n${lines.join("\n")}`;
  if (value.includes(block)) return { value };
  const next = `${value.trimEnd()}${value.trim() ? "\n\n" : ""}${block}`;
  if (next.length > maxLength)
    return {
      value,
      error: `This would exceed the ${maxLength}-character limit. Choose fewer preferences or shorten your message.`,
    };
  return { value: next };
}
