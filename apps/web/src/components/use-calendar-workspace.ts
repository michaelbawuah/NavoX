"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { observeAccountSession } from "../lib/account-session";
import {
  CalendarRequestError,
  type StudentCalendarApi,
} from "../lib/student-calendar-api";

/** Private data lives only in this visible, verified account epoch. */
export function useCalendarWorkspace(
  accountId: string,
  api: StudentCalendarApi,
  clearPrivate: () => void,
) {
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const current = useRef({
    mounted: false,
    ready: false,
    busy: false,
    epoch: 0,
    request: null as AbortController | null,
  });
  const clear = useRef(clearPrivate);
  clear.current = clearPrivate;

  const cancel = useCallback(() => {
    const state = current.current;
    ++state.epoch;
    state.request?.abort();
    state.request = null;
    state.busy = false;
    if (state.mounted) setBusy(false);
  }, []);

  useEffect(() => {
    const state = current.current;
    state.mounted = true;
    state.ready = false;
    const suspend = () => {
      cancel();
      state.ready = false;
      setReady(false);
      setError("");
      clear.current();
    };
    const observer = observeAccountSession({
      checking: suspend,
      revoking: suspend,
      unavailable() {
        suspend();
        setError(
          "Your sign-in could not be checked. Reload before continuing.",
        );
      },
      restored(access) {
        const account = access.account;
        const matches =
          account !== null &&
          `${account.workspace.id}:${account.id}` === accountId;
        state.ready = matches;
        setReady(matches);
        if (!matches) {
          clear.current();
          setError("The account changed. Reload before checking calendars.");
        }
      },
    });
    const refresh = () => observer.refresh();
    window.addEventListener("pagehide", suspend);
    window.addEventListener("pageshow", refresh);
    return () => {
      state.mounted = false;
      state.ready = false;
      ++state.epoch;
      state.request?.abort();
      state.request = null;
      state.busy = false;
      observer.dispose();
      window.removeEventListener("pagehide", suspend);
      window.removeEventListener("pageshow", refresh);
    };
  }, [accountId, cancel]);

  const run = useCallback(
    async <T>(
      operation: (signal: AbortSignal) => Promise<T>,
      accept: (value: T) => void,
    ): Promise<boolean> => {
      const state = current.current;
      if (!state.mounted || !state.ready || state.busy) return false;
      const epoch = ++state.epoch;
      const request = new AbortController();
      state.request = request;
      state.busy = true;
      setBusy(true);
      setError("");
      const fresh = () =>
        state.mounted &&
        state.ready &&
        epoch === state.epoch &&
        !request.signal.aborted;
      const proveAccount = async () => {
        try {
          const owner = await api.account(request.signal);
          if (!fresh()) return false;
          if (owner === accountId) return true;
        } catch {
          if (!fresh()) return false;
        }
        cancel();
        state.ready = false;
        setReady(false);
        clear.current();
        setError(
          "Your account could not be verified. Reload before continuing.",
        );
        return false;
      };
      try {
        if (!(await proveAccount())) return false;
        const result = await operation(request.signal);
        if (!fresh() || !(await proveAccount()) || !fresh()) return false;
        accept(result);
        return true;
      } catch (cause) {
        if (fresh()) {
          if (
            cause instanceof CalendarRequestError &&
            cause.code === "account-changed"
          ) {
            clear.current();
            if (cause.status === 401) {
              state.ready = false;
              setReady(false);
            }
          }
          setError(
            cause instanceof Error
              ? cause.message
              : "Calendar access could not be checked. Try again.",
          );
        }
        return false;
      } finally {
        if (epoch === state.epoch) {
          state.request = null;
          state.busy = false;
          if (state.mounted) setBusy(false);
        }
      }
    },
    [accountId, api, cancel],
  );

  return { ready, busy, error, cancel, run, setError };
}
