import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AccountWorkspace } from "../../components/account-workspace";
import { productFeatures } from "../../lib/product-features";

export const metadata: Metadata = { title: "Review your day · NavoX" };
export default function DayReviewPage() {
  if (!productFeatures.studentCoach) notFound();
  return <AccountWorkspace view="day-review" />;
}
