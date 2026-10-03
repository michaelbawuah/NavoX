export interface Account {
  id: string;
  email: string;
  display_name: string | null;
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

export async function restoreAccountAccess(apiBaseUrl: string): Promise<{
  account: Account | null;
  connections: GoogleConnection[];
  connectionsError: string;
}> {
  const response = await fetch(`${apiBaseUrl}/auth/me`, {
    credentials: "include",
  });
  if (!response.ok)
    return { account: null, connections: [], connectionsError: "" };
  const account = (await response.json()) as Account;
  try {
    const connectionResponse = await fetch(`${apiBaseUrl}/connections/google`, {
      credentials: "include",
    });
    if (!connectionResponse.ok) throw new Error("Connections unavailable");
    return {
      account,
      connections: (await connectionResponse.json()) as GoogleConnection[],
      connectionsError: "",
    };
  } catch {
    return {
      account,
      connections: [],
      connectionsError:
        "Connected apps could not be loaded. Open Connections to retry.",
    };
  }
}

export async function authenticate(
  apiBaseUrl: string,
  mode: "register" | "login",
  credentials: { email: string; password: string; display_name?: string },
): Promise<Account> {
  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl}/auth/${mode}`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(credentials),
    });
  } catch {
    throw new Error("Could not reach NavoX. Please try again.");
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : "Something went wrong. Please try again.",
    );
  }
  return (await response.json()) as Account;
}
