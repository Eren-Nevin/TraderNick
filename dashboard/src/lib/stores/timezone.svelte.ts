// Module-level reactive timezone-DISPLAY state: 'utc' (default) or 'local'
// (browser local time). This is purely a rendering concern — every timestamp
// in the app stays UTC in state, in queries, and over the wire; only how we
// FORMAT a unix-second for the user changes. Chart/table formatters read
// `timezoneStore.mode` so a toggle re-renders them (see fmtUtcTime + the LWC
// lwcChartOptions time formatters).

export type TzMode = 'utc' | 'local';

const STORAGE_KEY = 'tradernick:tz';

let _mode = $state<TzMode>('utc');

export const timezoneStore = {
  get mode(): TzMode {
    return _mode;
  },
  /** True when showing browser-local time. Convenience for formatters. */
  get isLocal(): boolean {
    return _mode === 'local';
  },
  set(m: TzMode) {
    if (m === _mode) return;
    _mode = m;
    try {
      localStorage.setItem(STORAGE_KEY, m);
    } catch {
      // ignore (private mode / SSR)
    }
  },
  toggle() {
    this.set(_mode === 'utc' ? 'local' : 'utc');
  },
  hydrate() {
    try {
      const s = localStorage.getItem(STORAGE_KEY);
      if ((s === 'utc' || s === 'local') && s !== _mode) _mode = s;
    } catch {
      // ignore
    }
  }
};

const _p2 = (n: number) => String(n).padStart(2, '0');

/** Display helper — "YYYY-MM-DD HH:MM" (24h, zero-padded) in the active zone.
 *  Reads timezoneStore.isLocal so Svelte components re-render on toggle.
 *  For DISPLAY only — never use for query params / cache keys (keep those UTC). */
export function fmtTzDateTime(unixSec: number): string {
  const d = new Date(unixSec * 1000);
  if (_mode === 'local') {
    return `${d.getFullYear()}-${_p2(d.getMonth() + 1)}-${_p2(d.getDate())} `
      + `${_p2(d.getHours())}:${_p2(d.getMinutes())}`;
  }
  return `${d.getUTCFullYear()}-${_p2(d.getUTCMonth() + 1)}-${_p2(d.getUTCDate())} `
    + `${_p2(d.getUTCHours())}:${_p2(d.getUTCMinutes())}`;
}

/** Display helper — "YYYY-MM-DD" in the active zone.
 *  Reads timezoneStore.isLocal so Svelte components re-render on toggle.
 *  For DISPLAY only — never use for query params / cache keys (keep those UTC). */
export function fmtTzDate(unixSec: number): string {
  const d = new Date(unixSec * 1000);
  if (_mode === 'local') {
    return `${d.getFullYear()}-${_p2(d.getMonth() + 1)}-${_p2(d.getDate())}`;
  }
  return `${d.getUTCFullYear()}-${_p2(d.getUTCMonth() + 1)}-${_p2(d.getUTCDate())}`;
}

/** Short label for the active zone, for UI chips/toggles.
 *  UTC → "UTC"; local → the browser's zone abbreviation (e.g. "PST", "GMT+2"). */
export function tzShortLabel(): string {
  if (_mode === 'utc') return 'UTC';
  try {
    const parts = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short' }).formatToParts(new Date());
    return parts.find((p) => p.type === 'timeZoneName')?.value ?? 'Local';
  } catch {
    return 'Local';
  }
}
