/** Apple credentials are sent only in an explicit, account-bound request body. */
import {
  type CalendarCommand,
  CalendarRequestError,
  createStudentCalendarApi,
  createStudentCalendarTransport,
  readCalendarCommand,
  type StudentCalendarTransportOptions,
} from "./student-calendar-api";

export const APPLE_CALENDAR_CONSENT = "apple-calendar-availability.v1";
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export interface AppleCalendarGrant {
  id: string;
  provider: "icloud";
  connection_id: string;
  account_label: string | null;
  status: "connected" | "unavailable";
  revision: number;
}
export interface AppleCalendarView {
  generation: number;
  revision: number;
  consent_version: typeof APPLE_CALENDAR_CONSENT;
  configured: boolean;
  grants: AppleCalendarGrant[];
  changes_applied: false;
}
export interface AppleCalendarConnect extends CalendarCommand {
  consent_version: typeof APPLE_CALENDAR_CONSENT;
  confirmed: true;
  apple_id: string;
  app_password: string;
}
export interface AppleCalendarApi {
  account(signal: AbortSignal): Promise<string>;
  view(signal: AbortSignal): Promise<AppleCalendarView>;
  connect(
    input: AppleCalendarConnect,
    signal: AbortSignal,
  ): Promise<AppleCalendarView>;
  disconnect(
    grantId: string,
    input: CalendarCommand,
    signal: AbortSignal,
  ): Promise<AppleCalendarView>;
}

function invalid(): never {
  throw new CalendarRequestError("invalid-response");
}
function object(
  value: unknown,
  keys: readonly string[],
): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  const result = value as Record<string, unknown>;
  if (
    Object.keys(result).length !== keys.length ||
    Object.keys(result).some((key) => !keys.includes(key))
  )
    return invalid();
  return result;
}
function revision(value: unknown, min = 0): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < min ||
    value > 2147483647
  )
    return invalid();
  return value;
}
function id(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value)) return invalid();
  return value.toLowerCase();
}

export function readAppleCalendarView(value: unknown): AppleCalendarView {
  const result = object(value, [
    "generation",
    "revision",
    "consent_version",
    "configured",
    "grants",
    "changes_applied",
  ]);
  if (
    result.consent_version !== APPLE_CALENDAR_CONSENT ||
    result.changes_applied !== false ||
    typeof result.configured !== "boolean" ||
    !Array.isArray(result.grants) ||
    result.grants.length > 8
  )
    return invalid();
  const grants = result.grants.map((item): AppleCalendarGrant => {
    const grant = object(item, [
      "id",
      "provider",
      "connection_id",
      "account_label",
      "status",
      "revision",
    ]);
    if (
      grant.provider !== "icloud" ||
      !["connected", "unavailable"].includes(grant.status as string)
    )
      return invalid();
    if (
      grant.account_label !== null &&
      (typeof grant.account_label !== "string" ||
        grant.account_label.length > 320 ||
        [...grant.account_label].some((char) => {
          const code = char.charCodeAt(0);
          return code < 32 || (code >= 127 && code <= 159);
        }))
    )
      return invalid();
    return {
      id: id(grant.id),
      provider: "icloud",
      connection_id: id(grant.connection_id),
      account_label: grant.account_label as string | null,
      status: grant.status as AppleCalendarGrant["status"],
      revision: revision(grant.revision, 1),
    };
  });
  if (
    new Set(grants.map((grant) => grant.id)).size !== grants.length ||
    new Set(grants.map((grant) => grant.connection_id)).size !== grants.length
  )
    return invalid();
  return {
    generation: revision(result.generation),
    revision: revision(result.revision),
    consent_version: APPLE_CALENDAR_CONSENT,
    configured: result.configured,
    grants,
    changes_applied: false,
  };
}

export function createAppleCalendarApi(
  accountId: string,
  options: StudentCalendarTransportOptions = {},
): AppleCalendarApi {
  const send = createStudentCalendarTransport(accountId, options);
  return {
    account: createStudentCalendarApi(accountId, options).account,
    view: (signal) =>
      send("/student/calendars/apple", readAppleCalendarView, signal),
    connect(input, signal) {
      if (
        typeof input.apple_id !== "string" ||
        typeof input.app_password !== "string"
      )
        return invalid();
      const command = readCalendarCommand({
        generation: input.generation,
        revision: input.revision,
        request_id: input.request_id,
      });
      const apple_id = input.apple_id.trim().toLowerCase();
      if (
        input.confirmed !== true ||
        input.consent_version !== APPLE_CALENDAR_CONSENT ||
        apple_id.length > 256 ||
        !/^[^\s:@]+@[^\s:@]+\.[^\s:@]+$/.test(apple_id) ||
        !/^[a-z]{4}(?:-[a-z]{4}){3}$/.test(input.app_password)
      )
        return invalid();
      return send(
        "/student/calendars/apple/connect",
        readAppleCalendarView,
        signal,
        {
          ...command,
          consent_version: APPLE_CALENDAR_CONSENT,
          confirmed: true,
          apple_id,
          app_password: input.app_password,
        },
        65536,
        40_000,
      );
    },
    disconnect: (grantId, input, signal) =>
      send(
        `/student/calendars/apple/${id(grantId)}/disconnect`,
        readAppleCalendarView,
        signal,
        readCalendarCommand(input),
      ),
  };
}
