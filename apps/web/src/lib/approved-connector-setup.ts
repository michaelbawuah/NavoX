/** Public, curated options. Never include raw connector configuration here. */
export interface ApprovedServiceChoice {
  id: string;
  name: string;
  read_capabilities: string[];
  authentication: "api_token" | "none";
}

export type ApprovedServiceKind = "rest" | "mcp";

export function approvedServicePaths(kind: ApprovedServiceKind) {
  const base =
    kind === "rest" ? "/connectors/generic-rest-api" : "/connectors/mcp";
  return {
    options: `${base}/${kind === "rest" ? "configurations" : "servers"}`,
    connect: `${base}/connect`,
  };
}

export function approvedConnectBody(
  kind: ApprovedServiceKind,
  choice: ApprovedServiceChoice,
  capabilities: string[],
  token: string | undefined,
  requestId: string,
) {
  return {
    ...(kind === "rest"
      ? { configuration_id: choice.id }
      : { server_id: choice.id }),
    capabilities,
    ...(token ? { token } : {}),
    confirmed: true,
    request_id: requestId,
  };
}

const capabilityPattern = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/;

export function approvedServiceChoices(
  value: unknown,
): ApprovedServiceChoice[] {
  if (!Array.isArray(value) || value.length > 100) {
    throw new Error("Approved connector choices are unavailable.");
  }
  const seen = new Set<string>();
  return value.map((item: unknown) => {
    if (typeof item !== "object" || item === null) {
      throw new Error("Approved connector choices are unavailable.");
    }
    const record = item as Record<string, unknown>;
    const {
      id,
      name,
      read_capabilities: capabilities,
      authentication,
    } = record;
    if (
      typeof id !== "string" ||
      id.length < 1 ||
      id.length > 128 ||
      [...id].some((character) => {
        const code = character.codePointAt(0) ?? 0;
        return code < 32 || code === 127;
      }) ||
      seen.has(id) ||
      typeof name !== "string" ||
      name.length < 1 ||
      name.length > 120 ||
      (authentication !== "api_token" && authentication !== "none") ||
      !Array.isArray(capabilities) ||
      capabilities.length < 1 ||
      capabilities.length > 32 ||
      capabilities.some(
        (capability: unknown) =>
          typeof capability !== "string" ||
          capability.length > 160 ||
          !capabilityPattern.test(capability),
      ) ||
      new Set(capabilities).size !== capabilities.length
    ) {
      throw new Error("Approved connector choices are unavailable.");
    }
    seen.add(id);
    return {
      id,
      name,
      read_capabilities: capabilities as string[],
      authentication,
    };
  });
}

export function canConnectApprovedService(
  choice: ApprovedServiceChoice | null,
  selected: string[],
  consent: boolean,
  hasToken: boolean,
): boolean {
  return (
    choice !== null &&
    consent &&
    selected.length > 0 &&
    selected.every((name) => choice.read_capabilities.includes(name)) &&
    (choice.authentication === "none" || hasToken)
  );
}
