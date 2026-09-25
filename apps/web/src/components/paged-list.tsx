"use client";

import { type ReactNode, useState } from "react";
import styles from "./today-workspace.module.css";

export function PagedList({
  items,
  label,
}: {
  items: ReactNode[];
  label: string;
}) {
  const [requestedPage, setPage] = useState(0);
  const pageSize = 6;
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const page = Math.min(requestedPage, pages - 1);
  const start = page * pageSize;
  return (
    <>
      <div className={styles.itemList}>
        {items.slice(start, start + pageSize)}
      </div>
      {pages > 1 && (
        <nav className={styles.pagination} aria-label={`${label} pages`}>
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            Previous
          </button>
          <span aria-live="polite">
            {start + 1}–{Math.min(start + pageSize, items.length)} of{" "}
            {items.length}
          </span>
          <button
            type="button"
            disabled={page === pages - 1}
            onClick={() => setPage(page + 1)}
          >
            Next
          </button>
        </nav>
      )}
    </>
  );
}
