export function uniqueTodayItems(today) {
  const seen = new Set();
  const sections = [
    ...(today?.needs_attention ?? []),
    ...(today?.coming_up ?? []),
    ...(today?.renewals ?? []),
    ...(today?.waiting_on ?? []),
  ];
  return sections.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

export function flattenSignals(briefing) {
  return [
    ...(briefing?.notify_now ?? []),
    ...(briefing?.briefing ?? []),
    ...(briefing?.dashboard ?? []),
  ];
}

export function actionSummary(action) {
  const payload = action?.payload ?? {};
  if (action?.provider === "gmail" && action?.action_type === "gmail.send") {
    return {
      title: typeof payload.subject === "string" ? payload.subject : "Prepared Gmail send",
      recipient: typeof payload.to === "string" ? payload.to : "Unknown recipient",
      body: typeof payload.body_text === "string" ? payload.body_text : "",
    };
  }
  return {
    title: action?.action_type ?? "Prepared action",
    recipient: action?.provider ?? "Provider",
    body: "",
  };
}

export function terminalPlan(status) {
  return ["completed", "blocked", "failed", "dispatch_failed"].includes(status);
}
