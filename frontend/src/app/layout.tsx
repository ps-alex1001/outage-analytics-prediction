import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AppSidebar } from "@/components/app-sidebar";
import { AppearanceControls } from "@/components/appearance-controls";
import { ThemeProvider } from "@/components/theme-provider";
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Bataan Outage Analytics and Forecasting",
  description: "GIS-based power outage analytics and forecasting for Bataan.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <ThemeProvider>
          <TooltipProvider>
            <SidebarProvider>
              <AppSidebar />
              <SidebarInset className="min-w-0">
                <header className="flex min-h-16 flex-wrap items-center gap-3 border-b px-4 py-3 sm:px-6">
                  <SidebarTrigger className="shrink-0" />
                  <span className="min-w-0 flex-1 text-sm font-medium leading-5 sm:text-base">
                    Bataan Outage Analytics and Forecasting
                  </span>
                  <div className="w-full lg:ml-auto lg:w-auto">
                    <AppearanceControls />
                  </div>
                </header>
                <div className="flex-1 p-6 sm:p-8">{children}</div>
              </SidebarInset>
            </SidebarProvider>
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
