import type { Metadata } from "next";
import { NavoXAssistant } from "../../../components/navox-assistant";

export const metadata: Metadata = {
  title: "Conversations and reviewed actions · NavoX",
};

export default function AssistantActionsPage() {
  return <NavoXAssistant />;
}
