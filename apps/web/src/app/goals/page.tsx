import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AccountWorkspace } from "../../components/account-workspace";
import { productFeatures } from "../../lib/product-features";

export const metadata: Metadata = { title: "Your goals · NavoX" };
export default function GoalsPage() {
  if (!productFeatures.studentGoals) notFound();
  return <AccountWorkspace view="goals" />;
}
