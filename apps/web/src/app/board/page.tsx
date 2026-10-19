import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AccountWorkspace } from "../../components/account-workspace";
import { productFeatures } from "../../lib/product-features";

export const metadata: Metadata = { title: "NavoX Board" };

export default function BoardPage() {
  if (!productFeatures.studentWhiteboard) notFound();
  return <AccountWorkspace view="board" />;
}
