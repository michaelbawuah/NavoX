import { outlookRequest, readOutlookStatus } from "./outlook";
import {
  readStudentEmailErase,
  readStudentEmailForget,
  readStudentEmailReview,
  readStudentEmailTaskReceipt,
  type StudentEmailApi,
  studentEmailRequest,
} from "./student-email";
import { readStudentTimetable } from "./student-schedule";

export function outlookTaskApi(accountKey: string): StudentEmailApi {
  const scope = { accountKey, namespace: "outlook" as const };
  return {
    account: async (signal) => {
      // The server validates the cookie and exact account header before returning status.
      await outlookRequest(
        accountKey,
        "/status",
        readOutlookStatus,
        "GET",
        undefined,
        signal,
      );
      return accountKey;
    },
    context: async (signal) => {
      const value = await studentEmailRequest(
        "/task-context",
        readStudentTimetable,
        "GET",
        undefined,
        signal,
        scope,
      );
      return {
        timezone: value.timezone,
        server_now: value.server_now,
        courses: value.courses.filter((course) => !course.archived),
      };
    },
    search: async () => {
      throw new Error("Choose one Outlook email in Inbox to review it.");
    },
    analyze: async () => {
      throw new Error("Choose one Outlook email in Inbox to review it.");
    },
    review: (id, signal) =>
      studentEmailRequest(
        `/task-reviews/${encodeURIComponent(id)}`,
        readStudentEmailReview,
        "GET",
        undefined,
        signal,
        scope,
      ),
    correct: (id, input, signal) =>
      studentEmailRequest(
        `/task-reviews/${encodeURIComponent(id)}`,
        readStudentEmailReview,
        "PATCH",
        input,
        signal,
        scope,
      ),
    accept: (id, input, signal) =>
      studentEmailRequest(
        `/task-reviews/${encodeURIComponent(id)}/accept-task`,
        readStudentEmailTaskReceipt,
        "POST",
        input,
        signal,
        scope,
      ),
    erase: (id, revision, signal) =>
      studentEmailRequest(
        `/task-reviews/${encodeURIComponent(id)}`,
        readStudentEmailErase,
        "DELETE",
        { revision },
        signal,
        scope,
      ),
    forget: (sourceId, signal) =>
      studentEmailRequest(
        `/task-sources/${encodeURIComponent(sourceId)}`,
        readStudentEmailForget,
        "DELETE",
        undefined,
        signal,
        scope,
      ),
  };
}

export function prepareOutlookTask(
  accountKey: string,
  connectionId: string,
  sourceMessageId: string,
  requestId: string,
  signal?: AbortSignal,
) {
  return studentEmailRequest(
    "/task-reviews",
    readStudentEmailReview,
    "POST",
    {
      request_id: requestId,
      connection_id: connectionId,
      source_message_id: sourceMessageId,
      confirmed: true,
    },
    signal,
    { accountKey, namespace: "outlook" },
  );
}
