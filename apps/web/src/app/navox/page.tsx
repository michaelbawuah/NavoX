import type { Metadata } from "next";
import { NavoXAssistant } from "../../components/navox-assistant";

export const metadata: Metadata = {
  title: "Ask NavoX",
  description:
    "Ask about your day. Type, talk, and get a little help with your next step.",
};

export default function NavoXAssistantPage() {
  return <NavoXAssistant />;
}
