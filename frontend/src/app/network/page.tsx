import NetworkExplorer from "@/components/network/network-explorer";

export const metadata = { title: "Network Topology | Bataan Outage Analytics" };

export default function NetworkPage() {
  return (
    <section className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Network Topology</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            Explore observed OSM infrastructure and Bataan’s generated network in gray.
            Select an asset, substation, or feeder to inspect its modeled connected paths.
          </p>
        </div>
        <span className="rounded-full border bg-muted px-3 py-1 text-xs font-medium">Observed + modeled</span>
      </div>
      <NetworkExplorer />
    </section>
  );
}
