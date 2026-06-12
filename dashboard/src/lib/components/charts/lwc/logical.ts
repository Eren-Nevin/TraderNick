/** Shared helpers for converting between time (the chart's external sync
 *  currency) and logical-bar indices (Lightweight's native coordinate that
 *  *does* extend past data, into whitespace).
 *
 *  Used by the four Lwc charts to keep cross-chart pan sync going past the
 *  right edge of data: subscribeVisibleTimeRangeChange clamps the emitted
 *  `to` to the last data point, so we synthesize the past-data portion
 *  ourselves from getVisibleLogicalRange() before pushing upstream, and
 *  apply via setVisibleLogicalRange when the inbound target falls past
 *  data. */

/** Per-bar time step. Inferred from the gap between the last two real
 *  points. Returns null if the array has fewer than two points or the
 *  inferred step isn't positive. */
export function timeStep(data: ReadonlyArray<{ time: number }>): number | null {
  if (data.length < 2) return null;
  const s = data[data.length - 1].time - data[data.length - 2].time;
  return s > 0 ? s : null;
}

/** Map a Unix-second time to a logical bar index. Extrapolates linearly
 *  outside data using the inferred time step — negative for times before
 *  the first bar, > lastIdx for times past the last bar. */
export function timeToLogical(t: number, data: ReadonlyArray<{ time: number }>): number {
  if (data.length === 0) return 0;
  if (data.length === 1) return 0;
  const step = timeStep(data);
  if (step === null) return 0;
  const first = data[0].time;
  const last = data[data.length - 1].time;
  if (t <= first) return -(first - t) / step;
  if (t >= last) return data.length - 1 + (t - last) / step;
  let lo = 0;
  let hi = data.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (data[mid].time < t) lo = mid + 1;
    else hi = mid;
  }
  if (data[lo].time === t) return lo;
  const a = data[lo - 1].time;
  const b = data[lo].time;
  return lo - 1 + (t - a) / (b - a);
}

/** Map a fractional logical bar index back to a time. Inverse of
 *  timeToLogical; consistent extrapolation outside data. */
export function logicalToTime(lg: number, data: ReadonlyArray<{ time: number }>): number {
  if (data.length === 0) return 0;
  if (data.length === 1) return data[0].time;
  const step = timeStep(data);
  if (step === null) return data[0].time;
  if (lg <= 0) return data[0].time + lg * step;
  const lastIdx = data.length - 1;
  if (lg >= lastIdx) return data[lastIdx].time + (lg - lastIdx) * step;
  const floor = Math.floor(lg);
  const frac = lg - floor;
  return data[floor].time + frac * (data[floor + 1].time - data[floor].time);
}
