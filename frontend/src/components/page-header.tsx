"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { createPortal } from "react-dom";
import { AppearanceControls } from "@/components/appearance-controls";
import { SidebarTrigger } from "@/components/ui/sidebar";

const OverviewYearContext = createContext<{ year: string; setYear: (year: string) => void } | null>(null);
const HeaderFilterContext = createContext<{ slot: HTMLDivElement | null; setSlot: (slot: HTMLDivElement | null) => void } | null>(null);

export function PageHeaderProvider({ children }: { children: ReactNode }) {
  const [year, setYear] = useState("all");
  const [slot, setSlot] = useState<HTMLDivElement | null>(null);
  return <OverviewYearContext.Provider value={{ year, setYear }}><HeaderFilterContext.Provider value={{ slot, setSlot }}>{children}</HeaderFilterContext.Provider></OverviewYearContext.Provider>;
}

export function SpatialHeaderFilters({ children }: { children: ReactNode }) {
  const context = useContext(HeaderFilterContext);
  const pathname = usePathname();
  return pathname === "/spatial" && context?.slot ? createPortal(children, context.slot) : null;
}

export function useOverviewYear() {
  const value = useContext(OverviewYearContext);
  if (!value) throw new Error("Overview year requires PageHeaderProvider");
  return value;
}

const titles: Record<string, string> = {
  "/": "Overview",
  "/spatial": "Spatial & Event Analysis",
  "/network": "Network Topology",
  "/forecast": "Outage Forecasting",
  "/scenario": "Scenario Simulator",
};

export function PageHeader() {
  const pathname = usePathname();
  const { year, setYear } = useOverviewYear();
  const headerFilters = useContext(HeaderFilterContext);
  return <header className="sticky top-0 z-30 flex min-h-16 shrink-0 flex-wrap items-center gap-3 border-b bg-background px-4 py-3 sm:px-6">
    <SidebarTrigger className="relative top-1 shrink-0" />
    <h1 className="relative top-1 min-w-0 text-lg font-semibold leading-7 sm:text-xl">{titles[pathname] ?? "Bataan Outage Analytics"}</h1>
    {pathname === "/" && <select aria-label="Select year" value={year} onChange={event => setYear(event.target.value)} className="min-h-10 rounded-md border bg-card px-3 text-sm focus-visible:outline-2 focus-visible:outline-ring">
      <option value="all">All Time</option>
      {Array.from({ length: 8 }, (_, index) => 2019 + index).map(value => <option key={value} value={value}>{value}</option>)}
    </select>}
    {pathname === "/spatial" && <div ref={headerFilters?.setSlot} role="group" aria-label="Spatial event filters" className="order-last grid w-full min-w-0 grid-cols-2 gap-2 xl:order-none xl:w-auto xl:flex-1 xl:grid-cols-4" />}
    <div className="ml-auto"><AppearanceControls /></div>
  </header>;
}
