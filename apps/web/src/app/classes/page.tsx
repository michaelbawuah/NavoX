import type { Metadata } from "next";
import { AccountWorkspace } from "../../components/account-workspace";

export const metadata: Metadata = { title: "Classes · NavoX" };

export default function ClassesPage() {
  return <AccountWorkspace view="classes" />;
}
