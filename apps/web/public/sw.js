/* NavoX never caches authenticated pages, API responses, or private content. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) =>
  event.waitUntil(self.clients.claim()),
);
// Deliberately no fetch handler: every page and API request goes to the network.
self.addEventListener("push", (event) => {
  let payload;
  try {
    payload = event.data?.json();
  } catch {
    return;
  }
  if (
    !payload ||
    typeof payload !== "object" ||
    typeof payload.tag !== "string" ||
    !/^class-[a-f0-9]{26}$/.test(payload.tag)
  )
    return;
  // Enforce generic lock-screen copy here too. Neither endpoints nor class names
  // from untrusted event data can become a message, external link, or click URL.
  event.waitUntil(
    self.registration.showNotification("NavoX · Class reminder", {
      body: "You have an upcoming class. Open NavoX to check your schedule.",
      tag: payload.tag,
      icon: "/icons/navox-192.png",
      badge: "/icons/navox-192.png",
      data: { url: "/classes" },
      renotify: false,
    }),
  );
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    (async () => {
      const target = new URL("/classes", self.location.origin).href;
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const client of windows) {
        if (
          new URL(client.url).origin === self.location.origin &&
          "focus" in client
        ) {
          await client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })(),
  );
});
