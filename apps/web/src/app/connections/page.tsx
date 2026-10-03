import type { Metadata } from "next";
import { AccountWorkspace } from "../../components/account-workspace";

export const metadata: Metadata = { title: "Connections · NavoX" };

export default function ConnectionsPage() {
  return <AccountWorkspace view="connections" />;
}
