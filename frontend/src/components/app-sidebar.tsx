"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FlaskConical,
  LayoutDashboard,
  MapPinned,
  Network,
  TrendingUp,
  Zap,
} from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";

const navigation = [
  { title: "Overview", href: "/", icon: LayoutDashboard },
  { title: "Spatial & Event Analysis", href: "/spatial", icon: MapPinned },
  { title: "Network Topology", href: "/network", icon: Network },
  { title: "Outage Forecasting", href: "/forecast", icon: TrendingUp },
  { title: "Scenario Simulator", href: "/scenario", icon: FlaskConical },
];

export function AppSidebar() {
  const pathname = usePathname();
  const { setOpenMobile } = useSidebar();

  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader className="border-b">
        <div className="flex h-12 items-center gap-2 px-2">
          <Zap className="size-4 shrink-0" aria-hidden="true" />
          <div className="min-w-0 group-data-[collapsible=icon]:hidden">
            <p className="text-sm font-semibold">Bataan</p>
            <p className="text-xs text-muted-foreground">Outage dashboard</p>
          </div>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Workspace</SidebarGroupLabel>
          <SidebarGroupContent>
            <nav aria-label="Main navigation">
              <SidebarMenu>
                {navigation.map((item) => {
                  const isActive = pathname === item.href;

                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        isActive={isActive}
                        tooltip={item.title}
                        className="data-active:underline data-active:underline-offset-4"
                        render={
                          <Link
                            href={item.href}
                            aria-label={item.title}
                            aria-current={isActive ? "page" : undefined}
                            onClick={() => setOpenMobile(false)}
                          />
                        }
                      >
                        <item.icon aria-hidden="true" />
                        <span>{item.title}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </nav>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
    </Sidebar>
  );
}
