import { API_BASE_URL } from "./config.js";

const SESSION_KEY = "navoxExtensionSession";

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch(() => undefined);
});

async function storedSession() {
  const result = await chrome.storage.local.get(SESSION_KEY);
  const session = result[SESSION_KEY];
  if (!session?.accessToken) return null;
  if (session.expiresAt && Date.parse(session.expiresAt) <= Date.now()) {
    await chrome.storage.local.remove(SESSION_KEY);
    return null;
  }
  return session;
}

async function setSession(login) {
  const session = {
    accessToken: login.access_token,
    expiresAt: login.expires_at,
    account: login.account,
  };
  await chrome.storage.local.set({ [SESSION_KEY]: session });
  return session;
}

async function clearSession() {
  await chrome.storage.local.remove(SESSION_KEY);
}

async function api(path, options = {}) {
  const session = await storedSession();
  const headers = new Headers(options.headers ?? {});
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (session?.accessToken) {
    headers.set("Authorization", `Bearer ${session.accessToken}`);
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });
  const body = await response.json().catch(() => null);
  if (response.status === 401) {
    await clearSession();
  }
  if (!response.ok) {
    const detail =
      typeof body?.detail === "string"
        ? body.detail
        : body?.detail?.message ?? "NavoX request failed";
    throw new Error(detail);
  }
  return body;
}

async function dashboard(timezone) {
  const zone = encodeURIComponent(timezone || "UTC");
  const [account, today, briefing, agent, approvals, plans] = await Promise.all([
    api("/auth/me"),
    api(`/today?timezone=${zone}`),
    api(`/proactive/briefing?timezone=${zone}`),
    api("/agent/state"),
    api("/actions?status=awaiting_approval&limit=5"),
    api("/plans?limit=5"),
  ]);
  return { account, today, briefing, agent, approvals, plans };
}

async function handleMessage(message) {
  switch (message?.type) {
    case "AUTH_STATUS": {
      const session = await storedSession();
      if (!session) return { authenticated: false };
      try {
        const account = await api("/auth/me");
        return { authenticated: true, account };
      } catch {
        return { authenticated: false };
      }
    }
    case "LOGIN": {
      const login = await api("/auth/extension/login", {
        method: "POST",
        body: JSON.stringify({
          email: message.email,
          password: message.password,
        }),
      });
      const session = await setSession(login);
      return { account: session.account };
    }
    case "LOGOUT": {
      try {
        await api("/auth/extension/logout", { method: "POST" });
      } finally {
        await clearSession();
      }
      return { signedOut: true };
    }
    case "DASHBOARD":
      return dashboard(message.timezone);
    case "QUERY":
      return api("/today/query", {
        method: "POST",
        body: JSON.stringify({
          query: message.query,
          timezone: message.timezone,
        }),
      });
    case "HANDLE":
      return api(`/commitments/${message.commitmentId}/handle`, {
        method: "POST",
        body: JSON.stringify({
          request_id: crypto.randomUUID(),
          goal: message.goal ?? null,
        }),
      });
    case "PLAN":
      return api(`/plans/${message.planId}`);
    case "APPROVE_ACTION":
      return api(`/actions/${message.actionId}/approve`, {
        method: "POST",
        body: JSON.stringify({ request_id: crypto.randomUUID() }),
      });
    case "REJECT_ACTION":
      return api(`/actions/${message.actionId}/reject`, {
        method: "POST",
        body: JSON.stringify({ request_id: crypto.randomUUID() }),
      });
    case "SNOOZE_SIGNAL":
      return api(`/proactive/signals/${message.signalId}/snooze`, {
        method: "POST",
        body: JSON.stringify({
          until: new Date(Date.now() + 4 * 60 * 60 * 1000).toISOString(),
        }),
      });
    case "DISMISS_SIGNAL":
      return api(`/proactive/signals/${message.signalId}/dismiss`, {
        method: "POST",
      });
    default:
      throw new Error("Unsupported extension request");
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((error) =>
      sendResponse({
        ok: false,
        error: error instanceof Error ? error.message : "NavoX request failed",
      }),
    );
  return true;
});
