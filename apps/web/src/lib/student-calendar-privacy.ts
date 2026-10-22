/** Content-free notices retire availability shown in another page or tab. */
export const CALENDAR_ACCESS_CHANGED_EVENT = "navox-calendar-access-changed";
const channelName = "navox-calendar-availability";
const schema = "navox-calendar-access.v1";

export function notifyCalendarAccessChanged() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(CALENDAR_ACCESS_CHANGED_EVENT));
  let channel: BroadcastChannel | null = null;
  try {
    if (typeof BroadcastChannel !== "undefined") {
      channel = new BroadcastChannel(channelName);
      channel.postMessage({ schema, type: "changed" });
    }
  } catch {
    // The local display has already been cleared. Other tabs expire shortly.
  } finally {
    channel?.close();
  }
}

export function observeCalendarAccessChanges(clear: () => void) {
  if (typeof window === "undefined") return () => {};
  let channel: BroadcastChannel | null = null;
  const changed = (event: MessageEvent<unknown>) => {
    const value = event.data as { schema?: unknown; type?: unknown } | null;
    if (value?.schema === schema && value.type === "changed") clear();
  };
  window.addEventListener(CALENDAR_ACCESS_CHANGED_EVENT, clear);
  try {
    if (typeof BroadcastChannel !== "undefined") {
      channel = new BroadcastChannel(channelName);
      channel.addEventListener("message", changed);
    }
  } catch {
    channel = null;
  }
  return () => {
    window.removeEventListener(CALENDAR_ACCESS_CHANGED_EVENT, clear);
    channel?.removeEventListener("message", changed);
    channel?.close();
  };
}
