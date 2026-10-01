import type { Metadata } from "next";
import { NavoXAssistant } from "../../components/navox-assistant";

export const metadata: Metadata = {
  title: "Assistant · NavoX",
  description:
    "Ask about your saved Today state by typing or with the microphone, and hear the answer.",
};

export default function NavoXAssistantPage() {
  return <NavoXAssistant />;
}
