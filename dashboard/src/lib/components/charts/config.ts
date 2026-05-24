import type {
  FundingRateRow,
  Interval,
  LongShortRow,
  OpenInterestRow,
  VolumeBucket
} from '$lib/api';

export type MAType = 'sma' | 'ema' | 'wma';

export const LOOKBACK_DAYS: Record<Interval, number> = {
  '1m': 1,
  '5m': 7,
  '15m': 30,
  '30m': 30,
  '1h': 30,
  '4h': 30,
  '1d': 30
};

export function lookbackWindow(iv: Interval): { since: Date; until: Date } {
  const now = new Date();
  const until = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
  const since = new Date(until.getTime() - LOOKBACK_DAYS[iv] * 24 * 60 * 60 * 1000);
  return { since, until };
}

export function unixSec(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000);
}

export const DEFAULT_VIEW_DAYS = 14;

/** Return a [start, end] view tuple that frames the most recent DEFAULT_VIEW_DAYS
 *  of data, or `null` (= full xExtent) if the loaded range is already shorter. */
export function defaultView(sinceIso: string, untilIso: string): [number, number] | null {
  const untilU = unixSec(untilIso);
  const sinceU = unixSec(sinceIso);
  const window = DEFAULT_VIEW_DAYS * 24 * 60 * 60;
  if (untilU - sinceU <= window) return null;
  return [untilU - window, untilU];
}

export function smaArray(vals: number[], n: number): number[] {
  const out = new Array<number>(vals.length);
  let sum = 0;
  for (let i = 0; i < vals.length; i++) {
    sum += vals[i];
    if (i >= n) sum -= vals[i - n];
    out[i] = i >= n - 1 ? sum / n : sum / (i + 1);
  }
  return out;
}

export function emaArray(vals: number[], n: number): number[] {
  const out = new Array<number>(vals.length);
  if (!vals.length) return out;
  const alpha = 2 / (n + 1);
  let ema = vals[0];
  out[0] = ema;
  for (let i = 1; i < vals.length; i++) {
    ema = alpha * vals[i] + (1 - alpha) * ema;
    out[i] = ema;
  }
  return out;
}

export function wmaArray(vals: number[], n: number): number[] {
  const out = new Array<number>(vals.length);
  for (let i = 0; i < vals.length; i++) {
    const w = Math.min(n, i + 1);
    let num = 0;
    let den = 0;
    for (let k = 0; k < w; k++) {
      const weight = w - k;
      num += vals[i - k] * weight;
      den += weight;
    }
    out[i] = den > 0 ? num / den : 0;
  }
  return out;
}

export function maArray(vals: number[], n: number, t: MAType): number[] {
  if (t === 'ema') return emaArray(vals, n);
  if (t === 'wma') return wmaArray(vals, n);
  return smaArray(vals, n);
}

export function fmtUsdAxis(v: number) {
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

export function fmtUsdTooltip(v: number) {
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(3)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(2)}`;
}

/** Tooltip timestamp helper — "Sun 2026-05-24 12:34:56 UTC". */
export function fmtUtcTime(unixSec: number): string {
  const d = new Date(unixSec * 1000);
  const weekday = d.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' });
  const iso = d.toISOString().replace('T', ' ').slice(0, 19);
  return `${weekday} ${iso} UTC`;
}

export const BUYER_SELLER_SERIES = [
  { key: 'buyer_taker_usd', label: 'Buyer', color: '#22c55e' },
  { key: 'seller_taker_usd', label: 'Seller', color: '#ef4444' }
];

export const BUYER_SELLER_LINES = [
  {
    key: 'buyer_pct',
    label: '% Buyer',
    color: '#fbbf24',
    compute: (d: VolumeBucket) => {
      const total = d.buyer_taker_usd + d.seller_taker_usd;
      return total > 0 ? (d.buyer_taker_usd / total) * 100 : 0;
    }
  }
];

export const OI_LINES = [
  {
    key: 'oi_usd',
    label: 'OI (USD)',
    color: '#06b6d4',
    compute: (d: OpenInterestRow) => d.open_interest_value
  }
];

export const TOP_TRADERS_LINES = [
  {
    key: 'top_ct',
    label: 'Top traders (count)',
    color: '#fbbf24',
    compute: (d: LongShortRow) => d.top_trader_count_ratio
  },
  {
    key: 'top_vol',
    label: 'Top traders (vol)',
    color: '#06b6d4',
    compute: (d: LongShortRow) => d.top_trader_vol_ratio
  }
];

export const LS_LINES = [
  {
    key: 'all_ct',
    label: 'All (L/S count)',
    color: '#84cc16',
    compute: (d: LongShortRow) => d.long_short_count_ratio
  },
  {
    key: 'taker_vol',
    label: 'Taker L/S vol',
    color: '#a855f7',
    compute: (d: LongShortRow) => d.taker_long_short_vol_ratio
  }
];

export const NEUTRAL_REF = [{ value: 1 }];

export function sizeSeries(under: number, over: number) {
  return [
    { key: 'small_usd', label: `< $${under}`, color: '#3f3f46' },
    { key: 'mid_usd', label: `$${under}–$${over}`, color: '#3b82f6' },
    { key: 'large_usd', label: `> $${over}`, color: '#a855f7' }
  ];
}

export function sizeLines(under: number, over: number) {
  return [
    {
      key: 'small_pct',
      label: `% < $${under}`,
      color: '#fbbf24',
      compute: (d: VolumeBucket) => {
        const total = d.small_usd + d.mid_usd + d.large_usd;
        return total > 0 ? (d.small_usd / total) * 100 : 0;
      }
    },
    {
      key: 'large_pct',
      label: `% > $${over}`,
      color: '#06b6d4',
      compute: (d: VolumeBucket) => {
        const total = d.small_usd + d.mid_usd + d.large_usd;
        return total > 0 ? (d.large_usd / total) * 100 : 0;
      }
    }
  ];
}

export type FundingRateBpsRow = FundingRateRow & { rate_bps: number };

export type ChartKind = 'ohlcv' | 'oi' | 'fr' | 'bs' | 'sz' | 'tt' | 'ls' | 'transfer';

export const CHART_KIND_LABELS: Record<ChartKind, string> = {
  ohlcv: 'OHLCV',
  oi: 'Open Interest',
  fr: 'Funding Rate',
  bs: 'Buyer vs Seller',
  sz: 'Volume by Size',
  tt: 'Top Traders L/S',
  ls: 'Long/Short',
  transfer: 'Transfer Volume'
};

export type MAConfig = {
  enabled: boolean;
  length: number;
  type: MAType;
};

/** Colours assigned by MA slot index (0..2). Used for the MA line(s) in every kind. */
export const MA_COLORS = ['#fbbf24', '#06b6d4', '#ec4899'] as const;

export const MAX_MAS = 3;

export function defaultMAs(): MAConfig[] {
  return [
    { enabled: false, length: 9, type: 'sma' },
    { enabled: false, length: 21, type: 'sma' },
    { enabled: false, length: 50, type: 'sma' }
  ];
}

export type TransferFilters = {
  // Wallet *category* filters (multi-valued list-of-string per wallet).
  sender_in?: string[];
  sender_ex?: string[];
  receiver_in?: string[];
  receiver_ex?: string[];
  involving_in?: string[];
  involving_ex?: string[];
  // Wallet *entity* filters (single nullable string per wallet, e.g. "Binance").
  sender_entity_in?: string[];
  sender_entity_ex?: string[];
  receiver_entity_in?: string[];
  receiver_entity_ex?: string[];
  involving_entity_in?: string[];
  involving_entity_ex?: string[];
};

export type ChartWidth = 1 | 2 | 4;
export type ChartHeight = 1 | 2;

export type ChartInstance = {
  id: string;
  kind: ChartKind;
  width: ChartWidth;
  height: ChartHeight;
  token: string;
  interval: Interval;
  showPoint: boolean;
  mas: MAConfig[]; // length MAX_MAS, each slot independently enabled
  // sz only
  under?: number;
  over?: number;
  underInput?: string;
  overInput?: string;
  // ohlcv only
  pin?: boolean;
  // transfer only
  chain?: string;
  /** Optional wallet-category filter applied to the transfer chart's main
   *  series. When set, the chart replaces its unfiltered sum with the filtered
   *  one (MAs computed from the filtered values too). */
  filter?: TransferFilters;
  /** Two filter sets fetched in parallel and subtracted on the client:
   *  `positive - negative` per bucket. Used by netflow-style templates
   *  (e.g. CeX Netflow = CeX Inflow − CeX Outflow). Mutually exclusive
   *  with `filter` — templates set one or the other, never both. */
  netFilter?: { positive: TransferFilters; negative: TransferFilters };
  /** If set, this chart was inserted from a template. The filter is treated as
   *  locked (no Apply/Clear UI), and the panel title uses this name instead of
   *  the generic kind label. Token / chain / interval / MAs remain editable. */
  templateName?: string;
};

/** Builder for a one-click chart preset — given the page's defaults, returns
 *  a ready-to-add ChartInstance. */
export type ChartTemplateBuild = (defaults: {
  token: string;
  chain?: string;
}) => ChartInstance;

/** A chart preset surfaced in the Insert menu.
 *
 *  Two shapes:
 *    - `{ build }` — single one-click template (e.g. "CeX Internal Flow").
 *    - `{ variants: [...] }` — parameterised template whose menu entry expands
 *      to a list of sub-choices the user picks from (e.g. "CeX Inflow" → All /
 *      Binance / Coinbase / OKX / Bybit). Each variant has its own builder. */
export type ChartTemplate = {
  id: string;
  label: string;
  build?: ChartTemplateBuild;
  variants?: {
    id: string;
    label: string;
    build: ChartTemplateBuild;
  }[];
};

/** Cycle of canonical sizes the chart can be toggled through.
 *  1×1 = compact (1 col, 270px chart),
 *  2×2 = default (2 cols, 540px chart),
 *  4×2 = wide   (4 cols, 540px chart). */
export const SIZE_CYCLE: { width: ChartWidth; height: ChartHeight }[] = [
  { width: 1, height: 1 },
  { width: 2, height: 1 },
  { width: 2, height: 2 },
  { width: 4, height: 2 }
];

export function newChartInstance(
  kind: ChartKind,
  defaults: { token: string; chain?: string }
): ChartInstance {
  const base: ChartInstance = {
    id:
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    kind,
    width: 2,
    height: 2,
    token: defaults.token,
    interval: '1h',
    showPoint: true,
    mas: defaultMAs()
  };
  if (kind === 'sz') {
    base.under = 10000;
    base.over = 100000;
    base.underInput = '10000';
    base.overInput = '100000';
  }
  if (kind === 'ohlcv') {
    base.pin = false;
  }
  if (kind === 'transfer') {
    base.chain = defaults.chain ?? 'ETH';
    base.filter = {};
  }
  return base;
}
