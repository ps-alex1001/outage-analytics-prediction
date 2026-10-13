"use client";

import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";
import { useColorblindMode } from "@/components/theme-provider";

const subscribe = () => () => {};
const getClientSnapshot = () => true;
const getServerSnapshot = () => false;

export function AppearanceControls() {
  const { theme, setTheme } = useTheme();
  const { colorblind, setColorblind } = useColorblindMode();
  // Preferences are unknown on the server. Keep controls stable until hydration.
  const mounted = useSyncExternalStore(
    subscribe,
    getClientSnapshot,
    getServerSnapshot,
  );

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
      <label className="flex items-center gap-2">
        <span>Appearance</span>
        <select
          className="h-8 rounded-md border border-input bg-background px-2 text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          value={mounted ? theme : "system"}
          disabled={!mounted}
          onChange={(event) => setTheme(event.target.value)}
        >
          <option value="light">Light</option>
          <option value="dark">Dark</option>
          <option value="system">System</option>
        </select>
      </label>
      <label className="flex min-h-8 cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          className="size-4 accent-primary focus-visible:outline-2 focus-visible:outline-ring"
          checked={mounted && colorblind}
          disabled={!mounted}
          onChange={(event) => setColorblind(event.target.checked)}
        />
        <span>Colorblind-friendly mode</span>
        <span className="font-medium" aria-hidden="true">
          {mounted && colorblind ? "On" : "Off"}
        </span>
      </label>
    </div>
  );
}
