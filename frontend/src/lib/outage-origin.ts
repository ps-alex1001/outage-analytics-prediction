import type { Asset } from "./topology";

/** A line source starts at its upstream topology node, regardless of coordinate order. */
export function outageOriginPoint(asset: Asset, upstreamNode?: Asset): [number, number] | undefined {
  const anchor = asset.geometry.type === "Point" ? asset : upstreamNode;
  if (anchor?.geometry.type !== "Point") return;
  const [x, y] = anchor.geometry.coordinates;
  return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : undefined;
}
