import type { Metadata } from "next";
import { AccountWorkspace } from "../../components/account-workspace";

export const metadata: Metadata = { title: "Inbox · NavoX" };

export default function InboxPage() {
  return <AccountWorkspace view="inbox" />;
}
