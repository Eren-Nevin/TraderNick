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
