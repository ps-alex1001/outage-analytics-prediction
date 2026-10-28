"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";

export function AppContent({ children }: { children: ReactNode }) {
  const fullMap = usePathname() === "/network";
  return <div className="app-content flex-1 p-6 sm:p-8" data-full-map={fullMap ? "true" : "false"}>{children}</div>;
}
