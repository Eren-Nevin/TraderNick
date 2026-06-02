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
