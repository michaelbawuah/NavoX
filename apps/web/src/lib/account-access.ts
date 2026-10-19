export interface Account {
  id: string;
  email: string;
  display_name: string | null;
  timezone?: string;
  workspace: {
    id: string;
    name: string;
    workspace_type: string;
  };
}

export interface GoogleConnection {
  id: string;
  provider: "google";
  external_email: string | null;
  status: string;
  granted_scopes: string[];
  last_checked_at: string | null;
  last_error: string | null;
}

export interface AccountAccess {
  account: Account | null;
  connections: GoogleConnection[];
  connectionsError: string;
}

const connectionsUnavailableMessage =
  "Connected apps could not be loaded. Retry to check your connections.";

export async function loadGoogleConnections(
  apiBaseUrl: string,
): Promise<GoogleConnection[]> {
  try {
    const response = await fetch(`${apiBaseUrl}/connections/google`, {
      credentials: "include",
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) throw new Error("Connections unavailable");
    const connections: unknown = await response.json();
    if (!Array.isArray(connections))
      throw new Error("Invalid connections response");
    return connections as GoogleConnection[];
  } catch {
    throw new Error(connectionsUnavailableMessage);
  }
}

async function accessForAccount(
  apiBaseUrl: string,
  account: Account,
): Promise<AccountAccess> {
  try {
    return {
      account,
      connections: await loadGoogleConnections(apiBaseUrl),
      connectionsError: "",
    };
  } catch {
    return {
      account,
      connections: [],
      connectionsError: connectionsUnavailableMessage,
    };
  }
}

export async function restoreAccountAccess(
  apiBaseUrl: string,
): Promise<AccountAccess> {
  const response = await fetch(`${apiBaseUrl}/auth/me`, {
    credentials: "include",
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok)
    return { account: null, connections: [], connectionsError: "" };
  const account = (await response.json()) as Account;
  return accessForAccount(apiBaseUrl, account);
}

type AuthMode = "register" | "login";
type AuthCredentials = {
  email: string;
  password: string;
  display_name?: string;
};

function passwordValidationMessage(mode: AuthMode): string {
  return mode === "register"
    ? "Use a password with 12 to 128 characters."
    : "Enter your password (up to 128 characters).";
}

function normalizeCredentials(
  mode: AuthMode,
  credentials: AuthCredentials,
): AuthCredentials {
  const email = credentials.email.trim();
  if (!email) throw new Error("Enter your email address.");
  const minimumPasswordLength = mode === "register" ? 12 : 1;
  if (
    credentials.password.length < minimumPasswordLength ||
    credentials.password.length > 128
  )
    throw new Error(passwordValidationMessage(mode));
  if (mode === "login") return { email, password: credentials.password };
  const displayName = credentials.display_name?.trim() ?? "";
  if (!displayName) throw new Error("Enter your name.");
  if (displayName.length > 256)
    throw new Error("Use a name with up to 256 characters.");
  return { email, password: credentials.password, display_name: displayName };
}

function authenticationError(detail: unknown, mode: AuthMode): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((issue: unknown) => {
      if (issue === null || typeof issue !== "object") return [];
      const { loc, type } = issue as { loc?: unknown; type?: unknown };
      if (!Array.isArray(loc)) return [];
      const field = loc[loc.length - 1];
      if (field === "email") return ["Enter a valid email address."];
      if (field === "password") return [passwordValidationMessage(mode)];
      if (field === "display_name")
        return [
          type === "string_too_long"
            ? "Use a name with up to 256 characters."
            : "Enter your name.",
        ];
      return [];
    });
    if (messages.length) return [...new Set(messages)].join(" ");
  }
  return "Something went wrong. Please try again.";
}

export async function authenticate(
  apiBaseUrl: string,
  mode: AuthMode,
  credentials: AuthCredentials,
): Promise<Account> {
  const payload = normalizeCredentials(mode, credentials);
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}/auth/${mode}`, {
      method: "POST",
      credentials: "include",
      signal: AbortSignal.timeout(10_000),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error("Could not reach NavoX. Please try again.");
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new Error(authenticationError(body?.detail, mode));
  }
  return (await response.json()) as Account;
}

export async function authenticateAccountAccess(
  apiBaseUrl: string,
  mode: AuthMode,
  credentials: AuthCredentials,
): Promise<AccountAccess> {
  const account = await authenticate(apiBaseUrl, mode, credentials);
  return accessForAccount(apiBaseUrl, account);
}
