import Link from "next/link";

export function StudentDayReviewEntry({
  enabled = false,
}: {
  enabled?: boolean;
}) {
  if (!enabled) return null;
  return (
    <Link href="/day-review">
      <strong>Review your day</strong>
      <span>
        Morning, evening or week so far <span aria-hidden="true">→</span>
      </span>
    </Link>
  );
}
