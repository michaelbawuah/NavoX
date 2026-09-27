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
