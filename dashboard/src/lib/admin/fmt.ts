// Formatter helpers used by StreamsGroupTable + BackfillJobsTable.

export function ageMs(iso: string | undefined | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso.endsWith('Z') ? iso : iso + 'Z');
  return Number.isFinite(t) ? Date.now() - t : null;
}

export function fmtAge(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 0) return 'in future';
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

export function fmtCadence(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${seconds / 60}m`;
  return `${seconds / 3600}h`;
}

// "X/Y" for live/sweep seconds — em-dash for unknown halves.
export function fmtDurations(
  live: number | null | undefined,
  sweep: number | null | undefined,
): string {
  const f = (v: number | null | undefined) =>
    v == null ? '—' : v >= 10 ? Math.round(v).toString() : v.toFixed(1);
  return `${f(live)}/${f(sweep)}`;
}

// Local time HH:MM:SS for compact datetime column.
export function fmtTime(iso: string | undefined | null): string {
  if (!iso) return '—';
  const t = Date.parse(iso.endsWith('Z') ? iso : iso + 'Z');
  if (!Number.isFinite(t)) return '—';
  const d = new Date(t);
  return d.toLocaleTimeString('en-GB', { hour12: false });
}
