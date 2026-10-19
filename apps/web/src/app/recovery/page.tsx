import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AccountWorkspace } from "../../components/account-workspace";
import { productFeatures } from "../../lib/product-features";

export const metadata: Metadata = { title: "Catch up · NavoX" };
export default function RecoveryPage() {
  if (!productFeatures.studentRecovery) notFound();
  return <AccountWorkspace view="recovery" />;
}
