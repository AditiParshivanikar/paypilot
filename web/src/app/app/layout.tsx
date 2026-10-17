import type { Metadata } from "next";

import { AppShell } from "@/components/app/app-shell";

export const metadata: Metadata = {
  title: "PayPilot: AI payments assistant",
  robots: { index: false },
};

export default function AppLayout({ children }: LayoutProps<"/app">) {
  return <AppShell>{children}</AppShell>;
}
