/** Synthetic acceptance fixtures only. No real mailbox, message or OAuth grant is represented. */
export const inboxFixture = [
  {
    id: "synthetic-office-hours",
    sourceId: "synthetic-office-hours-source",
    sender: "instructor@example.test",
    subject: "Office-hours meeting",
    body: "Would Monday at 2 PM work for a 30-minute meeting? Please reply with a time that works for you.",
    sent_at: "2026-10-24T14:00:00Z",
    open_url: "https://mail.google.com/mail/#all/synthetic-office-hours",
    reason: "Meeting time needs a reply",
  },
  {
    id: "synthetic-project-review",
    sourceId: "synthetic-project-review-source",
    sender: "teammate@example.test",
    subject: "Project outline review",
    body: "Please review the updated project outline and let me know whether Friday afternoon works. No personal reason is needed.",
    sent_at: "2026-10-24T15:00:00Z",
    open_url: "https://mail.google.com/mail/#all/synthetic-project-review",
    reason: "Feedback requested",
  },
] as const;
