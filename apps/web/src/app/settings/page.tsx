import type { Metadata } from "next";
import { AccountWorkspace } from "../../components/account-workspace";

export const metadata: Metadata = { title: "Settings · NavoX" };

export default function SettingsPage() {
  return <AccountWorkspace view="settings" />;
}
