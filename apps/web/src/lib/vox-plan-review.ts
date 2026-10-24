import {
  readStudyPreferences,
  readStudyTaskFields,
  type StudyPreferences,
  type StudyTaskFields,
} from "./student-planning";

interface ReviewIdentity {
  preview_id: string;
  preview_hash: string;
  timezone: string;
}
export type VoxPlanReview =
  | (ReviewIdentity & { kind: "study-plan" })
  | (ReviewIdentity & {
      kind: "task";
      task: StudyTaskFields;
      task_id?: string;
      revision?: number;
    })
  | (ReviewIdentity & { kind: "class"; transcript: string })
  | (ReviewIdentity & { kind: "routine"; preferences: StudyPreferences });

export function parseVoxPlanReview(data: Uint8Array): VoxPlanReview | null {
  if (data.byteLength > 32768) return null;
  try {
    const v = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(data),
    );
    if (
      !["study-plan", "routine", "task", "class"].includes(v?.kind) ||
      typeof v.preview_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        v.preview_id,
      ) ||
      typeof v.preview_hash !== "string" ||
      !/^[0-9a-f]{64}$/.test(v.preview_hash) ||
      typeof v.timezone !== "string" ||
      v.timezone.length > 64
    )
      return null;
    new Intl.DateTimeFormat("en", { timeZone: v.timezone }).format();
    const identity = {
      preview_id: v.preview_id,
      preview_hash: v.preview_hash,
      timezone: v.timezone,
    };
    if (v.kind === "class") {
      if (
        typeof v.transcript !== "string" ||
        !v.transcript.trim() ||
        v.transcript.length > 500 ||
        Array.from(v.transcript as string).some(
          (character) =>
            character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
        ) ||
        !/^(?:please\s+)?(?:add|create)\s+/i.test(v.transcript)
      )
        return null;
      return { ...identity, kind: "class", transcript: v.transcript };
    }
    if (v.kind === "task") {
      if (v.task_id !== undefined || v.revision !== undefined) {
        if (
          typeof v.task_id !== "string" ||
          !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
            v.task_id,
          ) ||
          !Number.isSafeInteger(v.revision) ||
          v.revision < 1
        )
          return null;
        return {
          ...identity,
          kind: "task",
          task: readStudyTaskFields(v.task),
          task_id: v.task_id,
          revision: v.revision,
        };
      }
      return { ...identity, kind: "task", task: readStudyTaskFields(v.task) };
    }
    if (v.kind === "routine") {
      const preferences = readStudyPreferences(v.preferences);
      if (preferences.timezone !== v.timezone) return null;
      return { ...identity, kind: "routine", preferences };
    }
    return { ...identity, kind: "study-plan" };
  } catch {
    return null;
  }
}
