// Module-level reactive theme state. Chart components import `themeStore` and
// read `themeStore.theme` to subscribe to changes; a $effect on this triggers
// chart re-render with the new CSS-variable values.

export type Theme = 'dark' | 'light';

const STORAGE_KEY = 'tradernick:theme';

let _theme = $state<Theme>('dark');

export const themeStore = {
  get theme(): Theme {
    return _theme;
  },
  set(t: Theme) {
    _theme = t;
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
      if (saved === 'light' || saved === 'dark') {
        _theme = saved;
        document.documentElement.classList.toggle('light', saved === 'light');
      }
    } catch {
      // ignore
    }
  }
};

/** Read a chart-themed CSS variable from the document root, with a hex fallback. */
export function cssVar(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
