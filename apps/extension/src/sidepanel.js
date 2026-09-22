import { WEB_APP_URL } from "./config.js";
import {
  actionSummary,
  flattenSignals,
  terminalPlan,
  uniqueTodayItems,
} from "./presentation.js";

const app = document.querySelector("#app");
const state = {
  authenticated: false,
  account: null,
  dashboard: null,
  busy: false,
  message: "",
  error: "",
  query: "",
  queryResult: null,
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
};

function element(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = String(options.text);
  if (options.type) node.type = options.type;
  if (options.href) node.href = options.href;
  if (options.target) node.target = options.target;
  if (options.rel) node.rel = options.rel;
  if (options.placeholder) node.placeholder = options.placeholder;
  if (options.value !== undefined) node.value = options.value;
  if (options.disabled) node.disabled = true;
  for (const child of children) node.append(child);
  return node;
}

function send(message) {
  return chrome.runtime.sendMessage(message).then((response) => {
    if (!response?.ok) throw new Error(response?.error ?? "NavoX request failed");
    return response.data;
  });
}

function setFeedback(message = "", error = "") {
  state.message = message;
  state.error = error;
}

function feedbackNode() {
  if (state.error) return element("p", { className: "error", text: state.error });
  if (state.message) return element("p", { className: "notice", text: state.message });
  return null;
}

function button(text, onClick, className = "") {
  const node = element("button", {
    className,
    text,
    type: "button",
    disabled: state.busy,
  });
  node.addEventListener("click", onClick);
  return node;
}

function brand() {
  const mark = element("div", { className: "brandMark" }, [
    element("span", { className: "gem" }),
    element("div", {}, [
      element("strong", { text: "NavoX" }),
      element("small", { text: "Operational intelligence" }),
    ]),
  ]);
  return element("header", { className: "brand" }, [
    mark,
    button("Refresh", () => void refresh(), "iconButton"),
  ]);
}

function loginView() {
  const form = element("form", { className: "login" });
  form.append(
    element("p", { className: "eyebrow", text: "NavoX side panel" }),
    element("h1", { text: "Your operational state, wherever you work." }),
    element("p", {
      text: "Sign in to the same NavoX account. The extension does not read the page you are viewing.",
    }),
  );
  const email = element("input", { type: "email", placeholder: "you@example.com" });
  email.required = true;
  const password = element("input", { type: "password", placeholder: "Password" });
  password.required = true;
  form.append(
    element("label", { text: "Email" }, [email]),
    element("label", { text: "Password" }, [password]),
  );
  const submit = element("button", { text: "Sign in", type: "submit" });
  form.append(submit);
  const feedback = feedbackNode();
  if (feedback) form.append(feedback);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.busy = true;
    setFeedback();
    render();
    try {
      const result = await send({
        type: "LOGIN",
        email: email.value.trim(),
        password: password.value,
      });
      state.authenticated = true;
      state.account = result.account;
      await refresh();
    } catch (error) {
      setFeedback("", error.message);
    } finally {
      state.busy = false;
      render();
    }
  });
  return form;
}

function metrics(today, briefing) {
  return element("div", { className: "metrics" }, [
    element("div", { className: "metric" }, [
      element("b", { text: today?.needs_attention?.length ?? 0 }),
      document.createTextNode("attention"),
    ]),
    element("div", { className: "metric" }, [
      element("b", { text: briefing?.notify_now?.length ?? 0 }),
      document.createTextNode("notify tier"),
    ]),
    element("div", { className: "metric" }, [
      element("b", { text: today?.waiting_on?.length ?? 0 }),
      document.createTextNode("waiting"),
    ]),
  ]);
}

async function handleCommitment(item) {
  state.busy = true;
  setFeedback();
  render();
  try {
    const plan = await send({
      type: "HANDLE",
      commitmentId: item.id,
      goal: `Safely handle: ${item.title}`,
    });
    setFeedback("Bounded plan started.");
    await watchPlan(plan.id);
    await refresh();
  } catch (error) {
    setFeedback("", error.message);
  } finally {
    state.busy = false;
    render();
  }
}

async function watchPlan(planId) {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const plan = await send({ type: "PLAN", planId });
    if (terminalPlan(plan.status)) return plan;
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  return null;
}

function todayPanel(today) {
  const items = uniqueTodayItems(today).slice(0, 6);
  const panel = element("section", { className: "panel" }, [
    element("div", {}, [
      element("p", { className: "eyebrow", text: "Today" }),
      element("h2", { className: "panelTitle", text: "What needs attention?" }),
    ]),
  ]);
  if (!items.length) {
    panel.append(element("p", { className: "empty", text: "No active commitments are surfacing." }));
    return panel;
  }
  for (const item of items) {
    const row = element("article", { className: "item" }, [
      element("span", {
        className: item.score >= 70 ? "badge attention" : "badge",
        text: item.type,
      }),
      element("h3", { text: item.title }),
      element("p", { text: item.reasons?.join(" · ") || "Active NavoX commitment" }),
      element("div", { className: "actions" }, [
        button("Handle this", () => void handleCommitment(item), "primary"),
      ]),
    ]);
    panel.append(row);
  }
  return panel;
}

async function signalMutation(signal, mode) {
  state.busy = true;
  setFeedback();
  render();
  try {
    await send({
      type: mode === "snooze" ? "SNOOZE_SIGNAL" : "DISMISS_SIGNAL",
      signalId: signal.id,
    });
    setFeedback(mode === "snooze" ? "Snoozed for four hours." : "Signal dismissed.");
    await refresh();
  } catch (error) {
    setFeedback("", error.message);
  } finally {
    state.busy = false;
    render();
  }
}

function proactivePanel(briefing) {
  const signals = flattenSignals(briefing).slice(0, 5);
  const panel = element("section", { className: "panel" }, [
    element("p", { className: "eyebrow", text: "Proactive briefing" }),
    element("h2", { className: "panelTitle", text: "Before it becomes a problem" }),
    element("p", { className: "headline", text: briefing?.headline ?? "No proactive briefing yet." }),
  ]);
  for (const signal of signals) {
    panel.append(
      element("article", { className: "signal" }, [
        element("span", {
          className: signal.tier === "notify_now" ? "badge attention" : "badge",
          text: `${signal.tier.replaceAll("_", " ")} · ${signal.attention_score}`,
        }),
        element("h3", { text: signal.what_happening }),
        element("p", { text: signal.why_matters }),
        element("div", { className: "actions" }, [
          button("Snooze 4h", () => void signalMutation(signal, "snooze")),
          button("Dismiss", () => void signalMutation(signal, "dismiss")),
        ]),
      ]),
    );
  }
  if (!signals.length) {
    panel.append(element("p", { className: "empty", text: "Nothing proactive needs surfacing." }));
  }
  return panel;
}

function queryPanel() {
  const input = element("input", {
    placeholder: "What am I forgetting?",
    value: state.query,
  });
  input.addEventListener("input", () => {
    state.query = input.value;
  });
  const ask = button("Ask", async () => {
    if (!state.query.trim()) return;
    state.busy = true;
    setFeedback();
    try {
      state.queryResult = await send({
        type: "QUERY",
        query: state.query.trim(),
        timezone: state.timezone,
      });
    } catch (error) {
      setFeedback("", error.message);
    } finally {
      state.busy = false;
      render();
    }
  }, "primary");
  const panel = element("section", { className: "panel" }, [
    element("p", { className: "eyebrow", text: "Ask NavoX" }),
    element("div", { className: "queryRow" }, [input, ask]),
  ]);
  if (state.queryResult) {
    panel.append(element("p", { className: "headline", text: state.queryResult.answer }));
    for (const detail of state.queryResult.details ?? []) {
      panel.append(element("p", { className: "empty", text: detail }));
    }
  }
  return panel;
}

async function decideAction(action, decision) {
  state.busy = true;
  setFeedback();
  render();
  try {
    await send({
      type: decision === "approve" ? "APPROVE_ACTION" : "REJECT_ACTION",
      actionId: action.id,
    });
    setFeedback(decision === "approve" ? "Exact action approved." : "Action rejected.");
    await refresh();
  } catch (error) {
    setFeedback("", error.message);
  } finally {
    state.busy = false;
    render();
  }
}

function approvalPanel(actions) {
  const panel = element("section", { className: "panel" }, [
    element("p", { className: "eyebrow", text: "Approval boundary" }),
    element("h2", { className: "panelTitle", text: "Exact actions awaiting you" }),
  ]);
  if (!actions?.length) {
    panel.append(element("p", { className: "empty", text: "No consequential action is awaiting approval." }));
    return panel;
  }
  for (const action of actions) {
    const summary = actionSummary(action);
    panel.append(
      element("article", { className: "approval" }, [
        element("span", { className: "badge attention", text: `${action.risk_level} · ${action.provider}` }),
        element("h3", { text: summary.title }),
        element("p", { text: `To: ${summary.recipient}` }),
        element("p", { text: summary.body }),
        element("p", {
          className: "empty",
          text: `Approval expires ${new Date(action.approval.expires_at).toLocaleString()}. The server verifies this exact payload hash before execution.`,
        }),
        element("div", { className: "actions" }, [
          button("Approve exact action", () => void decideAction(action, "approve"), "primary"),
          button("Reject", () => void decideAction(action, "reject"), "danger"),
        ]),
      ]),
    );
  }
  return panel;
}

function plansPanel(plans) {
  const panel = element("section", { className: "panel" }, [
    element("p", { className: "eyebrow", text: "Bounded agent" }),
    element("h2", { className: "panelTitle", text: "Recent plans" }),
  ]);
  for (const plan of plans?.slice(0, 4) ?? []) {
    panel.append(
      element("article", { className: "plan" }, [
        element("span", { className: "badge", text: plan.status }),
        element("h3", { text: plan.goal }),
        element("p", { text: `${plan.planner_version} · max ${plan.max_steps} steps` }),
      ]),
    );
  }
  if (!plans?.length) {
    panel.append(element("p", { className: "empty", text: "No recent plans." }));
  }
  return panel;
}

function dashboardView() {
  const data = state.dashboard;
  const container = document.createDocumentFragment();
  container.append(brand());
  const feedback = feedbackNode();
  if (feedback) container.append(feedback);
  if (!data) {
    container.append(element("div", { className: "skeleton" }), element("div", { className: "skeleton" }));
    return container;
  }

  const intro = element("section", { className: "panel" }, [
    element("div", { className: "topline" }, [
      element("div", {}, [
        element("p", { className: "eyebrow", text: data.agent.paused ? "Agent paused" : "Private workspace" }),
        element("h2", { className: "panelTitle", text: data.account.workspace.name }),
      ]),
      element("span", { className: data.agent.paused ? "badge attention" : "badge", text: data.agent.paused ? "paused" : "live" }),
    ]),
    metrics(data.today, data.briefing),
  ]);
  container.append(
    intro,
    queryPanel(),
    proactivePanel(data.briefing),
    todayPanel(data.today),
    approvalPanel(data.approvals),
    plansPanel(data.plans),
  );

  const footer = element("section", { className: "panel" });
  const link = element("a", {
    className: "link",
    text: "Open full NavoX workspace ↗",
    href: WEB_APP_URL,
    target: "_blank",
    rel: "noreferrer",
  });
  footer.append(
    link,
    button("Sign out of extension", () => void logout(), "quietButton"),
    element("p", {
      className: "empty",
      text: "This extension does not read the page you are viewing. Provider actions still use NavoX server-side policy and approval.",
    }),
  );
  container.append(footer);
  return container;
}

async function refresh() {
  if (!state.authenticated) return;
  state.busy = true;
  setFeedback();
  render();
  try {
    state.dashboard = await send({ type: "DASHBOARD", timezone: state.timezone });
    state.account = state.dashboard.account;
  } catch (error) {
    if (/authentication required/i.test(error.message)) {
      state.authenticated = false;
      state.dashboard = null;
    }
    setFeedback("", error.message);
  } finally {
    state.busy = false;
    render();
  }
}

async function logout() {
  state.busy = true;
  render();
  try {
    await send({ type: "LOGOUT" });
  } catch {
    // The service worker clears local session state even if revocation cannot be reached.
  } finally {
    state.authenticated = false;
    state.account = null;
    state.dashboard = null;
    state.busy = false;
    setFeedback("Signed out.");
    render();
  }
}

function render() {
  app.replaceChildren(state.authenticated ? dashboardView() : loginView());
}

async function bootstrap() {
  render();
  try {
    const status = await send({ type: "AUTH_STATUS" });
    state.authenticated = status.authenticated;
    state.account = status.account ?? null;
    if (state.authenticated) await refresh();
  } catch (error) {
    setFeedback("", error.message);
  }
  render();
}

void bootstrap();
