// User-created Dashboard pages. Each page hosts its own DynamicChartLayout
// with an independent storage key. Page list itself persists to
// `tradernick:pages:v1`. The first/default page has id 'default' so its
// layout key stays `tradernick:dashboard:layout:v1` (preserving anything
// the user built before the multi-page split).

export type Page = { id: string; name: string };

const STORAGE_KEY = 'tradernick:pages:v1';
const DEFAULT_PAGE: Page = { id: 'default', name: 'Dashboard' };

let _pages = $state<Page[]>([DEFAULT_PAGE]);
let _hydrated = false;

function persist() {
  if (typeof localStorage === 'undefined') return;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(_pages)); } catch { /* ignore */ }
}

function newId(): string {
  return `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

/** Storage key for a given page's chart layout. Default page reuses the
 *  legacy key so users keep what they had before pages existed. */
export function pageLayoutKey(pageId: string): string {
  return pageId === 'default'
    ? 'tradernick:dashboard:layout:v1'
    : `tradernick:page:${pageId}:layout:v1`;
}

export const pagesStore = {
  get pages(): Page[] { return _pages; },

  hydrate() {
    if (_hydrated) return;
    _hydrated = true;
    if (typeof localStorage === 'undefined') return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed) || parsed.length === 0) return;
      const cleaned = parsed.filter(
        (p): p is Page =>
          p && typeof p === 'object' && typeof p.id === 'string' && typeof p.name === 'string'
      );
      if (cleaned.length > 0) _pages = cleaned;
    } catch { /* ignore */ }
  },

  /** Add a new page; returns the created Page so the caller can navigate. */
  add(name?: string): Page {
    const trimmed = (name ?? '').trim();
    const finalName = trimmed || `Page ${_pages.length + 1}`;
    const page: Page = { id: newId(), name: finalName };
    _pages = [..._pages, page];
    persist();
    return page;
  },

  /** Duplicate a page — its name and ALL of its charts — and insert the copy
   *  right after the source. Each chart gets a fresh id so the two pages don't
   *  share per-instance state (the load cache is keyed by chart id). Returns
   *  the new Page, or null if the source doesn't exist. */
  duplicate(id: string, name?: string): Page | null {
    const src = _pages.find((p) => p.id === id);
    if (!src) return null;
    const trimmed = (name ?? '').trim();
    const page: Page = { id: newId(), name: trimmed || `${src.name} (copy)` };
    if (typeof localStorage !== 'undefined') {
      try {
        const raw = localStorage.getItem(pageLayoutKey(id));
        if (raw != null) {
          let out = raw;
          try {
            const parsed = JSON.parse(raw);
            if (parsed && Array.isArray(parsed.charts)) {
              parsed.charts = parsed.charts.map((c: Record<string, unknown>) => ({
                ...c,
                id:
                  typeof crypto !== 'undefined' && crypto.randomUUID
                    ? crypto.randomUUID()
                    : `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
              }));
              out = JSON.stringify(parsed);
            }
          } catch { /* not parseable — copy verbatim */ }
          localStorage.setItem(pageLayoutKey(page.id), out);
        }
      } catch { /* ignore */ }
    }
    const idx = _pages.findIndex((p) => p.id === id);
    _pages =
      idx >= 0
        ? [..._pages.slice(0, idx + 1), page, ..._pages.slice(idx + 1)]
        : [..._pages, page];
    persist();
    return page;
  },

  /** Append a chart object to another page's stored layout, creating the
   *  layout if the target was never customised (user pages default to an empty
   *  canvas). Used by the chart "Move to Page" context menu — the source page
   *  removes the chart from its live `instances` (which auto-persists), and
   *  this writes it into the target page's localStorage so it appears there on
   *  next mount. The layout shape mirrors DynamicChartLayout's persist effect
   *  ({version,charts}) and duplicate() above. Returns false on any failure. */
  appendChartToPage(pageId: string, chart: Record<string, unknown>): boolean {
    if (typeof localStorage === 'undefined') return false;
    try {
      const key = pageLayoutKey(pageId);
      const raw = localStorage.getItem(key);
      let charts: Record<string, unknown>[] = [];
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && Array.isArray(parsed.charts)) charts = parsed.charts;
      }
      charts.push(chart);
      localStorage.setItem(key, JSON.stringify({ version: 1, charts }));
      return true;
    } catch {
      return false;
    }
  },

  rename(id: string, name: string) {
    const trimmed = name.trim();
    if (!trimmed) return;
    _pages = _pages.map((p) => (p.id === id ? { ...p, name: trimmed } : p));
    persist();
  },

  /** Delete a page. The last remaining page is protected to keep the
   *  Sidebar from becoming a dead-end. Returns true if removal happened. */
  remove(id: string): boolean {
    if (_pages.length <= 1) return false;
    _pages = _pages.filter((p) => p.id !== id);
    persist();
    if (typeof localStorage !== 'undefined') {
      try { localStorage.removeItem(pageLayoutKey(id)); } catch { /* ignore */ }
    }
    return true;
  }
};
