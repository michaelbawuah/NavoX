export const VOX_CALENDAR_REVIEW_TOPIC = "navox.vox.calendar-review.v1";
export type VoxCalendarReview = {
  kind: "calendar-event";
  event_id: string;
  status:
    | "pending"
    | "executing"
    | "uncertain"
    | "created"
    | "changed"
    | "undoing"
    | "undone"
    | "failed";
  voice_approval_available: boolean;
  destination: "/connections#calendar";
};
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export function parseVoxCalendarReview(
  data: Uint8Array,
): VoxCalendarReview | null {
  if (data.byteLength > 1024) return null;
  try {
    const value = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(data),
    );
    if (
      !value ||
      typeof value !== "object" ||
      Array.isArray(value) ||
      Object.keys(value).length !== 5 ||
      Object.keys(value).some(
        (key) =>
          ![
            "kind",
            "event_id",
            "status",
            "voice_approval_available",
            "destination",
          ].includes(key),
      ) ||
      value.kind !== "calendar-event" ||
      typeof value.event_id !== "string" ||
      !UUID.test(value.event_id) ||
      ![
        "pending",
        "executing",
        "uncertain",
        "created",
        "changed",
        "undoing",
        "undone",
        "failed",
      ].includes(value.status) ||
      typeof value.voice_approval_available !== "boolean" ||
      value.destination !== "/connections#calendar"
    )
      return null;
    return value;
  } catch {
    return null;
  }
}
