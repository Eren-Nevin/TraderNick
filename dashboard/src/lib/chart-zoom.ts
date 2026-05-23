import * as d3 from 'd3';

export type View = [number, number] | null;

export function viewToTransform(
  view: View,
  baseStart: number,
  baseEnd: number,
  plotW: number
): d3.ZoomTransform {
  if (view === null || baseEnd <= baseStart || plotW <= 0) return d3.zoomIdentity;
  const baseRange = baseEnd - baseStart;
  const viewRange = view[1] - view[0];
  if (viewRange <= 0) return d3.zoomIdentity;
  const k = baseRange / viewRange;
  const x = (-k * plotW * (view[0] - baseStart)) / baseRange;
  return d3.zoomIdentity.translate(x, 0).scale(k);
}

export function transformToView(
  transform: d3.ZoomTransform,
  baseStart: number,
  baseEnd: number,
  plotW: number
): [number, number] {
  if (plotW <= 0 || baseEnd <= baseStart) return [baseStart, baseEnd];
  const invertX0 = -transform.x / transform.k;
  const invertXP = (plotW - transform.x) / transform.k;
  const span = baseEnd - baseStart;
  return [baseStart + (invertX0 / plotW) * span, baseStart + (invertXP / plotW) * span];
}
