// Saved wallet filters (see lib/components/charts/filters.ts for the model).
// Persisted to `tradernick:filters:v1`. Mirrors the pages store shape so the
// hydrate/persist lifecycle is familiar. Charts reference filters by id; the
// builder UI on /filters is the CRUD surface.

import {
  type SavedFilter,
  type FilterConfig,
  hlConfig,
  sanitizeSavedFilter,
} from '../components/charts/filters';
import {
  type SmartSelectorState,
  smartSelectorCacheKey,
} from '../components/charts/smartSelector';

const STORAGE_KEY = 'tradernick:filters:v1';

let _filters = $state<SavedFilter[]>([]);
let _hydrated = false;

function persist() {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, filters: _filters }));
  } catch {
    /* localStorage may be full or disabled */
  }
}

function newId(): string {
  return `f-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

/** Would adding `refs` to filter `id` create a cycle (some ref reaches `id`)? */
function wouldCycle(id: string, refs: string[], all: SavedFilter[]): boolean {
  const byId = new Map(all.map((f) => [f.id, f]));
  const seen = new Set<string>();
  const stack = [...refs];
  while (stack.length) {
    const cur = stack.pop()!;
    if (cur === id) return true;
    if (seen.has(cur)) continue;
    seen.add(cur);
    const f = byId.get(cur);
    if (f) stack.push(...f.refs);
  }
  return false;
}

export const filtersStore = {
  get filters(): SavedFilter[] {
    return _filters;
  },

  hydrate() {
    if (_hydrated) return;
    _hydrated = true;
    if (typeof localStorage === 'undefined') return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      const arr = Array.isArray(parsed?.filters) ? parsed.filters : [];
      const cleaned = arr
        .map(sanitizeSavedFilter)
        .filter((f: SavedFilter | null): f is SavedFilter => f !== null);
      if (cleaned.length > 0) _filters = cleaned;
    } catch {
      /* ignore */
    }
  },

  getById(id: string): SavedFilter | undefined {
    return _filters.find((f) => f.id === id);
  },

  /** Create a filter. Throws on a self/cyclic ref set. */
  add(name: string, config: FilterConfig, refs: string[] = []): SavedFilter {
    const id = newId();
    if (wouldCycle(id, refs, _filters)) {
      throw new Error('filter refs would create a cycle');
    }
    const trimmed = name.trim() || `Filter ${_filters.length + 1}`;
    const f: SavedFilter = { id, name: trimmed, config, refs };
    _filters = [..._filters, f];
    persist();
    return f;
  },

  /** Patch an existing filter (name / config / refs). Rejects ref changes
   *  that would introduce a cycle; returns false if rejected or not found. */
  update(
    id: string,
    patch: { name?: string; config?: FilterConfig; refs?: string[] },
  ): boolean {
    const cur = _filters.find((f) => f.id === id);
    if (!cur) return false;
    const nextRefs = patch.refs ?? cur.refs;
    if (nextRefs.includes(id) || wouldCycle(id, nextRefs, _filters)) return false;
    _filters = _filters.map((f) =>
      f.id === id
        ? {
            ...f,
            name: patch.name !== undefined ? patch.name.trim() || f.name : f.name,
            config: patch.config ?? f.config,
            refs: nextRefs,
          }
        : f,
    );
    persist();
    return true;
  },

  rename(id: string, name: string) {
    const trimmed = name.trim();
    if (!trimmed) return;
    _filters = _filters.map((f) => (f.id === id ? { ...f, name: trimmed } : f));
    persist();
  },

  remove(id: string): boolean {
    const before = _filters.length;
    _filters = _filters.filter((f) => f.id !== id);
    if (_filters.length === before) return false;
    persist();
    return true;
  },

  /** Migration helper: find an existing leaf hl filter equal to `selector`,
   *  or create one. Dedupes by the selector cache key so the same inline
   *  selector shared across charts maps to a single saved filter. */
  findOrCreateFromSelector(selector: SmartSelectorState): string {
    const key = smartSelectorCacheKey(selector);
    const existing = _filters.find(
      (f) =>
        f.refs.length === 0 &&
        f.config.kind === 'hl' &&
        smartSelectorCacheKey(f.config.selector) === key,
    );
    if (existing) return existing.id;
    return this.add(`Migrated filter ${_filters.length + 1}`, hlConfig(selector), []).id;
  },
};
