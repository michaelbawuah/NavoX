import type { Metadata, Viewport } from "next";
import { StudentAppRegistration } from "../components/student-notifications";
import { VoxCallControls } from "../components/vox-call-controls";
import { VoxCallProvider } from "../components/vox-call-provider";
import { productFeatures } from "../lib/product-features";
import "@fontsource-variable/inter";
import "@navox/ui/tokens.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "NavoX — Your student day, in perspective.",
  description:
    "Your personal assistant for college. Keep classes, shifts, deadlines, and messages in one place.",
  appleWebApp: {
    capable: true,
    title: "NavoX",
    statusBarStyle: "default",
  },
  icons: { apple: "/icons/apple-touch-icon.png" },
};

export const viewport: Viewport = { themeColor: "#122124" };

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        {productFeatures.studentVox ? (
          <VoxCallProvider>
            <StudentAppRegistration />
            <VoxCallControls />
            {children}
          </VoxCallProvider>
        ) : (
          <>
            <StudentAppRegistration />
            {children}
          </>
        )}
      </body>
    </html>
  );
}
