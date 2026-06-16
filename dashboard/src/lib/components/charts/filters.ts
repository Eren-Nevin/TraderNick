/**
 * First-class, composable wallet filters.
 *
 * A SavedFilter is a named rule that resolves to a per-day set of wallets.
 * The GENERIC concept is kind-agnostic: every filter — whatever data it ranks
 * on — ultimately yields a wallet set, so filters of ANY kind can be AND-ed
 * (per-day set-intersected) together and consumed by any "smart" chart.
 *
 * Today only ONE kind is implemented: `hl` — a Hyperliquid leaderboard built
 * from trade/position/funding/volume criteria (the backend resolver is
 * SmartSelector in services/data_server/src/wallets/smart_selector.py). Future
 * kinds (e.g. an `exchange_flow` filter ranking wallets by average deposit
 * value to exchange addresses) plug in by adding a config variant here plus a
 * backend resolver that emits the same `(day, wallets[])` CTE shape — at which
 * point an exchange-flow filter could be combined with an HL smart-OI chart,
 * even if that pairing is unusual.
 *
 * `refs` is kind-agnostic on purpose: a filter's building blocks may be of any
 * kind. What is currently kind-limited is *resolution* — only `hl` nodes can
 * be expanded into the `filter=` wire the hl-only backend understands; a
 * subtree containing a not-yet-resolvable kind reports as `broken`.
 *
 * Filters live in localStorage (see ../../stores/filters.svelte.ts). Smart-
 * money charts reference them by id and inline-expand them at fetch time.
 */
import {
  type SmartSelectorState,
  sanitizeSmartSelectorState,
} from './smartSelector';

/** Discriminator for the kind of data a filter ranks on. Add new kinds here. */
export type FilterKind = 'hl';

/** Hyperliquid filter: criteria-based leaderboard over HL trade/position/
 *  funding/volume tables. `selector` is the SmartSelector config 1:1. */
export interface HlFilterConfig {
  kind: 'hl';
  selector: SmartSelectorState;
}

/** Kind-discriminated payload. Union grows as new filter kinds land
 *  (e.g. `| ExchangeFlowFilterConfig`). */
export type FilterConfig = HlFilterConfig;

export interface SavedFilter {
  id: string;
  name: string;
  /** Type-specific ranking config, discriminated by `config.kind`. */
  config: FilterConfig;
  /** Ids of other saved filters AND-ed (intersected) into this one. May be
   *  of any kind — all filters resolve to a wallet set. */
  refs: string[];
}

/** Recursive wire shape the hl backend's `filter=` param expects: a selector
 *  with an optional nested `refs` array (named refs inlined as full objects).
 *  Only `hl`-kind filters produce this; see expandFilter. */
export type FilterWire = SmartSelectorState & { refs?: FilterWire[] };

export type FilterStatus = 'ok' | 'missing' | 'broken';

type GetById = (id: string) => SavedFilter | undefined;

/** Human label for a filter kind (chips / badges). */
export function filterKindLabel(kind: FilterKind): string {
  switch (kind) {
    case 'hl':
      return 'Hyperliquid';
    default:
      return kind;
  }
}

/** Build a default hl config (used by "new filter" and migration). */
export function hlConfig(selector: SmartSelectorState): HlFilterConfig {
  return { kind: 'hl', selector };
}

/** Sanitize an unknown blob (from localStorage) into a SavedFilter, or null
 *  if it can't be salvaged. Accepts the legacy pre-kind shape (a bare
 *  `selector` field) and lifts it into an hl config. */
export function sanitizeSavedFilter(raw: unknown): SavedFilter | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== 'string' || typeof r.name !== 'string') return null;
  const refs = Array.isArray(r.refs)
    ? r.refs.filter((x): x is string => typeof x === 'string')
    : [];
  // config.kind drives the variant; default + legacy fall back to hl.
  const cfg = r.config as Record<string, unknown> | undefined;
  let config: FilterConfig;
  if (cfg && cfg.kind === 'hl') {
    config = hlConfig(sanitizeSmartSelectorState(cfg.selector));
  } else if (r.selector !== undefined) {
    // Legacy pre-kind shape: { selector } with no config wrapper.
    config = hlConfig(sanitizeSmartSelectorState(r.selector));
  } else {
    config = hlConfig(sanitizeSmartSelectorState(undefined));
  }
  return { id: r.id, name: r.name, config, refs };
}

/** Expand a saved filter into the recursive wire object the hl backend wants.
 *  Returns null if the filter is missing, part of a cycle, contains a
 *  not-yet-resolvable (non-hl) node anywhere in its subtree, or has a missing
 *  ref — callers treat null as "broken, show placeholder" (per product
 *  decision: never silently drop a constraint). */
export function expandFilter(
  id: string,
  getById: GetById,
  stack: ReadonlySet<string> = new Set(),
): FilterWire | null {
  if (stack.has(id)) return null; // cycle
  const f = getById(id);
  if (!f) return null; // missing
  if (f.config.kind !== 'hl') return null; // no backend resolver yet
  const nextStack = new Set(stack);
  nextStack.add(id);
  const childWires: FilterWire[] = [];
  for (const rid of f.refs) {
    const w = expandFilter(rid, getById, nextStack);
    if (w === null) return null; // broken subtree ⇒ whole filter is broken
    childWires.push(w);
  }
  const wire: FilterWire = { ...f.config.selector };
  if (childWires.length) wire.refs = childWires;
  return wire;
}

/** Health of a filter for UI messaging, distinguishing the failure modes. */
export function filterStatus(id: string, getById: GetById): FilterStatus {
  if (!getById(id)) return 'missing';
  return expandFilter(id, getById) === null ? 'broken' : 'ok';
}

/** Ids of refs (transitive) that don't resolve — for the builder dialog to
 *  flag exactly which building blocks are gone. */
export function missingRefs(id: string, getById: GetById): string[] {
  const out = new Set<string>();
  const walk = (fid: string, stack: Set<string>) => {
    if (stack.has(fid)) return;
    const f = getById(fid);
    if (!f) {
      out.add(fid);
      return;
    }
    const next = new Set(stack);
    next.add(fid);
    for (const rid of f.refs) walk(rid, next);
  };
  walk(id, new Set());
  out.delete(id);
  return [...out];
}

/** Stable hash of an expanded wire filter, mirroring the backend
 *  `_full_canonical` (children sorted so ref order is irrelevant). Drives the
 *  chart's loadKey so editing a filter (or anything it references) re-fetches. */
export function filterWireKey(wire: FilterWire): string {
  const canon = (w: FilterWire): unknown => ({
    lookback: w.lookback,
    top_n: w.top_n,
    scope: w.scope,
    sort_by: w.sort_by,
    criteria: w.criteria.map((c) => ({
      metric: c.metric,
      min: c.min ?? null,
      max: c.max ?? null,
      scope: c.scope ?? null,
      lookback: c.lookback ?? null,
      min_days: c.min_days ?? null,
      disabled: c.disabled ?? false,
    })),
    refs: (w.refs ?? [])
      .map(canon)
      .sort((a, b) => (JSON.stringify(a) < JSON.stringify(b) ? -1 : 1)),
  });
  return JSON.stringify(canon(wire));
}
