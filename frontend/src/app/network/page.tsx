import NetworkExplorer from "@/components/network/network-explorer";

export const metadata = { title: "Network Topology | Bataan Outage Analytics" };

export default function NetworkPage() {
  return (
    <section className="network-page flex min-h-0 flex-1 flex-col">
      <NetworkExplorer />
    </section>
  );
}
