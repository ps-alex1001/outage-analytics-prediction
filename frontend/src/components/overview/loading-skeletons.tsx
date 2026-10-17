import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";

function SkeletonState({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div role="status" aria-label={label} className={className}>
      <span className="sr-only">{label}</span>
      <div aria-hidden="true" className="h-full">{children}</div>
    </div>
  );
}

export function DonutChartSkeleton() {
  return (
    <div aria-hidden="true" className="relative h-full">
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="relative aspect-square w-3/5 max-w-68">
          <Skeleton className="size-full rounded-full" />
          <div className="absolute inset-[22%] flex flex-col items-center justify-center gap-2 rounded-full bg-card">
            <Skeleton className="h-7 w-16" />
            <Skeleton className="h-3 w-20" />
          </div>
        </div>
      </div>
      <svg viewBox="0 0 600 360" preserveAspectRatio="none" className="absolute inset-0 size-full animate-pulse text-muted motion-reduce:animate-none">
        <path d="M200 115 L160 90 H110 M190 235 L155 270 H110 M400 120 L440 90 H490 M410 235 L445 270 H490" fill="none" stroke="currentColor" strokeWidth="2" />
      </svg>
      {["left-0 top-[20%]", "left-0 top-[70%]", "right-0 top-[20%]", "right-0 top-[70%]"].map((position) => (
        <div key={position} className={`absolute w-[18%] space-y-2 ${position}`}>
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      ))}
    </div>
  );
}

export function DonutSkeleton() {
  return (
    <SkeletonState label="Loading cause distribution" className="h-full">
      <DonutChartSkeleton />
    </SkeletonState>
  );
}

export function TrendSkeleton() {
  return (
    <SkeletonState label="Loading outage trends" className="h-full px-5 pt-8 pb-6">
      <div className="flex h-full gap-3">
        <div className="flex flex-col justify-between pb-5">
          {Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-3 w-6" />)}
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="relative flex-1 border-b border-l">
            <div className="absolute inset-0 flex flex-col justify-between">
              {Array.from({ length: 6 }, (_, index) => <div key={index} className="border-t border-border/50" />)}
            </div>
            <svg aria-hidden="true" viewBox="0 0 600 200" preserveAspectRatio="none" className="absolute inset-0 size-full animate-pulse text-muted motion-reduce:animate-none">
              <path d="M0 135 L35 155 L65 95 L95 115 L130 75 L165 100 L200 60 L235 110 L270 85 L305 125 L340 65 L375 90 L410 45 L445 100 L480 80 L515 130 L550 95 L600 115" fill="none" stroke="currentColor" strokeWidth="6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div className="mt-3 flex justify-between gap-2">
            {Array.from({ length: 8 }, (_, index) => <Skeleton key={index} className="h-3 w-6" />)}
          </div>
        </div>
      </div>
    </SkeletonState>
  );
}

export function RankingsSkeleton({ rows }: { rows: number }) {
  return (
    <SkeletonState label="Loading location rankings" className="space-y-0.5">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="px-2 py-1">
          <div className="flex h-5 items-center justify-between gap-4">
            <Skeleton className="h-3.5" style={{ width: `${45 + (index % 3) * 12}%` }} />
            <Skeleton className="h-3 w-8" />
          </div>
          <Skeleton className="mt-1 h-1" style={{ width: `${95 - index * 5}%` }} />
        </div>
      ))}
    </SkeletonState>
  );
}

export function EventsSkeleton() {
  return (
    <SkeletonState label="Loading longest outages" className="mt-4 min-h-0 flex-1 overflow-hidden">
      <div className="flex justify-between border-b pb-2 text-xs text-muted-foreground">
        <span>Location / Date</span><span>Duration</span>
      </div>
      {Array.from({ length: 8 }, (_, index) => (
        <div key={index} className="flex justify-between gap-4 border-b py-2 last:border-0">
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-3" style={{ width: `${65 + (index % 3) * 12}%` }} />
            <Skeleton className="h-3 w-20" />
          </div>
          <Skeleton className="h-3 w-10" />
        </div>
      ))}
    </SkeletonState>
  );
}

export function LocationSkeleton() {
  return (
    <SkeletonState label="Loading selected location statistics" className="h-full">
      <div className="flex h-full flex-col gap-3">
        <div className="space-y-2"><Skeleton className="h-5 w-3/4" /><Skeleton className="h-3 w-1/2" /></div>
        <div className="grid h-[125px] grid-cols-[115px_minmax(0,1fr)] items-center gap-2">
          <div className="relative mx-auto size-24">
            <Skeleton className="size-full rounded-full" />
            <div className="absolute inset-5 rounded-full bg-card" />
          </div>
          <div className="space-y-2"><Skeleton className="h-3 w-16" /><Skeleton className="h-7 w-20" /><Skeleton className="h-3 w-24 max-w-full" /></div>
        </div>
        <div className="space-y-2 border-t pt-3"><Skeleton className="h-3 w-2/3" /><Skeleton className="h-6 w-24" /></div>
        <div className="space-y-2 border-t pt-3"><Skeleton className="h-3 w-1/2" /><Skeleton className="h-4 w-3/4" /><Skeleton className="h-1.5 w-full" /></div>
        <Skeleton className="mt-auto h-10 w-full" />
      </div>
    </SkeletonState>
  );
}

export function TimingSkeleton() {
  return (
    <SkeletonState label="Loading outage timing patterns" className="h-[340px] min-w-180 pt-[22px] pb-[34px]">
      <div className="grid h-full grid-cols-[30%_9%_58%] grid-rows-7 gap-y-1 pr-[3%]">
        {[52, 68, 84, 96, 78, 60, 42].map((width) => (
          <div key={width} className="col-span-3 grid grid-cols-subgrid items-center">
            <Skeleton className="ml-auto h-5" style={{ width: `${width}%` }} />
            <Skeleton className="mx-auto h-3 w-7" />
            <div className="grid h-full grid-cols-24 gap-0.5">
              {Array.from({ length: 24 }, (_, hour) => <Skeleton key={hour} className="h-full rounded-xs" />)}
            </div>
          </div>
        ))}
      </div>
    </SkeletonState>
  );
}

export function MapSkeleton() {
  return (
    <SkeletonState label="Loading geographic outage map" className="absolute inset-0 z-10 rounded-lg bg-card">
      <div className="relative flex h-full items-center justify-center overflow-hidden rounded-lg border border-border/50 bg-muted/20">
        <div className="absolute inset-0 grid grid-cols-6 grid-rows-4 opacity-40">
          {Array.from({ length: 24 }, (_, index) => <div key={index} className="border border-card" />)}
        </div>
        <Skeleton className="h-4/5 w-2/5 rounded-[40%_55%_45%_35%]" />
        <div className="absolute top-3 right-3 space-y-1 rounded-md border bg-card p-1">
          <Skeleton className="size-6" /><Skeleton className="size-6" /><Skeleton className="size-6" />
        </div>
      </div>
    </SkeletonState>
  );
}
