"use client";

export function DismissCommitment({
  createdBy,
  status,
  busy,
  onDismiss,
}: {
  createdBy: string;
  status: string;
  busy: boolean;
  onDismiss: () => void;
}) {
  if (
    createdBy !== "ai" ||
    ![
      "confirmed",
      "attention",
      "upcoming",
      "waiting",
      "waiting_on_external",
    ].includes(status)
  )
    return null;
  return (
    <button disabled={busy} onClick={onDismiss} type="button">
      Not a task
    </button>
  );
}
