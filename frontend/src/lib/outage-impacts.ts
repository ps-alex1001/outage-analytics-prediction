import { buildTopology, type Assets, type LoadedNetwork, type Network } from "./topology";
import { ImpactCache, awaitImpact } from "./impact-cache";
import type { Asset } from "./topology";

export const IMPACT_API = `${(process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "")}/api/impacts`;
export type OutageEvent = {
  outage_id: string; municipality_psgc: string; municipality: string; barangay_psgc: string; barangay: string;
  start_time: string; end_time: string; duration_minutes: number; cause_category: string;
  resolution_status: "resolved_modeled" | "unresolved"; unresolved_reason: string | null;
};
export type ImpactMetadata = {
  total_events: number; years: number[]; causes: string[];
  geography: Pick<OutageEvent, "municipality_psgc" | "municipality" | "barangay_psgc" | "barangay">[];
  validation: { mapped_percentage: number; mapped_outages: number }; notice: string;
};
export type ImpactDetail = {
  event: OutageEvent;
  assignment: {
    fault_source_topology_id: string | null; fault_source_asset_id: string | null;
    fault_source_asset_type: string | null; fault_source_kind: "node" | "edge" | null;
    feeder_id: string | null; substation_node_id: string | null; substation_name: string | null;
    impact_scenario: string | null; assignment_method: string | null; assignment_uncertainty: string | null;
    source_anchor_node_id?: string; source_promoted_upstream?: boolean;
    source_target_met?: boolean; source_selection_limit_reason?: string;
  };
  impact: {
    affected_node_ids: string[]; affected_edge_ids: string[]; upstream_node_ids: string[]; upstream_edge_ids: string[];
    affected_barangay_psgcs: string[]; affected_line_segment_count: number; affected_line_length_km: number;
    affected_pole_count: number; affected_transformer_count: number; affected_barangay_count: number;
    large_modeled_impact: boolean; no_downstream_transformers: boolean;
  } | null;
  affected_barangays: { barangay_psgc: string; barangay: string; municipality: string; is_origin_barangay: boolean }[];
  boundaries: Assets;
};

export async function fetchImpact<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${IMPACT_API}/${path}`, { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Impact request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

const detailCache = new ImpactCache<ImpactDetail>(24);
const feederCache = new ImpactCache<LoadedNetwork>(3);
const boundaryCache = new Map<string, Promise<Asset>>();
export function loadImpactDetail(id: string, signal?: AbortSignal) {
  return awaitImpact(detailCache.get(id, () => fetchImpact<ImpactDetail>(`events/${encodeURIComponent(id)}/view`)), signal);
}

export async function loadImpactBoundaries(codes: string[], signal?: AbortSignal): Promise<Assets> {
  const unique = [...new Set(codes)].sort();
  const missing = unique.filter(code => !boundaryCache.has(code));
  if (missing.length) {
    const batch = fetchImpact<Assets>(`boundaries?${new URLSearchParams({ codes: missing.join(",") })}`);
    for (const code of missing) {
      const feature = batch.then(data => {
        const result = data.features.find(f => f.properties.barangay_psgc === code);
        if (!result) throw new Error("Missing impact boundary");
        return result;
      }).catch(error => { if (boundaryCache.get(code) === feature) boundaryCache.delete(code); throw error; });
      // A canceled consumer can leave other members of a batch unused.
      void feature.catch(() => {});
      boundaryCache.set(code, feature);
    }
  }
  const features = await awaitImpact(Promise.all(unique.map(code => boundaryCache.get(code)!)), signal);
  return { type: "FeatureCollection", features };
}

let prefetching = false;
export async function prefetchImpact(id: string) {
  if (prefetching) return;
  prefetching = true;
  try {
    const detail = await loadImpactDetail(id);
    if (detail.impact && detail.assignment.feeder_id) await loadImpactFeeder(detail.assignment.feeder_id);
  } catch { /* Selecting the row retries failed requests through the normal UI. */ }
  finally { prefetching = false; }
}

export function loadImpactFeeder(id: string) {
  return feederCache.get(id, () => fetchImpact<Network>(`network?${new URLSearchParams({ feeder_id: id })}`)
    .then(network => ({ ...network, graph: buildTopology(network) })));
}

export function manilaTime(value: string) {
  return new Intl.DateTimeFormat("en-PH", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Manila" }).format(new Date(value));
}
