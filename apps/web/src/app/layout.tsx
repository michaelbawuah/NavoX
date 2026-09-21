import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NavoX — Private Operations",
  description: "A private, human-approved operations workspace.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
