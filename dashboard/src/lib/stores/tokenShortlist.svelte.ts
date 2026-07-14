// Token Shortlist: two small ordered watchlists — Short and Long — the user
// maintains in the sidebar. Browser-local (localStorage), same idiom as the Pages
// store. A token is EXCLUSIVE to one side; each side caps at MAX_PER_SIDE.

export type ShortlistSide = 'short' | 'long';

const STORAGE_KEY = 'tradernick:token_shortlist:v1';
const MAX_PER_SIDE = 10;

let _short = $state<string[]>([]);
let _long = $state<string[]>([]);
let _hydrated = false;

function persist() {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ short: _short, long: _long }));
  } catch {
    /* ignore quota/disabled */
  }
}

/** Uppercase, trim, dedup, cap — used on load and on drag-reorder. */
function cleanList(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of raw) {
    if (typeof t !== 'string') continue;
    const s = t.trim().toUpperCase();
    if (!s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
    if (out.length >= MAX_PER_SIDE) break;
  }
  return out;
}

export const tokenShortlistStore = {
  get short() {
    return _short;
  },
  get long() {
    return _long;
  },
  get max() {
    return MAX_PER_SIDE;
  },

  hydrate() {
    if (_hydrated) return;
    _hydrated = true;
    if (typeof localStorage === 'undefined') return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      const short = cleanList(parsed?.short);
      const shortSet = new Set(short);
      // Enforce exclusivity on load: a token in both sides stays on short.
      _short = short;
      _long = cleanList(parsed?.long).filter((t) => !shortSet.has(t));
    } catch {
      /* corrupt persistence — start empty */
    }
  },

  /** Add a token to a side. Exclusive (removes it from the other side), skips
   *  dupes, and no-ops when the target side is already full. Returns true if the
   *  list changed. */
  add(token: string, side: ShortlistSide): boolean {
    const s = (token ?? '').trim().toUpperCase();
    if (!s) return false;
    const target = side === 'short' ? _short : _long;
    if (target.includes(s)) return false; // already on this side
    if (target.length >= MAX_PER_SIDE) return false; // full — don't move it off the other side
    if (side === 'short') {
      _long = _long.filter((t) => t !== s);
      _short = [..._short, s];
    } else {
      _short = _short.filter((t) => t !== s);
      _long = [..._long, s];
    }
    persist();
    return true;
  },

  remove(token: string, side: ShortlistSide) {
    const s = (token ?? '').trim().toUpperCase();
    if (side === 'short') _short = _short.filter((t) => t !== s);
    else _long = _long.filter((t) => t !== s);
    persist();
  },

  /** Replace a side's order (drag-reorder result), sanitised. */
  setOrder(side: ShortlistSide, tokens: string[]) {
    const cleaned = cleanList(tokens);
    if (side === 'short') _short = cleaned;
    else _long = cleaned;
    persist();
  },
};
