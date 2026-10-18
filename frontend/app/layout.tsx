import type { Metadata } from "next";
import type { ReactNode } from "react";
import AppShell from "@/components/layout/AppShell";
import { FluffySessionProvider } from "@/providers/FluffySessionProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fluffy",
  description: "Fluffy proposes a quiet change in the home.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-background text-foreground">
        <FluffySessionProvider>
          <AppShell>{children}</AppShell>
        </FluffySessionProvider>
      </body>
    </html>
  );
}
