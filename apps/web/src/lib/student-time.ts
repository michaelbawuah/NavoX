/** Student-local display only. Stored instants remain UTC. */
export function studentLocalTime(value: Date | string | number, zone: string) {
  const instant = new Date(value);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "numeric",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(instant);
  const part = (kind: Intl.DateTimeFormatPartTypes) =>
    parts.find((p) => p.type === kind)?.value ?? "";
  const hour = Number(part("hour"));
  const period =
    hour < 5
      ? "late-night"
      : hour < 12
        ? "morning"
        : hour < 17
          ? "afternoon"
          : "evening";
  return {
    hour,
    minute: Number(part("minute")),
    period,
    date: `${part("year")}-${part("month")}-${part("day")}`,
    greeting: period === "late-night" ? "Still up" : `Good ${period}`,
    clock: new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      hour: "numeric",
      minute: "2-digit",
    }).format(instant),
    header: new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      weekday: "short",
      month: "short",
      day: "numeric",
    }).format(instant),
  };
}

export const STUDENT_TIMEZONE_CHANGED = "navox-student-timezone-changed";
export function notifyStudentTimezoneChanged(
  accountKey: string,
  timezone: string,
) {
  window.dispatchEvent(
    new CustomEvent(STUDENT_TIMEZONE_CHANGED, {
      detail: { accountKey, timezone },
    }),
  );
}
