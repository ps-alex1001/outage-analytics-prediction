import OutageImpactExplorer from "@/components/spatial/outage-impact-explorer";

export default function SpatialPage() {
  return (
    <section className="space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
        Spatial &amp; Event Analysis
      </h1>
      <p className="max-w-2xl text-sm leading-6 text-muted-foreground sm:text-base">
        Select a synthetic outage to inspect its simulated fault source and the
        modeled electrical components losing supply across Bataan.
      </p>
      <OutageImpactExplorer />
    </section>
  );
}
