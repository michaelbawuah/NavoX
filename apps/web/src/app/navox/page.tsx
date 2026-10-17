import type { Metadata } from "next";
import { AccountWorkspace } from "../../components/account-workspace";
import { NavoXAssistant } from "../../components/navox-assistant";
import { productFeatures } from "../../lib/product-features";

export const metadata: Metadata = {
  title: "Ask NavoX",
  description:
    "Ask about your day. Type, talk, and get a little help with your next step.",
};

export default function NavoXAssistantPage() {
  if (productFeatures.studentAgent)
    return <AccountWorkspace view="student-assistant" />;
  return <NavoXAssistant />;
}
