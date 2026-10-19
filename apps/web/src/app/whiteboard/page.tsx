import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AccountWorkspace } from "../../components/account-workspace";
import { productFeatures } from "../../lib/product-features";

export const metadata: Metadata = { title: "Whiteboard · NavoX" };

export default function WhiteboardPage() {
  if (!productFeatures.studentWhiteboard) notFound();
  return <AccountWorkspace view="whiteboard" />;
}
