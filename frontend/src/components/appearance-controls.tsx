"use client";

import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";

const subscribe = () => () => {};
const getClientSnapshot = () => true;
const getServerSnapshot = () => false;

export function AppearanceControls() {
  const { resolvedTheme, setTheme } = useTheme();
  // Preferences are unknown on the server. Keep controls stable until hydration.
  const mounted = useSyncExternalStore(
    subscribe,
    getClientSnapshot,
    getServerSnapshot,
  );

  const dark = mounted && resolvedTheme === "dark";
  return <button
    type="button"
    role="switch"
    aria-label="Dark mode"
    aria-checked={dark}
    title={dark ? "Switch to light mode" : "Switch to dark mode"}
    disabled={!mounted}
    onClick={() => setTheme(dark ? "light" : "dark")}
    className="relative flex h-11 w-[88px] shrink-0 items-center justify-between rounded-full border border-input bg-muted p-1 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-50"
  >
    <span aria-hidden="true" className={`absolute left-1 top-1 size-9 rounded-full bg-foreground shadow-sm transition-transform duration-200 motion-reduce:transition-none ${dark ? "translate-x-11" : "translate-x-0"}`} />
    <span className={`relative z-10 flex size-9 items-center justify-center ${dark ? "text-muted-foreground" : "text-primary-foreground"}`}><Sun size={18} aria-hidden="true" /></span>
    <span className={`relative z-10 flex size-9 items-center justify-center ${dark ? "text-primary-foreground" : "text-muted-foreground"}`}><Moon size={18} aria-hidden="true" /></span>
  </button>;
}
