import type { OutlookDraft } from "./outlook";

export function outlookDraftGroups(drafts: OutlookDraft[]) {
  const verified = drafts.filter(
    (draft) => draft.status === "completed" && draft.verified_at,
  );
  return {
    editable: drafts.filter((draft) => draft.status === "review"),
    pending: drafts.filter(
      (draft) =>
        ["executing", "uncertain"].includes(draft.status) ||
        (draft.status === "completed" && !draft.verified_at),
    ),
    waiting: verified.filter((draft) => draft.reply_state === "waiting"),
    replied: verified.filter((draft) => draft.reply_state === "replied"),
    unchecked: verified.filter(
      (draft) =>
        !["waiting", "replied"].includes(draft.reply_state ?? "not_checked"),
    ),
    sentSources: new Set(
      drafts
        .filter((draft) =>
          ["executing", "uncertain", "completed"].includes(draft.status),
        )
        .map((draft) => draft.source_message_id),
    ),
  };
}
