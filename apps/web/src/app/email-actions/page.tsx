import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AccountWorkspace } from "../../components/account-workspace";
import { productFeatures } from "../../lib/product-features";

export const metadata: Metadata = { title: "Email actions · NavoX" };
export default function EmailActionsPage() {
  if (!productFeatures.studentEmail) notFound();
  return <AccountWorkspace view="student-email" />;
}
