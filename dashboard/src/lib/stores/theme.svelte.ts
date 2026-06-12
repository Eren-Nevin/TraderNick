// Module-level reactive theme state. Chart components import `themeStore` and
// read `themeStore.theme` to subscribe to changes; a $effect on this triggers
// chart re-render with the new CSS-variable values.

export type Theme = 'dark' | 'light';

const STORAGE_KEY = 'tradernick:theme';

let _theme = $state<Theme>('dark');

// Plain-Map cache of resolved CSS-variable values. `getComputedStyle()`
// is a forced style flush — the profile shows it eats ~1.4% of total
// hover time even with d3 batching, because drawCrosshair calls cssVar
// once per chart on every mouse-move. The cache is keyed by name only;
// `themeStore.set()` / `hydrate()` clear it whenever the resolved value
// could have changed. This is deliberately NOT reactive — reading the
// `$state` rune from a module helper is what broke hydration last time.
const _cssVarCache = new Map<string, string>();

export const themeStore = {
  get theme(): Theme {
    return _theme;
  },
  set(t: Theme) {
    if (t === _theme) return;
    _theme = t;
    _cssVarCache.clear();
    if (typeof document !== 'undefined') {
      document.documentElement.classList.toggle('light', t === 'light');
      try {
        localStorage.setItem(STORAGE_KEY, t);
      } catch {
        // ignore
      }
    }
  },
  toggle() {
    this.set(_theme === 'dark' ? 'light' : 'dark');
  },
  hydrate() {
    if (typeof document === 'undefined') return;
    try {
      const saved = localStorage.getItem(STORAGE_KEY) as Theme | null;
      if ((saved === 'light' || saved === 'dark') && saved !== _theme) {
        _theme = saved;
        _cssVarCache.clear();
        document.documentElement.classList.toggle('light', saved === 'light');
      }
    } catch {
      // ignore
    }
  }
};

/** Read a chart-themed CSS variable from the document root, with a hex
 *  fallback. Result is memoized per name; the cache is cleared on theme
 *  change (see `themeStore.set` / `hydrate`). */
export function cssVar(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  const hit = _cssVarCache.get(name);
  if (hit !== undefined) return hit;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const resolved = v || fallback;
  _cssVarCache.set(name, resolved);
  return resolved;
}
