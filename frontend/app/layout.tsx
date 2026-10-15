import type { Metadata } from "next";
import Link from "next/link";
import HealthPill from "@/components/HealthPill";
// Fonts are self-hosted from npm (Fontsource): works offline, and avoids a Windows dev-mode
// bug in next/font/google with Turbopack in Next 16.4.
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "vid-gen",
  description: "Local automated video generator: script, stock footage, voiceover, captions, music.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="flex min-h-full flex-col">
        <header className="border-b border-border">
          <div className="mx-auto flex h-14 w-full max-w-5xl items-center justify-between px-4 sm:px-6">
            <Link href="/" className="text-[15px] font-semibold tracking-tight">
              vid-gen
            </Link>
            <HealthPill />
          </div>
        </header>
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 pb-20 sm:px-6">{children}</main>
      </body>
    </html>
  );
}
