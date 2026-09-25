export const canvasPermissions = [
  ["academic.courses.read", "Read my active courses"],
  ["academic.assignments.read", "Read assignments and due dates"],
  ["academic.submissions.read", "Read my submission state (not grades)"],
  ["academic.announcements.read", "Read course announcements"],
  ["calendar.events.read", "Read course calendar context"],
] as const;
export function canvasSelection(values: string[]): string[] {
  const selected = new Set(values);
  if (
    !selected.has("academic.courses.read") ||
    selected.size !== values.length ||
    values.some((v) => !canvasPermissions.some(([key]) => key === v)) ||
    (selected.has("academic.submissions.read") &&
      !selected.has("academic.assignments.read"))
  ) {
    throw new Error(
      "Select supported read permissions and required course access.",
    );
  }
  return [...selected].sort();
}
export function canvasOrigin(value: unknown): string {
  if (typeof value !== "string")
    throw new Error("Canvas setup is unavailable.");
  const url = new URL(value);
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/" ||
    url.port
  )
    throw new Error("Canvas setup is unavailable.");
  return url.origin;
}
export function canvasAuthorizationUrl(value: unknown, origin: string): string {
  if (typeof value !== "string")
    throw new Error("Canvas authorization was not returned.");
  const url = new URL(value);
  if (
    url.origin !== canvasOrigin(origin) ||
    url.pathname !== "/login/oauth2/auth" ||
    url.username ||
    url.password ||
    url.hash ||
    !url.searchParams.get("state")
  ) {
    throw new Error("Canvas authorization URL is invalid.");
  }
  return url.toString();
}
