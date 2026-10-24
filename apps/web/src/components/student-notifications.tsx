"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  type ClassAlertStatus,
  classAlertRequest,
  endpointDigest,
  NotificationRequestError,
  notificationError,
  pushSupported,
  readAlertStatus,
  vapidKey,
} from "../lib/student-notifications";
import styles from "./student-notifications.module.css";

export function StudentAppRegistration() {
  useEffect(() => {
    if (window.isSecureContext && "serviceWorker" in navigator) {
      void navigator.serviceWorker
        .register("/sw.js", { scope: "/", updateViaCache: "none" })
        .catch(() => {});
    }
  }, []);
  return null;
}

export function StudentNotifications({ accountId }: { accountId: string }) {
  return <NotificationControls key={accountId} accountKey={accountId} />;
}

function NotificationControls({ accountKey }: { accountKey: string }) {
  const [status, setStatus] = useState<ClassAlertStatus | null>(null);
  const [supported, setSupported] = useState(false);
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const request = useRef(0);
  const id = useId();
  const refresh = useCallback(async () => {
    const current = ++request.current;
    try {
      const next = readAlertStatus(
        await classAlertRequest("", "GET", undefined, accountKey),
      );
      let savedId: string | null = null;
      if (pushSupported()) {
        const registration = await navigator.serviceWorker.getRegistration("/");
        const subscription = await registration?.pushManager.getSubscription();
        if (subscription && Notification.permission === "granted") {
          const digest = await endpointDigest(subscription.endpoint);
          savedId =
            next.devices.find((device) => device.endpoint_hash === digest)
              ?.id ?? null;
        }
      }
      if (current === request.current) {
        setStatus(next);
        setDeviceId(savedId);
      }
    } catch (cause) {
      if (current === request.current)
        setError(
          notificationError(
            cause,
            "Your reminders couldn’t load. Please try again.",
          ),
        );
    }
  }, [accountKey]);
  useEffect(() => {
    setSupported(pushSupported());
    void refresh();
    return () => {
      ++request.current;
    };
  }, [refresh]);

  async function saveSettings(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!status) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await classAlertRequest(
        "/settings",
        "PUT",
        {
          enabled: data.has("enabled"),
          next_class: data.has("next_class"),
          leave_now: data.has("leave_now"),
          lead_minutes: Number(data.get("lead_minutes")),
          quiet_start: data.get("quiet_start"),
          quiet_end: data.get("quiet_end"),
          daily_cap: Number(data.get("daily_cap")),
        },
        accountKey,
      );
      setNotice("Class reminder settings saved.");
    } catch (cause) {
      setError(
        notificationError(
          cause,
          "Your changes couldn’t be confirmed. Check your reminders before trying again.",
        ),
      );
    } finally {
      await refresh();
      setBusy(false);
    }
  }
  async function enableDevice() {
    if (!status?.configured || !status.public_key || !pushSupported()) return;
    // This function runs only from the explicit button. Request permission before
    // any network await so Safari retains the user's direct activation.
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const permission = Notification.requestPermission();
      if ((await permission) !== "granted")
        throw new NotificationRequestError(
          "Notifications are off in your browser. You can change that in your browser or device settings.",
        );
      const registration = await navigator.serviceWorker.register("/sw.js", {
        scope: "/",
        updateViaCache: "none",
      });
      await navigator.serviceWorker.ready;
      let subscription = await registration.pushManager.getSubscription();
      if (!subscription)
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: vapidKey(status.public_key),
        });
      await classAlertRequest(
        "/subscriptions",
        "POST",
        subscription.toJSON(),
        accountKey,
      );
      setNotice(
        "This device is connected. Save your reminder settings below to turn reminders on.",
      );
      await refresh();
    } catch (cause) {
      setError(
        notificationError(
          cause,
          "This device couldn’t connect. Check notification permissions in your browser or device settings, then try again.",
        ),
      );
    } finally {
      setBusy(false);
    }
  }
  async function removeDevice(identifier: string, thisDevice: boolean) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await classAlertRequest(
        `/subscriptions/${identifier}`,
        "DELETE",
        undefined,
        accountKey,
      );
      if (thisDevice && pushSupported()) {
        const registration = await navigator.serviceWorker.getRegistration("/");
        const subscription = await registration?.pushManager.getSubscription();
        await subscription?.unsubscribe();
      }
      setNotice(
        "Saved device removed. NavoX will stop sending class reminders to it.",
      );
      await refresh();
    } catch (cause) {
      setError(
        notificationError(
          cause,
          "Removing this device couldn’t be confirmed. Check its connection before trying again.",
        ),
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={styles.panel} aria-labelledby={`${id}-heading`}>
      <div>
        <p className={styles.eyebrow}>A little heads-up</p>
        <h2 id={`${id}-heading`}>Class reminders</h2>
        <p>
          Get a quiet nudge before your next class. Course names and rooms stay
          off your lock screen.
        </p>
      </div>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!status ? (
        <button type="button" onClick={() => void refresh()} disabled={busy}>
          Try again
        </button>
      ) : (
        <>
          <p className={styles.note}>{status.notice}</p>
          <p className={styles.note}>
            On iPhone or iPad, use Share → Add to Home Screen, then open NavoX
            from its icon before enabling notifications. Web reminders depend on
            your device and cannot replace a wake-up alarm.
          </p>
          {!supported && (
            <p className={styles.note}>
              This browser does not offer web push here. Install NavoX on your
              Home Screen or use a supported browser on HTTPS.
            </p>
          )}
          <div className={styles.deviceRow}>
            <span>
              {deviceId
                ? "This device is connected"
                : "This device is not connected"}
            </span>
            <button
              type="button"
              disabled={busy || !supported || !status.configured}
              onClick={() =>
                deviceId
                  ? void removeDevice(deviceId, true)
                  : void enableDevice()
              }
            >
              {deviceId ? "Turn off on this device" : "Enable on this device"}
            </button>
          </div>
          <form
            key={`${status.settings.enabled}-${status.settings.lead_minutes}-${status.settings.quiet_start}-${status.settings.quiet_end}-${status.settings.daily_cap}-${status.settings.next_class}-${status.settings.leave_now}`}
            onSubmit={(event) => void saveSettings(event)}
          >
            <fieldset disabled={busy}>
              <legend className={styles.legend}>Your reminders</legend>
              <label className={styles.check}>
                <input
                  name="enabled"
                  type="checkbox"
                  defaultChecked={status.settings.enabled}
                  disabled={!status.configured}
                />
                Turn on class reminders
              </label>
              <label className={styles.check}>
                <input
                  name="next_class"
                  type="checkbox"
                  defaultChecked={status.settings.next_class}
                />
                Next class
              </label>
              <label className={styles.check}>
                <input
                  name="leave_now"
                  type="checkbox"
                  defaultChecked={status.settings.leave_now}
                />
                Leave now, using only walk times I saved
              </label>
              <div className={styles.fields}>
                <label htmlFor={`${id}-lead`}>
                  Remind me before class
                  <input
                    id={`${id}-lead`}
                    name="lead_minutes"
                    type="number"
                    min="1"
                    max="60"
                    defaultValue={status.settings.lead_minutes}
                    required
                  />
                  <span>minutes</span>
                </label>
                <label htmlFor={`${id}-quiet-start`}>
                  Quiet hours start
                  <input
                    id={`${id}-quiet-start`}
                    name="quiet_start"
                    type="time"
                    defaultValue={status.settings.quiet_start}
                    required
                  />
                </label>
                <label htmlFor={`${id}-quiet-end`}>
                  Quiet hours end
                  <input
                    id={`${id}-quiet-end`}
                    name="quiet_end"
                    type="time"
                    defaultValue={status.settings.quiet_end}
                    required
                  />
                </label>
                <label htmlFor={`${id}-cap`}>
                  Maximum per day
                  <input
                    id={`${id}-cap`}
                    name="daily_cap"
                    type="number"
                    min="1"
                    max="12"
                    defaultValue={status.settings.daily_cap}
                    required
                  />
                </label>
              </div>
              <p className={styles.note}>
                Times use {status.timezone}. Reminders within 5 minutes are
                bundled. Quiet hours skip reminders; they do not arrive in a
                burst later. Signing out disconnects that session's reminders.
              </p>
              <button type="submit">
                {busy ? "Saving…" : "Save reminders"}
              </button>
            </fieldset>
          </form>
          {status.devices.some((device) => device.id !== deviceId) && (
            <details>
              <summary>
                Other saved devices (
                {
                  status.devices.filter((device) => device.id !== deviceId)
                    .length
                }
                )
              </summary>
              {status.devices
                .filter((device) => device.id !== deviceId)
                .map((device, index) => (
                  <div className={styles.deviceRow} key={device.id}>
                    <span>Saved device {index + 1}</span>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void removeDevice(device.id, false)}
                    >
                      Remove device
                    </button>
                  </div>
                ))}
            </details>
          )}
        </>
      )}
    </section>
  );
}
