"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import { useSyncExternalStore } from "react";

const COLORBLIND_STORAGE_KEY = "bataan-colorblind";
const COLORBLIND_EVENT = "bataan-colorblind-change";

function getColorblindSnapshot() {
  return document.documentElement.dataset.colorblind === "true";
}

function getServerSnapshot() {
  return false;
}

function subscribeToColorblind(onChange: () => void) {
  // Keep other tabs in sync as well as controls in the current tab.
  const onStorage = (event: StorageEvent) => {
    if (event.key === COLORBLIND_STORAGE_KEY || event.key === null) {
      document.documentElement.dataset.colorblind = String(
        event.key !== null && event.newValue === "true",
      );
      onChange();
    }
  };
  window.addEventListener(COLORBLIND_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(COLORBLIND_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

function setColorblind(enabled: boolean) {
  document.documentElement.dataset.colorblind = String(enabled);
  try {
    localStorage.setItem(COLORBLIND_STORAGE_KEY, String(enabled));
  } catch {
    // The toggle still works for this visit if browser storage is blocked.
  }
  window.dispatchEvent(new Event(COLORBLIND_EVENT));
}

export function useColorblindMode() {
  const colorblind = useSyncExternalStore(
    subscribeToColorblind,
    getColorblindSnapshot,
    getServerSnapshot,
  );
  return { colorblind, setColorblind };
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      storageKey="bataan-appearance"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      {/* Restore the independent palette before first paint, like next-themes. */}
      <script
        suppressHydrationWarning
        dangerouslySetInnerHTML={{
          __html: `try{document.documentElement.dataset.colorblind=String(localStorage.getItem(${JSON.stringify(COLORBLIND_STORAGE_KEY)})==="true")}catch{}`,
        }}
      />
      {children}
    </NextThemesProvider>
  );
}
