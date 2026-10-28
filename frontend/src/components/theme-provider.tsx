"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return <NextThemesProvider attribute="class" storageKey="bataan-appearance" defaultTheme="system" enableSystem disableTransitionOnChange>
    {children}
  </NextThemesProvider>;
}
