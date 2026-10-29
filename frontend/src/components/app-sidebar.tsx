"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  FlaskConical,
  LayoutDashboard,
  MapPinned,
  Network,
  TrendingUp,
} from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
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
    <Sidebar collapsible="icon" variant="sidebar">
      <SidebarHeader className="border-b border-sidebar-border">
        <div className="flex h-12 items-center gap-2 px-2 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0">
          <span className="relative size-8 shrink-0 overflow-hidden" role="img" aria-label="PENELCO logo">
            <Image
              src="/penelco-logo.png"
              alt=""
              width={194}
              height={122}
              unoptimized
              className="absolute max-w-none"
              style={{ width: 116, height: 73, left: -24, top: -14 }}
            />
          </span>
          <div className="min-w-0 group-data-[collapsible=icon]:hidden">
            <p className="text-sm font-semibold leading-5">Bataan Outage Analytics</p>
          </div>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
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
                        className="data-active:underline data-active:underline-offset-4 group-data-[collapsible=icon]:mx-auto"
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
