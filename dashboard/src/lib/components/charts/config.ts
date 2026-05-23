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
  '5m': 3,
  '15m': 7,
  '30m': 14,
  '1h': 14,
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
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

export function fmtUsdTooltip(v: number) {
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(3)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(2)}`;
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

export type ChartKind = 'ohlcv' | 'oi' | 'fr' | 'bs' | 'sz' | 'tt' | 'ls';

export const CHART_KIND_LABELS: Record<ChartKind, string> = {
  ohlcv: 'OHLCV',
  oi: 'Open Interest',
  fr: 'Funding Rate',
  bs: 'Buyer vs Seller',
  sz: 'Volume by Size',
  tt: 'Top Traders L/S',
  ls: 'Long/Short'
};

export type ChartInstance = {
  id: string;
  kind: ChartKind;
  width: 1 | 2;
  token: string;
  interval: Interval;
  showPoint: boolean;
  showCumulative: boolean;
  maLength: number;
  maType: MAType;
  // sz only
  under?: number;
  over?: number;
  underInput?: string;
  overInput?: string;
  // ohlcv only
  pin?: boolean;
};

export function newChartInstance(kind: ChartKind, defaults: { token: string }): ChartInstance {
  const base: ChartInstance = {
    id: typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    kind,
    width: kind === 'ohlcv' ? 2 : 1,
    token: defaults.token,
    interval: '1h',
    showPoint: true,
    showCumulative: false,
    maLength: 9,
    maType: 'sma'
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
  return base;
}
