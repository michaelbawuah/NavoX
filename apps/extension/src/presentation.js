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


export function priorityLabel(priority) {
  const labels = {
    1: "Low",
    2: "Moderate",
    3: "Standard",
    4: "Important",
    5: "Critical",
  };
  return labels[priority] ?? "Standard";
}

export function deadlineTone(item, now = Date.now()) {
  if (item.status === "completed") return "completed";
  if (!item.due_at) return "upcoming";
  const hours = (new Date(item.due_at).getTime() - now) / (60 * 60 * 1000);
  return hours <= 48 ? "urgent" : "upcoming";
}

export function deadlineItems(today) {
  const active = uniqueTodayItems(today).filter((item) => item.type === "deadline");
  const completed = (today?.completed_recently ?? []).filter(
    (item) => item.type === "deadline",
  );
  return [...active, ...completed];
}

export function urgencyLabel(item, now = Date.now()) {
  const tone = deadlineTone(item, now);
  if (tone === "urgent") return "Urgent";
  if (tone === "completed") return "Completed";
  return "Upcoming";
}
