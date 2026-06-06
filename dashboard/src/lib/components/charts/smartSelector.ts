/**
 * Frontend twin of services/data_server/src/wallets/smart_selector.py.
 *
 * The metric catalogue here is the source of truth for the criteria UI —
 * a row in the picker is one entry here. Backend and frontend must stay in
 * sync on `key` values (those go on the wire inside the `selector` JSON);
 * labels are display-only and free to differ between layers.
 *
 * The state shape is the literal JSON the server expects, so the URL
 * builder is just `JSON.stringify(state)`.
 */

export type SmartMetricKey =
  | 'pnl_pct'
  | 'unrealized_pnl_pct'
  | 'realized_pnl'
  | 'unrealized_pnl'
  | 'total_pnl'
  | 'total_pnl_pct'
  | 'volume'
  | 'volume_token'
  | 'trade_count'
  | 'long_pnl'
  | 'short_pnl'
  | 'sharpe'
  | 'avg_total_oi_token'
  | 'avg_long_oi_token'
  | 'avg_short_oi_token'
  | 'avg_total_oi_usd'
  | 'avg_long_oi_usd'
  | 'avg_short_oi_usd'
  | 'avg_roe_pct'
  | 'long_volume_usd'
  | 'long_volume_token'
  | 'short_volume_usd'
  | 'short_volume_token'
  | 'taker_buy_volume_usd'
  | 'taker_buy_volume_token'
  | 'taker_sell_volume_usd'
  | 'taker_sell_volume_token';

export type SmartMetricKind = 'usd' | 'pct' | 'ratio' | 'count' | 'token';

export interface SmartMetricDef {
  key: SmartMetricKey;
  label: string;
  /** What the value represents — drives the UI's min/max placeholder text
   *  and the formatter used to display sample numbers in the toolbar. */
  kind: SmartMetricKind;
  /** Sensible default `min` for the picker when the user first adds this
   *  metric as a criterion. `undefined` means "no default — leave blank". */
  defaultMin?: number;
}

export const METRIC_CATALOGUE: ReadonlyArray<SmartMetricDef> = [
  { key: 'pnl_pct',                 label: 'Realized PnL / Volume %',   kind: 'pct' },
  { key: 'unrealized_pnl_pct',      label: 'Unrealized PnL / Volume %', kind: 'pct' },
  { key: 'realized_pnl',            label: 'Realized PnL ($)',          kind: 'usd', defaultMin: 10000 },
  { key: 'unrealized_pnl',          label: 'Unrealized PnL ($)',        kind: 'usd' },
  { key: 'total_pnl',               label: 'Total PnL ($)',             kind: 'usd' },
  { key: 'total_pnl_pct',           label: 'Total PnL %',               kind: 'pct' },
  { key: 'volume',                  label: 'Volume ($)',                kind: 'usd', defaultMin: 1_000_000 },
  { key: 'volume_token',            label: 'Volume (token)',            kind: 'token' },
  { key: 'long_volume_usd',         label: 'Long Volume ($)',           kind: 'usd' },
  { key: 'long_volume_token',       label: 'Long Volume (token)',       kind: 'token' },
  { key: 'short_volume_usd',        label: 'Short Volume ($)',          kind: 'usd' },
  { key: 'short_volume_token',      label: 'Short Volume (token)',      kind: 'token' },
  { key: 'taker_buy_volume_usd',    label: 'Taker Buy Volume ($)',      kind: 'usd' },
  { key: 'taker_buy_volume_token',  label: 'Taker Buy Volume (token)',  kind: 'token' },
  { key: 'taker_sell_volume_usd',   label: 'Taker Sell Volume ($)',     kind: 'usd' },
  { key: 'taker_sell_volume_token', label: 'Taker Sell Volume (token)', kind: 'token' },
  { key: 'avg_total_oi_usd',        label: 'Avg Total OI ($)',          kind: 'usd' },
  { key: 'avg_long_oi_usd',         label: 'Avg Long OI ($)',           kind: 'usd' },
  { key: 'avg_short_oi_usd',        label: 'Avg Short OI ($)',          kind: 'usd' },
  { key: 'avg_total_oi_token',      label: 'Avg Total OI (token)',      kind: 'token' },
  { key: 'avg_long_oi_token',       label: 'Avg Long OI (token)',       kind: 'token' },
  { key: 'avg_short_oi_token',      label: 'Avg Short OI (token)',      kind: 'token' },
  { key: 'avg_roe_pct',             label: 'Avg RoE (%)',               kind: 'pct' },
  { key: 'trade_count',             label: 'Trade count',               kind: 'count' },
  { key: 'long_pnl',                label: 'Long PnL ($)',              kind: 'usd' },
  { key: 'short_pnl',               label: 'Short PnL ($)',             kind: 'usd' },
  { key: 'sharpe',                  label: 'Sharpe ratio',              kind: 'ratio' },
];

export function metricDef(key: string): SmartMetricDef | undefined {
  return METRIC_CATALOGUE.find((m) => m.key === key);
}

export function isMetricKey(s: unknown): s is SmartMetricKey {
  return typeof s === 'string' && METRIC_CATALOGUE.some((m) => m.key === s);
}

export interface SmartCriterionState {
  metric: SmartMetricKey;
  min?: number;
  max?: number;
  /** Per-criterion scope override. When unset, the criterion inherits the
   *  selector's overall scope (saved as the active scope at add-time, so
   *  it's always explicit in persisted state — see defaultSmartSelector
   *  and the add-criterion handler in SmartWalletSelector). */
  scope?: 'global' | 'token';
  /** Soft-disable: criterion stays in the UI list but its min/max bounds
   *  are skipped server-side. Lets the user A/B configurations without
   *  losing the saved values. Default false (criterion is active). */
  disabled?: boolean;
}

export interface SmartSelectorState {
  lookback: number;          // 1..180
  top_n: number;             // 1..500
  scope: 'global' | 'token';
  sort_by: SmartMetricKey;
  criteria: SmartCriterionState[];
}

export function defaultSmartSelectorState(): SmartSelectorState {
  return {
    lookback: 7,
    top_n: 50,
    // Default to token-scope: most users open the selector on a token
    // chart and want criteria specific to that token; global is rarely
    // the right default since it asks the engine to scan every token.
    scope: 'token',
    sort_by: 'pnl_pct',
    criteria: [{ metric: 'realized_pnl', min: 10000, scope: 'token' }],
  };
}

/** Sanitize anything the persistence layer hands us into a valid state
 *  object. Garbage in → defaults out. */
export function sanitizeSmartSelectorState(raw: unknown): SmartSelectorState {
  const d = defaultSmartSelectorState();
  if (!raw || typeof raw !== 'object') return d;
  const r = raw as Record<string, unknown>;
  const out: SmartSelectorState = { ...d };
  if (typeof r.lookback === 'number' && r.lookback >= 1 && r.lookback <= 180) {
    out.lookback = Math.round(r.lookback);
  }
  if (typeof r.top_n === 'number' && r.top_n >= 1 && r.top_n <= 500) {
    out.top_n = Math.round(r.top_n);
  }
  if (r.scope === 'global' || r.scope === 'token') out.scope = r.scope;
  if (isMetricKey(r.sort_by)) out.sort_by = r.sort_by;
  if (Array.isArray(r.criteria)) {
    out.criteria = [];
    for (const c of r.criteria) {
      if (!c || typeof c !== 'object') continue;
      const cc = c as Record<string, unknown>;
      if (!isMetricKey(cc.metric)) continue;
      const item: SmartCriterionState = { metric: cc.metric };
      if (typeof cc.min === 'number' && isFinite(cc.min)) item.min = cc.min;
      if (typeof cc.max === 'number' && isFinite(cc.max)) item.max = cc.max;
      if (cc.scope === 'global' || cc.scope === 'token') item.scope = cc.scope;
      else item.scope = out.scope;  // inherit from overall when missing/invalid
      if (cc.disabled === true) item.disabled = true;
      out.criteria.push(item);
    }
  }
  return out;
}

/** Cache key suffix folding all selector knobs in. Two selectors that
 *  produce different leaderboards return different keys; identical
 *  selectors return identical keys. */
export function smartSelectorCacheKey(s: SmartSelectorState): string {
  // Stable stringify — keep field order deterministic to avoid
  // semantically-equal-but-string-different keys.
  const norm = {
    lookback: s.lookback, top_n: s.top_n, scope: s.scope, sort_by: s.sort_by,
    criteria: s.criteria.map((c) => ({
      metric: c.metric,
      min: c.min ?? null,
      max: c.max ?? null,
      scope: c.scope ?? null,
      disabled: c.disabled ?? false
    })),
  };
  return JSON.stringify(norm);
}
