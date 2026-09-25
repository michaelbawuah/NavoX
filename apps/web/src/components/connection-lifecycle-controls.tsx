"use client";

import { useState } from "react";
import {
  canManageLifecycle,
  type LifecycleAction,
  lifecycleConfirmation,
  type ManagedConnection,
} from "../lib/connection-management";
import styles from "./connections-panel.module.css";

export function ConnectionLifecycleControls({
  connection,
  pending,
  onConfirm,
}: {
  connection: ManagedConnection;
  pending: boolean;
  onConfirm: (action: LifecycleAction) => Promise<boolean>;
}) {
  const [selected, setSelected] = useState<LifecycleAction | null>(null);
  const [typed, setTyped] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const canDisconnect = canManageLifecycle(connection, "disconnect");
  const canDelete = canManageLifecycle(connection, "delete-data");

  function select(action: LifecycleAction | null) {
    setSelected(action);
    setTyped("");
  }

  async function submit() {
    if (
      !selected ||
      submitting ||
      pending ||
      typed !== lifecycleConfirmation(selected) ||
      !canManageLifecycle(connection, selected)
    )
      return;
    setSubmitting(true);
    try {
      if (await onConfirm(selected)) select(null);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className={styles.lifecycle}>
      <div className={styles.actions}>
        <button
          type="button"
          disabled={pending || submitting || !canDisconnect}
          onClick={() => select("disconnect")}
        >
          Disconnect
        </button>
        <button
          type="button"
          disabled={pending || submitting || !canDelete}
          onClick={() => select("delete-data")}
        >
          Delete learned data
        </button>
      </div>
      {selected && canManageLifecycle(connection, selected) ? (
        <fieldset
          className={styles.confirmation}
          aria-label="Confirm connection action"
        >
          <h4>
            {selected === "disconnect"
              ? "Disconnect this account?"
              : "Permanently delete this connection’s learned data?"}
          </h4>
          <p>
            {connection.name}
            {connection.account_label ? ` · ${connection.account_label}` : ""}
          </p>
          <p>
            {selected === "disconnect"
              ? "NavoX will stop future source access and sync. Saved learned data remains until you choose to delete it. Requests already sent to the provider cannot be undone."
              : "NavoX will remove this connection’s source evidence and knowledge supported only by it. Knowledge supported by other sources remains. This action cannot be undone."}
          </p>
          <label>
            Type <strong>{lifecycleConfirmation(selected)}</strong> to confirm
            <input
              type="text"
              autoComplete="off"
              spellCheck={false}
              value={typed}
              disabled={pending || submitting}
              onChange={(event) => setTyped(event.target.value)}
            />
          </label>
          <div className={styles.actions}>
            <button
              type="button"
              disabled={
                pending ||
                submitting ||
                typed !== lifecycleConfirmation(selected)
              }
              onClick={() => void submit()}
            >
              {submitting
                ? "Working…"
                : selected === "disconnect"
                  ? "Confirm disconnect"
                  : "Confirm deletion"}
            </button>
            <button
              type="button"
              disabled={pending || submitting}
              onClick={() => select(null)}
            >
              Cancel
            </button>
          </div>
        </fieldset>
      ) : null}
      <p className={styles.footnote}>
        {connection.health === "DISCONNECTED"
          ? "This connection is disconnected. Learned data can be removed when deletion is available."
          : "Pause keeps access and learned data. Disconnect stops future source access; deleting learned data is a separate step."}
      </p>
    </div>
  );
}
