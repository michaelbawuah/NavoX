import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NavoX — Your day. In perspective.",
  description:
    "Your personal assistant for a clearer day. Keep up with tasks, classes, messages and news.",
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
