import type {
  FundingRateRow,
  Interval,
  LongShortRow,
  OpenInterestRow,
  VolumeBucket
} from '$lib/api';
import { sanitizeSmartSelectorState } from './smartSelector';

export type MAType = 'sma' | 'ema' | 'wma';

// Default chart window per interval — tracks the ClickHouse TTL (180
// days). 1m / 5m stay tighter (30d) because painting 30×1440 minute
// candles is already heavy; longer history at minute granularity is
// rarely useful and the user can switch to a coarser interval when
// they want more history.
export const LOOKBACK_DAYS: Record<Interval, number> = {
  '1m':  30,
  '5m':  30,
  '15m': 180,
  '30m': 180,
  '1h':  180,
  '4h':  180,
  '1d':  180
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

// Compact USD formatter capped at 3 significant figures — gives readable
// scaling for high-magnitude numbers (e.g. $42.3M, $2.55B, $942K) without
// the trailing-decimal noise of fmtUsdTooltip. Used by the OHLCV chart's
// USD volume tooltip.
const _usdCompactFmt = new Intl.NumberFormat('en-US', {
  notation: 'compact',
  maximumSignificantDigits: 3,
});
export function fmtUsdCompact(v: number) {
  return `${v < 0 ? '-' : ''}$${_usdCompactFmt.format(Math.abs(v))}`;
}

/** Unitless ratio formatter — used by the HL OI Long/Short ratio mode and
 *  any future ratio overlays. Two decimals is enough resolution for the
 *  typical 0.3–3.0 range while staying compact on the axis. */
export function fmtRatio(v: number) {
  if (!isFinite(v)) return '—';
  return v.toFixed(2);
}

/** Token-amount formatters — same K/M/B abbreviation as the USD ones but
 *  without the $ prefix. Used when an event-driven chart is toggled to
 *  amount mode (sum of raw token amount instead of USD value). */
export function fmtAmountAxis(v: number) {
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(0)}K`;
  if (abs >= 1)   return `${sign}${abs.toFixed(0)}`;
  return `${sign}${abs.toFixed(3)}`;
}

export function fmtAmountTooltip(v: number) {
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(3)}B`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}K`;
  if (abs >= 1)   return `${sign}${abs.toFixed(2)}`;
  return `${sign}${abs.toFixed(4)}`;
}

/** Return the Unix-second timestamps for every Saturday- and Monday-00:00
 *  UTC inside [sinceSec, untilSec]. Used by the optional "Week lines"
 *  overlay so users can eyeball weekly cycles. */
export function weekBoundariesSec(sinceSec: number, untilSec: number): number[] {
  if (!Number.isFinite(sinceSec) || !Number.isFinite(untilSec) || untilSec <= sinceSec) {
    return [];
  }
  const DAY = 86_400;
  // Floor `since` to the start of the day (UTC), then sweep one day at a time.
  const startOfDay = Math.floor(sinceSec / DAY) * DAY;
  const out: number[] = [];
  for (let t = startOfDay; t <= untilSec; t += DAY) {
    const dow = new Date(t * 1000).getUTCDay(); // Sun=0 ... Sat=6
    if ((dow === 1 || dow === 6) && t >= sinceSec) out.push(t);
  }
  return out;
}

/** Tooltip timestamp helper — "Sun 2026-05-24 12:34:56 UTC".
 *  Memoized: hover/crosshair redraws hit this on the same handful of
 *  timestamps thousands of times; `toLocaleDateString` is a measurable
 *  fraction of hover self-time in profiles. Cap at 50k so the cache
 *  can't grow unbounded over a long session. */
const _fmtUtcTimeCache = new Map<number, string>();
export function fmtUtcTime(unixSec: number): string {
  const hit = _fmtUtcTimeCache.get(unixSec);
  if (hit !== undefined) return hit;
  const d = new Date(unixSec * 1000);
  const weekday = d.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' });
  const iso = d.toISOString().replace('T', ' ').slice(0, 19);
  const out = `${weekday} ${iso} UTC`;
  if (_fmtUtcTimeCache.size > 50_000) _fmtUtcTimeCache.clear();
  _fmtUtcTimeCache.set(unixSec, out);
  return out;
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

// Taker buyer/seller SHARE lines: % of total taker volume on each side (sum to
// 100). Param typed Datum-compatible (fields optional + time) so it satisfies
// LineChart's Line[].
export const BUYER_SELLER_PCT_LINES = [
  {
    key: 'bs_buyer_pct',
    label: '% Buyer',
    color: '#22c55e',
    compute: (d: { time: number; buyer_taker_usd?: number; seller_taker_usd?: number }) => {
      const t = (d.buyer_taker_usd ?? 0) + (d.seller_taker_usd ?? 0);
      return t > 0 ? ((d.buyer_taker_usd ?? 0) / t) * 100 : 0;
    }
  },
  {
    key: 'bs_seller_pct',
    label: '% Seller',
    color: '#ef4444',
    compute: (d: { time: number; buyer_taker_usd?: number; seller_taker_usd?: number }) => {
      const t = (d.buyer_taker_usd ?? 0) + (d.seller_taker_usd ?? 0);
      return t > 0 ? ((d.seller_taker_usd ?? 0) / t) * 100 : 0;
    }
  }
];

// Taker buyer/seller RATIO line. >1 = more taker buying, <1 = more selling,
// 1 = balanced. scale:'ratio' puts it on the secondary (left) axis when shown
// alongside the stacked $ bars ('both' mode); on its own it's a plain line.
export const BUYER_SELLER_RATIO_LINES = [
  {
    key: 'bs_ratio',
    label: 'Buyer / Seller',
    color: '#fbbf24',
    scale: 'ratio' as const,
    // Param typed as a Datum-compatible shape (fields optional) so the line
    // satisfies both LineChart's and StackedBarChart's Line[] — a bare
    // VolumeBucket has required fields and isn't a supertype of Datum.
    compute: (d: { time: number; buyer_taker_usd?: number; seller_taker_usd?: number }) => {
      const s = d.seller_taker_usd ?? 0;
      return s > 0 ? (d.buyer_taker_usd ?? 0) / s : 0;
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
  },
  {
    // Volume-per-trader L/S ratio: the vol L/S ratio divided by the count L/S
    // ratio = (avg vol per long trader) / (avg vol per short trader). Reads
    // > 1 when longs trade bigger on average than shorts, < 1 when smaller.
    key: 'top_avg_vol',
    label: 'Top traders (avg vol)',
    color: '#a855f7',
    compute: (d: LongShortRow) =>
      d.top_trader_count_ratio ? d.top_trader_vol_ratio / d.top_trader_count_ratio : 0
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
export const ZERO_REF = [{ value: 0 }];

export function sizeSeries(under: number, over: number) {
  return [
    { key: 'small_usd', label: `< $${under}`, color: '#3f3f46' },
    { key: 'mid_usd', label: `$${under}–$${over}`, color: '#3b82f6' },
    { key: 'large_usd', label: `> $${over}`, color: '#a855f7' }
  ];
}

// Absolute per-bucket USD volume as line series (small / mid / large), for the
// line-rendered Volume-by-Size chart. Same fields the stacked bars used, just
// plotted as independent lines instead of a stack.
export function sizeLineSeries(under: number, over: number) {
  return [
    { key: 'small_usd', label: `< $${under}`,        color: '#3f3f46', compute: (d: VolumeBucket) => d.small_usd },
    { key: 'mid_usd',   label: `$${under}–$${over}`, color: '#3b82f6', compute: (d: VolumeBucket) => d.mid_usd },
    { key: 'large_usd', label: `> $${over}`,         color: '#a855f7', compute: (d: VolumeBucket) => d.large_usd }
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

export type ChartKind =
  | 'ohlcv'
  | 'price'
  | 'price_ratio'
  | 'oi'
  | 'vol_oi'
  | 'volume'
  | 'fr'
  | 'book_depth'
  | 'bs'
  | 'sz'
  | 'tt'
  | 'ls'
  | 'token_leaderboard'
  | 'smart_wallets_table'
  | 'transfer'
  | 'exchange_flow'
  | 'pc'
  | 'morpho'
  | 'spark'
  | 'aave_v3'
  | 'aave_v2'
  | 'aave_v4'
  | 'aave_v3_deposit'
  | 'aave_v3_withdraw'
  | 'aave_v3_net_deposit'
  | 'aave_v3_borrow'
  | 'aave_v3_repay'
  | 'aave_v3_net_borrow'
  | 'aave_v3_flashloan'
  | 'aave_v3_liquidation'
  | 'aave_v2_deposit'
  | 'aave_v2_withdraw'
  | 'aave_v2_net_deposit'
  | 'aave_v2_borrow'
  | 'aave_v2_repay'
  | 'aave_v2_net_borrow'
  | 'aave_v2_flashloan'
  | 'aave_v2_liquidation'
  | 'aave_v4_deposit'
  | 'aave_v4_withdraw'
  | 'aave_v4_net_deposit'
  | 'aave_v4_borrow'
  | 'aave_v4_repay'
  | 'aave_v4_net_borrow'
  | 'aave_v4_liquidation'
  | 'aave_v2_top_wallets'
  | 'aave_v3_top_wallets'
  | 'aave_v4_top_wallets'
  | 'uniswap_v2_top_wallets'
  | 'uniswap_v3_top_wallets'
  | 'uniswap_v4_top_wallets'
  | 'morpho_supply'
  | 'morpho_withdraw'
  | 'morpho_net_supply'
  | 'morpho_borrow'
  | 'morpho_repay'
  | 'morpho_net_borrow'
  | 'morpho_supply_collateral'
  | 'morpho_withdraw_collateral'
  | 'morpho_net_collateral'
  | 'morpho_liquidation'
  | 'spark_deposit'
  | 'spark_withdraw'
  | 'spark_net_deposit'
  | 'spark_borrow'
  | 'spark_repay'
  | 'spark_net_borrow'
  | 'spark_flashloan'
  | 'spark_liquidation'
  | 'gmx_v2'
  | 'gmx_v2_position_increase'
  | 'gmx_v2_position_decrease'
  | 'gmx_v2_net_position'
  | 'gmx_v2_liquidation'
  | 'gmx_v2_swap'
  | 'gmx_v2_deposit'
  | 'gmx_v2_withdraw'
  | 'gmx_v2_net_lp'
  | 'hl_pnl'
  | 'hl_unrealized_pnl'
  | 'hl_transfers'
  | 'hl_vault_net'
  | 'hl_top_vaults'
  | 'hl_top_vault_lps'
  | 'hl_vault_detail'
  | 'hl_top_traders'
  | 'hl_top_positions'
  | 'hl_smart_oi'
  | 'uniswap_v2_swap'
  | 'uniswap_v2_deposit'
  | 'uniswap_v2_withdraw'
  | 'uniswap_v2_net_liquidity'
  | 'uniswap_v4_swap'
  | 'uniswap_v4_deposit'
  | 'uniswap_v4_withdraw'
  | 'uniswap_v4_initialize'
  | 'uniswap_v4_net_liquidity'
  | 'aero_cl'
  | 'aero_basic'
  | 'aero_cl_swap'
  | 'aero_cl_deposit'
  | 'aero_cl_withdraw'
  | 'aero_cl_collect'
  | 'aero_cl_net_liquidity'
  | 'aero_basic_swap'
  | 'aero_basic_deposit'
  | 'aero_basic_withdraw'
  | 'aero_basic_claim'
  | 'aero_basic_net_liquidity'
  | 'uniswap_v3'
  | 'uniswap_v2'
  | 'uniswap_v4'
  | 'uniswap_v3_swap'
  | 'uniswap_v3_deposit'
  | 'uniswap_v3_withdraw'
  | 'uniswap_v3_collect'
  | 'uniswap_v3_net_liquidity'
  | 'uniswap_v3_net_swap_flow'
  | 'lido'
  | 'lido_deposit'
  | 'lido_withdrawal_request'
  | 'lido_withdrawal_claimed'
  | 'lido_net_stake'
  | 'lido_net_request_stake'
  | 'lido_request_pending'
  | 'lido_l2_deposit'
  | 'lido_l2_withdrawal_request'
  | 'lido_l2_net';

export const CHART_KIND_LABELS: Record<ChartKind, string> = {
  ohlcv: 'OHLCV',
  price: 'Price',
  price_ratio: 'Price Ratio',
  // pc: chart token shown relative to one or more base tokens (a price-ratio
  // line per base). Labelled below at the `pc:` key.
  oi: 'Open Interest',
  vol_oi: 'Vol / OI',
  volume: 'Volume',
  fr: 'Funding Rate',
  book_depth: 'Book Depth',
  bs: 'Taker Buyer vs Seller',
  sz: 'Volume by Size',
  tt: 'Top Traders L/S',
  ls: 'Long/Short',
  token_leaderboard: 'Token Leaderboard',
  smart_wallets_table: 'Smart Wallets',
  transfer: 'Token Flow',
  exchange_flow: 'Exchange Flow',
  pc: 'Relative Price',
  aave_v3: 'AAVE V3',
  aave_v2: 'AAVE V2',
  aave_v4: 'AAVE V4',
  aave_v3_deposit: 'AAVE V3 Deposits',
  aave_v3_withdraw: 'AAVE V3 Withdrawals',
  aave_v3_net_deposit: 'AAVE V3 Net Deposit',
  aave_v3_borrow: 'AAVE V3 Borrows',
  aave_v3_repay: 'AAVE V3 Repays',
  aave_v3_net_borrow: 'AAVE V3 Net Borrow',
  aave_v3_flashloan: 'AAVE V3 Flash Loans',
  aave_v3_liquidation: 'AAVE V3 Liquidations',
  aave_v2_deposit: 'AAVE V2 Deposits',
  aave_v2_withdraw: 'AAVE V2 Withdrawals',
  aave_v2_net_deposit: 'AAVE V2 Net Deposit',
  aave_v2_borrow: 'AAVE V2 Borrows',
  aave_v2_repay: 'AAVE V2 Repays',
  aave_v2_net_borrow: 'AAVE V2 Net Borrow',
  aave_v2_flashloan: 'AAVE V2 Flash Loans',
  aave_v2_liquidation: 'AAVE V2 Liquidations',
  aave_v4_deposit: 'AAVE V4 Deposits',
  aave_v4_withdraw: 'AAVE V4 Withdrawals',
  aave_v4_net_deposit: 'AAVE V4 Net Deposit',
  aave_v4_borrow: 'AAVE V4 Borrows',
  aave_v4_repay: 'AAVE V4 Repays',
  aave_v4_net_borrow: 'AAVE V4 Net Borrow',
  aave_v4_liquidation: 'AAVE V4 Liquidations',
  aave_v2_top_wallets: 'AAVE V2 Top Wallets',
  aave_v3_top_wallets: 'AAVE V3 Top Wallets',
  aave_v4_top_wallets: 'AAVE V4 Top Wallets',
  uniswap_v2_top_wallets: 'Uniswap V2 Top Wallets',
  uniswap_v3_top_wallets: 'Uniswap V3 Top Wallets',
  uniswap_v4_top_wallets: 'Uniswap V4 Top Wallets',
  morpho: 'Morpho',
  morpho_supply: 'Morpho Supplies',
  morpho_withdraw: 'Morpho Withdrawals',
  morpho_net_supply: 'Morpho Net Supply',
  morpho_borrow: 'Morpho Borrows',
  morpho_repay: 'Morpho Repays',
  morpho_net_borrow: 'Morpho Net Borrow',
  morpho_supply_collateral: 'Morpho Supply Collateral',
  morpho_withdraw_collateral: 'Morpho Withdraw Collateral',
  morpho_net_collateral: 'Morpho Net Collateral',
  morpho_liquidation: 'Morpho Liquidations',
  spark: 'Spark',
  spark_deposit: 'Spark Deposits',
  spark_withdraw: 'Spark Withdrawals',
  spark_net_deposit: 'Spark Net Deposit',
  spark_borrow: 'Spark Borrows',
  spark_repay: 'Spark Repays',
  spark_net_borrow: 'Spark Net Borrow',
  spark_flashloan: 'Spark Flash Loans',
  hl_pnl: 'HL Realized PnL',
  hl_unrealized_pnl: 'HL Unrealized PnL',
  hl_transfers: 'HL Bridge Flows',
  hl_vault_net: 'HL Vault Flow',
  hl_top_vaults: 'HL Top Vaults',
  hl_top_vault_lps: 'HL Top Vault LPs',
  hl_vault_detail: 'HL Vault Detail',
  hl_top_traders: 'HL Top Traders',
  hl_top_positions: 'HL Top Positions',
  hl_smart_oi: 'HL Smart-Money OI',
  gmx_v2: 'GMX',
  gmx_v2_position_increase: 'GMX Position Open',
  gmx_v2_position_decrease: 'GMX Position Close',
  gmx_v2_net_position: 'GMX Net Position Flow',
  gmx_v2_liquidation: 'GMX Liquidations',
  gmx_v2_swap: 'GMX Swaps',
  gmx_v2_deposit: 'GMX LP Deposits',
  gmx_v2_withdraw: 'GMX LP Withdrawals',
  gmx_v2_net_lp: 'GMX Net LP Flow',
  spark_liquidation: 'Spark Liquidations',
  uniswap_v2_swap: 'Uniswap V2 Swaps',
  uniswap_v2_deposit: 'Uniswap V2 Deposits',
  uniswap_v2_withdraw: 'Uniswap V2 Withdrawals',
  uniswap_v2_net_liquidity: 'Uniswap V2 Net Liquidity',
  uniswap_v4_swap: 'Uniswap V4 Swaps',
  uniswap_v4_deposit: 'Uniswap V4 Deposits',
  uniswap_v4_withdraw: 'Uniswap V4 Withdrawals',
  uniswap_v4_initialize: 'Uniswap V4 Pool Initializations',
  uniswap_v4_net_liquidity: 'Uniswap V4 Net Liquidity',
  aero_cl: 'Aerodrome CL',
  aero_basic: 'Aerodrome Basic',
  aero_cl_swap: 'Aerodrome CL Swaps',
  aero_cl_deposit: 'Aerodrome CL Deposits',
  aero_cl_withdraw: 'Aerodrome CL Withdrawals',
  aero_cl_collect: 'Aerodrome CL Collects',
  aero_cl_net_liquidity: 'Aerodrome CL Net Liquidity',
  aero_basic_swap: 'Aerodrome Basic Swaps',
  aero_basic_deposit: 'Aerodrome Basic Deposits',
  aero_basic_withdraw: 'Aerodrome Basic Withdrawals',
  aero_basic_claim: 'Aerodrome Basic Claims',
  aero_basic_net_liquidity: 'Aerodrome Basic Net Liquidity',
  uniswap_v3: 'Uniswap V3',
  uniswap_v2: 'Uniswap V2',
  uniswap_v4: 'Uniswap V4',
  uniswap_v3_swap: 'Uniswap V3 Swaps',
  uniswap_v3_deposit: 'Uniswap V3 Deposits',
  uniswap_v3_withdraw: 'Uniswap V3 Withdrawals',
  uniswap_v3_collect: 'Uniswap V3 Collects',
  uniswap_v3_net_liquidity: 'Uniswap V3 Net Liquidity',
  uniswap_v3_net_swap_flow: 'Uniswap V3 Net Swap Flow',
  lido: 'Lido',
  lido_deposit: 'Lido Deposits',
  lido_withdrawal_request: 'Lido Withdrawal Requests',
  lido_withdrawal_claimed: 'Lido Withdrawal Claims',
  lido_net_stake: 'Lido Net Stake',
  lido_net_request_stake: 'Lido Net Request Stake',
  lido_request_pending: 'Lido New Pending Requests',
  lido_l2_deposit: 'Lido L2 Deposits',
  lido_l2_withdrawal_request: 'Lido L2 Withdrawal Requests',
  lido_l2_net: 'Lido L2 Net'
};

/** Top-wallets leaderboard kind config — the single extension seam for
 *  this feature. Adding a new protocol's leaderboard is one entry here,
 *  one backend route, and one availableKinds registration. The metric
 *  array drives the table's column set (so a protocol without liquidation
 *  just passes a shorter list). */
export type LeaderboardMetric =
  | 'deposit' | 'withdraw' | 'net_deposit'
  | 'borrow'  | 'repay'    | 'net_borrow'
  | 'liquidation'
  | 'swap' | 'collect' | 'net_lp';

export type LeaderboardColumn = {
  key: LeaderboardMetric;
  /** Label shown in the toolbar selector and the column header. */
  label: string;
  /** Sub-fields on each row, in the order they should render in the cell.
   *  `usd` is the headline number; `count` is the smaller event-count
   *  shown beneath it. */
  usdField: string;
  countField: string;
};

export const AAVE_LEADERBOARD_METRICS: ReadonlyArray<LeaderboardColumn> = [
  { key: 'deposit',     label: 'Deposit',     usdField: 'deposit_usd',     countField: 'deposit_count' },
  { key: 'withdraw',    label: 'Withdraw',    usdField: 'withdraw_usd',    countField: 'withdraw_count' },
  { key: 'net_deposit', label: 'Net Deposit', usdField: 'net_deposit_usd', countField: '' },
  { key: 'borrow',      label: 'Borrow',      usdField: 'borrow_usd',      countField: 'borrow_count' },
  { key: 'repay',       label: 'Repay',       usdField: 'repay_usd',       countField: 'repay_count' },
  { key: 'net_borrow',  label: 'Net Borrow',  usdField: 'net_borrow_usd',  countField: '' },
  { key: 'liquidation', label: 'Liquidation', usdField: 'liquidation_usd', countField: 'liquidation_count' },
];

/** Uniswap V2: pool-scoped (no fee tier, no collect — V2 auto-compounds fees). */
export const UNISWAP_V2_LEADERBOARD_METRICS: ReadonlyArray<LeaderboardColumn> = [
  { key: 'swap',     label: 'Swap',     usdField: 'swap_usd',     countField: 'swap_count' },
  { key: 'deposit',  label: 'LP Add',   usdField: 'deposit_usd',  countField: 'deposit_count' },
  { key: 'withdraw', label: 'LP Remove',usdField: 'withdraw_usd', countField: 'withdraw_count' },
  { key: 'net_lp',   label: 'Net LP',   usdField: 'net_lp_usd',   countField: '' },
];

/** Uniswap V3: adds Collect (fee harvesting) on top of the V2 set. */
export const UNISWAP_V3_LEADERBOARD_METRICS: ReadonlyArray<LeaderboardColumn> = [
  { key: 'swap',     label: 'Swap',     usdField: 'swap_usd',     countField: 'swap_count' },
  { key: 'deposit',  label: 'LP Add',   usdField: 'deposit_usd',  countField: 'deposit_count' },
  { key: 'withdraw', label: 'LP Remove',usdField: 'withdraw_usd', countField: 'withdraw_count' },
  { key: 'net_lp',   label: 'Net LP',   usdField: 'net_lp_usd',   countField: '' },
  { key: 'collect',  label: 'Collect',  usdField: 'collect_usd',  countField: 'collect_count' },
];

/** Uniswap V4: same as V2 (no collect event in the indexed tables). */
export const UNISWAP_V4_LEADERBOARD_METRICS: ReadonlyArray<LeaderboardColumn> = [
  { key: 'swap',     label: 'Swap',     usdField: 'swap_usd',     countField: 'swap_count' },
  { key: 'deposit',  label: 'LP Add',   usdField: 'deposit_usd',  countField: 'deposit_count' },
  { key: 'withdraw', label: 'LP Remove',usdField: 'withdraw_usd', countField: 'withdraw_count' },
  { key: 'net_lp',   label: 'Net LP',   usdField: 'net_lp_usd',   countField: '' },
];

/** `paramShape` selects how ChartInstance builds the request querystring for
 *  this leaderboard. AAVE keeps (chain, token) + optional groups + eth_market;
 *  Uniswap kinds key by pool tuple (sym0/sym1/fee/...). */
export type LeaderboardParamShape =
  | 'aave' | 'uniswap_v2' | 'uniswap_v3' | 'uniswap_v4';

export type LeaderboardKindConfig = {
  endpoint: string;
  metrics: ReadonlyArray<LeaderboardColumn>;
  protocolLabel: string;
  paramShape: LeaderboardParamShape;
  /** Default metric to sort by when a fresh instance of this kind is added. */
  defaultMetric: LeaderboardMetric;
};

export const LEADERBOARD_KIND_CONFIG: Partial<Record<ChartKind, LeaderboardKindConfig>> = {
  aave_v2_top_wallets: {
    endpoint: '/api/aave_v2/wallets/leaderboard',
    metrics: AAVE_LEADERBOARD_METRICS,
    protocolLabel: 'AAVE V2',
    paramShape: 'aave',
    defaultMetric: 'deposit'
  },
  aave_v3_top_wallets: {
    endpoint: '/api/aave/wallets/leaderboard',
    metrics: AAVE_LEADERBOARD_METRICS,
    protocolLabel: 'AAVE V3',
    paramShape: 'aave',
    defaultMetric: 'deposit'
  },
  aave_v4_top_wallets: {
    endpoint: '/api/aave_v4/wallets/leaderboard',
    metrics: AAVE_LEADERBOARD_METRICS,
    protocolLabel: 'AAVE V4',
    paramShape: 'aave',
    defaultMetric: 'deposit'
  },
  uniswap_v2_top_wallets: {
    endpoint: '/api/uniswap_v2/wallets/leaderboard',
    metrics: UNISWAP_V2_LEADERBOARD_METRICS,
    protocolLabel: 'Uniswap V2',
    paramShape: 'uniswap_v2',
    defaultMetric: 'swap'
  },
  uniswap_v3_top_wallets: {
    endpoint: '/api/uniswap/wallets/leaderboard',
    metrics: UNISWAP_V3_LEADERBOARD_METRICS,
    protocolLabel: 'Uniswap V3',
    paramShape: 'uniswap_v3',
    defaultMetric: 'swap'
  },
  uniswap_v4_top_wallets: {
    endpoint: '/api/uniswap_v4/wallets/leaderboard',
    metrics: UNISWAP_V4_LEADERBOARD_METRICS,
    protocolLabel: 'Uniswap V4',
    paramShape: 'uniswap_v4',
    defaultMetric: 'swap'
  }
};

export function isLeaderboardKind(kind: ChartKind): boolean {
  return LEADERBOARD_KIND_CONFIG[kind] !== undefined;
}

/** AAVE chart kinds collected for convenience (loop over them on the
 *  lending page + share helpers). Order matters — used as the default
 *  layout order on the Lending page. */
export const AAVE_V3_CHART_KINDS: ChartKind[] = [
  'aave_v3_deposit',
  'aave_v3_withdraw',
  'aave_v3_net_deposit',
  'aave_v3_borrow',
  'aave_v3_repay',
  'aave_v3_net_borrow',
  'aave_v3_flashloan',
  'aave_v3_liquidation'
];

/** Map from a single-event ChartKind to the AAVE V3 event slug. */
export const AAVE_V3_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  aave_v3_deposit: 'deposit',
  aave_v3_withdraw: 'withdraw',
  aave_v3_borrow: 'borrow',
  aave_v3_repay: 'repay',
  aave_v3_flashloan: 'flashloan',
  aave_v3_liquidation: 'liquidation'
};

/** Net AAVE V3 kinds — each fetches two regular event aggregates in
 *  parallel and plots positive − negative per bucket. positive[0] is
 *  added, positive[1] is subtracted. */
export const AAVE_V3_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  aave_v3_net_deposit: ['deposit', 'withdraw'],
  aave_v3_net_borrow: ['borrow', 'repay']
};

/** True for any AAVE V3 kind (single-event, net, or the general wrapper).
 *  The 'aave_v3' wrapper kind delegates to a concrete aave_v3_* event via
 *  instance.aaveV3Subkind; every AAVE V3 routing branch reads the subkind
 *  through the effective-kind derived value. */
export function isAaveV3Kind(kind: ChartKind): boolean {
  if (kind === 'aave_v3') return true;
  return (
    AAVE_V3_KIND_TO_EVENT[kind] !== undefined ||
    AAVE_V3_NET_KIND_TO_EVENTS[kind] !== undefined
  );
}

/** AAVE V2 chart kinds (legacy mainnet + Polygon). Same 6-event + 2-net
 *  taxonomy as V3 minus the eth_market axis (V2 was a single pool per
 *  chain). The Lending page exposes these alongside the V3 kinds. */
export const AAVE_V2_CHART_KINDS: ChartKind[] = [
  'aave_v2_deposit',
  'aave_v2_withdraw',
  'aave_v2_net_deposit',
  'aave_v2_borrow',
  'aave_v2_repay',
  'aave_v2_net_borrow',
  'aave_v2_flashloan',
  'aave_v2_liquidation'
];
export const AAVE_V2_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  aave_v2_deposit: 'deposit',
  aave_v2_withdraw: 'withdraw',
  aave_v2_borrow: 'borrow',
  aave_v2_repay: 'repay',
  aave_v2_flashloan: 'flashloan',
  aave_v2_liquidation: 'liquidation'
};
export const AAVE_V2_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  aave_v2_net_deposit: ['deposit', 'withdraw'],
  aave_v2_net_borrow: ['borrow', 'repay']
};
export function isAaveV2Kind(kind: ChartKind): boolean {
  // The 'aave_v2' wrapper kind delegates to a concrete aave_v2_* event via
  // instance.aaveV2Subkind — see isAaveV3Kind for the same pattern.
  if (kind === 'aave_v2') return true;
  return AAVE_V2_KIND_TO_EVENT[kind] !== undefined || AAVE_V2_NET_KIND_TO_EVENTS[kind] !== undefined;
}

/** AAVE V4 chart kinds — ETH-only currently (V4 launched mainnet-only).
 *  5 events (no flashloan in V4). Same Lending-page layout pattern as V2/V3. */
export const AAVE_V4_CHART_KINDS: ChartKind[] = [
  'aave_v4_deposit',
  'aave_v4_withdraw',
  'aave_v4_net_deposit',
  'aave_v4_borrow',
  'aave_v4_repay',
  'aave_v4_net_borrow',
  'aave_v4_liquidation'
];
export const AAVE_V4_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  aave_v4_deposit: 'deposit',
  aave_v4_withdraw: 'withdraw',
  aave_v4_borrow: 'borrow',
  aave_v4_repay: 'repay',
  aave_v4_liquidation: 'liquidation'
};
export const AAVE_V4_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  aave_v4_net_deposit: ['deposit', 'withdraw'],
  aave_v4_net_borrow: ['borrow', 'repay']
};
export function isAaveV4Kind(kind: ChartKind): boolean {
  // The 'aave_v4' wrapper kind delegates to a concrete aave_v4_* event via
  // instance.aaveV4Subkind — see isAaveV3Kind for the same pattern.
  if (kind === 'aave_v4') return true;
  return AAVE_V4_KIND_TO_EVENT[kind] !== undefined || AAVE_V4_NET_KIND_TO_EVENTS[kind] !== undefined;
}

/** Morpho chart kinds. 7 single-event + 3 net = 10 kinds. Morpho Blue's
 *  isolated-market architecture surfaces separate supply (lending) vs
 *  supply_collateral (collateral posting) events — the Net Supply chart
 *  reads supplies vs withdrawals; Net Collateral reads supply_collateral
 *  vs withdraw_collateral. ETH + BASE only. */
export const MORPHO_CHART_KINDS: ChartKind[] = [
  'morpho_supply',
  'morpho_withdraw',
  'morpho_net_supply',
  'morpho_borrow',
  'morpho_repay',
  'morpho_net_borrow',
  'morpho_supply_collateral',
  'morpho_withdraw_collateral',
  'morpho_net_collateral',
  'morpho_liquidation'
];
export const MORPHO_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  morpho_supply: 'supply',
  morpho_withdraw: 'withdraw',
  morpho_borrow: 'borrow',
  morpho_repay: 'repay',
  morpho_supply_collateral: 'supply_collateral',
  morpho_withdraw_collateral: 'withdraw_collateral',
  morpho_liquidation: 'liquidation'
};
export const MORPHO_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  morpho_net_supply: ['supply', 'withdraw'],
  morpho_net_borrow: ['borrow', 'repay'],
  morpho_net_collateral: ['supply_collateral', 'withdraw_collateral']
};
export function isMorphoKind(kind: ChartKind): boolean {
  // The general 'morpho' wrapper kind delegates to a concrete morpho_* event
  // via instance.morphoSubkind — every Morpho routing branch (data fetch,
  // chain selector, value-mode toggle, render branch) treats it as a Morpho
  // chart and reads the subkind through the effective-kind derived value.
  if (kind === 'morpho') return true;
  return MORPHO_KIND_TO_EVENT[kind] !== undefined || MORPHO_NET_KIND_TO_EVENTS[kind] !== undefined;
}

/** Spark chart kinds (AAVE V3 fork by Sky/Maker, ETH-only).
 *  Same 6-event + 2-net taxonomy as AAVE V3 minus the eth_market axis. */
export const SPARK_CHART_KINDS: ChartKind[] = [
  'spark_deposit',
  'spark_withdraw',
  'spark_net_deposit',
  'spark_borrow',
  'spark_repay',
  'spark_net_borrow',
  'spark_flashloan',
  'spark_liquidation'
];
export const SPARK_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  spark_deposit: 'deposit',
  spark_withdraw: 'withdraw',
  spark_borrow: 'borrow',
  spark_repay: 'repay',
  spark_flashloan: 'flashloan',
  spark_liquidation: 'liquidation'
};
export const SPARK_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  spark_net_deposit: ['deposit', 'withdraw'],
  spark_net_borrow: ['borrow', 'repay']
};
export function isSparkKind(kind: ChartKind): boolean {
  // The general 'spark' wrapper kind delegates to a concrete spark_* event
  // via instance.sparkSubkind — every Spark routing branch (data fetch,
  // chain pin, value-mode toggle, render branch) treats it as a Spark
  // chart and reads the subkind through the effective-kind derived value.
  if (kind === 'spark') return true;
  return SPARK_KIND_TO_EVENT[kind] !== undefined || SPARK_NET_KIND_TO_EVENTS[kind] !== undefined;
}

/** GMX V2 chart kinds (perp DEX, ARB-only). Filter dimension is per-market
 *  (`market_name` like "BTC/USD [WBTC-USDC]") — same selector model as
 *  Uniswap pools. Per-event value field is picked deliberately because the
 *  server returns `swap.amount_in` and `withdrawals.value_usd` in raw
 *  uint256 units (decoder bug, similar to the Morpho case before its fix). */
export const GMX_V2_CHART_KINDS: ChartKind[] = [
  'gmx_v2_position_increase',
  'gmx_v2_position_decrease',
  'gmx_v2_net_position',
  'gmx_v2_liquidation',
  'gmx_v2_swap',
  'gmx_v2_deposit',
  'gmx_v2_withdraw',
  'gmx_v2_net_lp'
];
export const GMX_V2_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  gmx_v2_position_increase: 'position_increase',
  gmx_v2_position_decrease: 'position_decrease',
  gmx_v2_liquidation: 'liquidation',
  gmx_v2_swap: 'swap',
  gmx_v2_deposit: 'deposit',
  gmx_v2_withdraw: 'withdraw'
};
export const GMX_V2_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  gmx_v2_net_position: ['position_increase', 'position_decrease'],
  gmx_v2_net_lp: ['deposit', 'withdraw']
};
/** True for any GMX V2 kind (single-event, net, or the general wrapper).
 *  The 'gmx_v2' wrapper delegates to a concrete gmx_v2_* subkind via
 *  instance.gmxV2Subkind. */
export function isGmxV2Kind(kind: ChartKind): boolean {
  if (kind === 'gmx_v2') return true;
  return GMX_V2_KIND_TO_EVENT[kind] !== undefined || GMX_V2_NET_KIND_TO_EVENTS[kind] !== undefined;
}
/** Per-kind value-field picker. Each GMX V2 kind defaults to either
 *  sum_amount (size_delta_usd / token-units) or sum_value_usd — chosen
 *  for whichever is the cleanest unit for that event class on the V1
 *  dashboard. The choice takes precedence over the instance.valueMode
 *  toggle. */
export const GMX_V2_PRIMARY_FIELD: Partial<Record<ChartKind, 'sum_amount' | 'sum_value_usd'>> = {
  // size_delta_usd (USD notional) — the real "position size" number
  gmx_v2_position_increase: 'sum_amount',
  gmx_v2_position_decrease: 'sum_amount',
  gmx_v2_net_position: 'sum_amount',
  gmx_v2_liquidation: 'sum_amount',
  // value_usd is correct here; amount_in is broken upstream
  gmx_v2_swap: 'sum_value_usd',
  // long+short token-units — symmetric across deposit/withdraw so net_lp
  // subtracts apples-to-apples (deposit.value_usd is fine but
  // withdraw.value_usd is broken upstream, so we use token-units everywhere
  // in this family)
  gmx_v2_deposit: 'sum_amount',
  gmx_v2_withdraw: 'sum_amount',
  gmx_v2_net_lp: 'sum_amount'
};

/** Hyperliquid chart kinds (perp DEX, on-chain — every event carries a
 *  wallet identity, enabling per-trader and whale-tracking analyses).
 *  Per-token filter via the same token selector as binance. Per-wallet
 *  filter exposed on every kind via a wallet input + a category dropdown
 *  sourced from the tradernick.wallet_labels CH dictionary. */
export const HL_CHART_KINDS: ChartKind[] = [
  // hl_ohlcv removed — superseded by the generic `ohlcv` kind with
  // exchange='hl'. Same goes for hl_funding_paid → `fr` + exchange='hl'.
  'hl_pnl',
  'hl_unrealized_pnl',
  'hl_transfers',
  'hl_vault_net',
  'hl_top_vaults',
  'hl_top_vault_lps',
  'hl_vault_detail',
  'hl_top_traders',
  'hl_top_positions',
  'hl_smart_oi'
];
/** Single-event HL kinds → server-side event slug. */
export const HL_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  // hl_ohlcv intentionally absent — handled via the generic `ohlcv` kind.
  // hl_funding_paid intentionally absent — handled via `fr` + exchange='hl'.
  hl_pnl: 'trade_history',
  hl_unrealized_pnl: 'position_history',
  // hl_transfers intentionally absent — uses /hyperliquid/bridge_flows for
  // a directional deposit/withdrawal/net three-line view.
  // hl_vault_net intentionally absent — uses /hyperliquid/vault_flow for
  // the same 3-line shape (deposit/withdraw/net) over vault data.
  // hl_top_vaults / hl_top_vault_lps / hl_vault_detail intentionally
  // absent — each uses its own dedicated endpoint.
  // hl_top_traders has no single event — uses the leaderboard endpoint
};
export function isHlKind(kind: ChartKind): boolean {
  return kind.startsWith('hl_');
}
/** Per-kind value-field picker. value_usd for events where the server
 *  computes one; sum_amount otherwise. hl_unrealized_pnl is absent — it
 *  uses its own endpoint and row shape (long_pnl/short_pnl/net_pnl). */
export const HL_PRIMARY_FIELD: Partial<Record<ChartKind, 'sum_amount' | 'sum_value_usd'>> = {
  hl_pnl: 'sum_value_usd'          // realized PnL in USD
  // hl_transfers / hl_vault_net / hl_top_vaults / hl_top_vault_lps /
  // hl_vault_detail: each uses its own dedicated endpoint, not the
  // generic /hyperliquid/aggregate one.
};

/** Uniswap V3 chart kinds exposed in the in-chart subkind picker.
 *  NOTE: 'uniswap_v3_collect' is intentionally omitted — same rationale
 *  as AERO_CL_CHART_KINDS (Collect events mix principal + fees and
 *  can't be split cleanly from events alone once a Collect crosses
 *  days from its Burn). The underlying ChartKind + KIND_TO_EVENT
 *  entries stay so saved layouts that reference 'uniswap_v3_collect'
 *  continue to load. */
export const UNISWAP_V3_CHART_KINDS: ChartKind[] = [
  'uniswap_v3_swap',
  'uniswap_v3_deposit',
  'uniswap_v3_withdraw',
  'uniswap_v3_net_liquidity',
  'uniswap_v3_net_swap_flow'
];

/** Map from a single-event Uniswap V3 kind → the data_server event slug. */
export const UNISWAP_V3_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  uniswap_v3_swap: 'swap',
  uniswap_v3_deposit: 'deposit',
  uniswap_v3_withdraw: 'withdraw',
  uniswap_v3_collect: 'collect'
};

/** Net Uniswap V3 kinds — net_liquidity fetches two endpoints (deposit +
 *  withdraw); net_swap_flow uses the swap endpoint's directional split
 *  via sum_value_usd_t0t1 − sum_value_usd_t1t0 (no second fetch). */
export const UNISWAP_V3_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  uniswap_v3_net_liquidity: ['deposit', 'withdraw']
};

/** True for any Uniswap V3 kind (single-event, net-liquidity, net-swap-flow,
 *  or the general wrapper). The 'uniswap_v3' wrapper delegates to a concrete
 *  uniswap_v3_* subkind via instance.uniswapV3Subkind. */
export function isUniswapV3Kind(kind: ChartKind): boolean {
  if (kind === 'uniswap_v3') return true;
  return (
    UNISWAP_V3_KIND_TO_EVENT[kind] !== undefined ||
    UNISWAP_V3_NET_KIND_TO_EVENTS[kind] !== undefined ||
    kind === 'uniswap_v3_net_swap_flow'
  );
}

/** Uniswap V2 chart kinds. No collect (V2 auto-compounds fees), no
 *  net_swap_flow (V2 swap rows lack the directional USD split V3 has). */
export const UNISWAP_V2_CHART_KINDS: ChartKind[] = [
  'uniswap_v2_swap',
  'uniswap_v2_deposit',
  'uniswap_v2_withdraw',
  'uniswap_v2_net_liquidity'
];
export const UNISWAP_V2_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  uniswap_v2_swap: 'swap',
  uniswap_v2_deposit: 'deposit',
  uniswap_v2_withdraw: 'withdraw'
};
export const UNISWAP_V2_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  uniswap_v2_net_liquidity: ['deposit', 'withdraw']
};
export function isUniswapV2Kind(kind: ChartKind): boolean {
  // The 'uniswap_v2' wrapper kind delegates to a concrete uniswap_v2_*
  // subkind via instance.uniswapV2Subkind.
  if (kind === 'uniswap_v2') return true;
  return (
    UNISWAP_V2_KIND_TO_EVENT[kind] !== undefined ||
    UNISWAP_V2_NET_KIND_TO_EVENTS[kind] !== undefined
  );
}

/** Uniswap V4 chart kinds exposed in the in-chart subkind picker. V4 LP
 *  events lack amount0/amount1 — only liquidity_delta — so Amount mode
 *  on deposit/withdraw isn't meaningful (the data_server returns 0 for
 *  sum_amount0/1 on those events).
 *
 *  NOTE: 'uniswap_v4_initialize' is intentionally omitted. Pool init is
 *  a one-shot event that fires at pool deployment and never again, so
 *  filtered by a specific pool tuple (sym0, sym1, fee, ts, hooks) it's
 *  guaranteed-empty for every window after creation — chart-of-zeros
 *  by design. The underlying ChartKind + KIND_TO_EVENT entry stays so
 *  saved layouts that reference it continue to load. */
export const UNISWAP_V4_CHART_KINDS: ChartKind[] = [
  'uniswap_v4_swap',
  'uniswap_v4_deposit',
  'uniswap_v4_withdraw',
  'uniswap_v4_net_liquidity'
];
export const UNISWAP_V4_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  uniswap_v4_swap: 'swap',
  uniswap_v4_deposit: 'deposit',
  uniswap_v4_withdraw: 'withdraw',
  uniswap_v4_initialize: 'initialize'
};
export const UNISWAP_V4_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  uniswap_v4_net_liquidity: ['deposit', 'withdraw']
};
export function isUniswapV4Kind(kind: ChartKind): boolean {
  // The 'uniswap_v4' wrapper kind delegates to a concrete uniswap_v4_*
  // subkind via instance.uniswapV4Subkind.
  if (kind === 'uniswap_v4') return true;
  return (
    UNISWAP_V4_KIND_TO_EVENT[kind] !== undefined ||
    UNISWAP_V4_NET_KIND_TO_EVENTS[kind] !== undefined
  );
}

/** V4 pool identity needs fee + tick_spacing + hooks alongside the
 *  symbol pair. We extend UniPool conceptually but model the extra
 *  fields as a separate optional shape on ChartInstance to avoid
 *  breaking the V3 selector code. */
export type UniV4Pool = {
  symbol0: string;
  symbol1: string;
  fee: number;
  tick_spacing: number;
  hooks: string;
};

/** Aerodrome CL (concentrated-pool) chart kinds exposed in the in-chart
 *  subkind picker. BASE chain only. NOTE: 'aero_cl_collect' is
 *  intentionally omitted — the raw Collect aggregate mixes principal
 *  (returned after Burn) and fees, and there's no event-only way to
 *  split them once a Collect crosses days from its Burn (tokensOwed is
 *  a persistent state slot, not derivable from events alone). The
 *  underlying ChartKind + KIND_TO_EVENT entries stay so saved layouts
 *  that reference 'aero_cl_collect' continue to load. */
export const AERO_CL_CHART_KINDS: ChartKind[] = [
  'aero_cl_swap',
  'aero_cl_deposit',
  'aero_cl_withdraw',
  'aero_cl_net_liquidity'
];
export const AERO_CL_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  aero_cl_swap: 'swap',
  aero_cl_deposit: 'deposit',
  aero_cl_withdraw: 'withdraw',
  aero_cl_collect: 'collect'
};
export const AERO_CL_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  aero_cl_net_liquidity: ['deposit', 'withdraw']
};
/** True for any Aerodrome CL kind (single-event, net, or the general
 *  wrapper). The 'aero_cl' wrapper delegates to a concrete aero_cl_*
 *  subkind via instance.aeroClSubkind. */
export function isAeroClKind(kind: ChartKind): boolean {
  if (kind === 'aero_cl') return true;
  return (
    AERO_CL_KIND_TO_EVENT[kind] !== undefined ||
    AERO_CL_NET_KIND_TO_EVENTS[kind] !== undefined
  );
}

/** Aero (concentrated) pool identity: (chain=BASE, sym0, sym1, tick_spacing). */
export type AeroPool = {
  symbol0: string;
  symbol1: string;
  tick_spacing: number;
};

/** Aerodrome BASIC pool chart kinds (Solidly v1, BASE only). Pool identity
 *  is (sym0, sym1, stable) — no fee tier, no tick spacing. 4 events: swap,
 *  deposit, withdraw, claim (basic-only — concentrated uses collect). */
export const AERO_BASIC_CHART_KINDS: ChartKind[] = [
  'aero_basic_swap',
  'aero_basic_deposit',
  'aero_basic_withdraw',
  'aero_basic_claim',
  'aero_basic_net_liquidity'
];
export const AERO_BASIC_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  aero_basic_swap: 'swap',
  aero_basic_deposit: 'deposit',
  aero_basic_withdraw: 'withdraw',
  aero_basic_claim: 'claim'
};
export const AERO_BASIC_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  aero_basic_net_liquidity: ['deposit', 'withdraw']
};
/** True for any Aerodrome Basic kind (single-event, net, or the general
 *  wrapper). The 'aero_basic' wrapper delegates to a concrete aero_basic_*
 *  subkind via instance.aeroBasicSubkind. */
export function isAeroBasicKind(kind: ChartKind): boolean {
  if (kind === 'aero_basic') return true;
  return (
    AERO_BASIC_KIND_TO_EVENT[kind] !== undefined ||
    AERO_BASIC_NET_KIND_TO_EVENTS[kind] !== undefined
  );
}
export type AeroBasicPool = {
  symbol0: string;
  symbol1: string;
  stable: boolean;
};

/** Group label used when bucketing chart kinds in the Insert menu. The
 *  group level lets us collapse the 18+ event-driven kinds into a single
 *  parent row per protocol (AAVE V3 / Uniswap V4 / Aerodrome / …). Single-
 *  family kinds (OHLCV, Token Flow, …) return null and render flat at the
 *  top of the menu. */
export function chartKindGroup(kind: ChartKind): string | null {
  if (kind.startsWith('aave_v2_')) return 'AAVE V2';
  if (kind.startsWith('aave_v3_')) return 'AAVE V3';
  if (kind.startsWith('aave_v4_')) return 'AAVE V4';
  if (kind.startsWith('morpho_')) return 'Morpho';
  if (kind.startsWith('spark_')) return 'Spark';
  if (kind.startsWith('uniswap_v2_')) return 'Uniswap V2';
  if (kind.startsWith('uniswap_v3_')) return 'Uniswap V3';
  if (kind.startsWith('uniswap_v4_')) return 'Uniswap V4';
  if (kind.startsWith('lido_')) return 'Lido';
  if (kind.startsWith('aero_basic_')) return 'Aerodrome Basic';
  if (kind.startsWith('aero_cl_')) return 'Aerodrome CL';
  if (kind.startsWith('gmx_v2_')) return 'GMX';
  if (kind.startsWith('hl_')) return 'Hyperliquid';
  return null;
}

/** High-level Insert-menu category used by the Dashboard page. Splits every
 *  surfaceable chart kind into one of 6 user-facing buckets — Exchange /
 *  Flows / Lending / DeX / Perp / Staking — instead of the protocol-family
 *  grouping the per-category pages use. Returns null for kinds we don't
 *  want to surface from the Dashboard picker. */
export type ChartCategory = 'Exchange' | 'Flows' | 'Lending' | 'DeX' | 'Perp' | 'Staking';

export const CHART_CATEGORIES: ChartCategory[] = [
  'Exchange', 'Flows', 'Lending', 'DeX', 'Perp', 'Staking'
];

export function chartKindCategory(kind: ChartKind): ChartCategory | null {
  // Exchange — Binance OHLCV + derivatives. The chart's in-built exchange
  // selector lets the user flip these to Hyperliquid in place, so we list
  // them once under Exchange rather than duplicating under Perp.
  if (kind === 'ohlcv' || kind === 'price' || kind === 'price_ratio' || kind === 'pc' || kind === 'oi' || kind === 'vol_oi' || kind === 'volume' || kind === 'fr'
      || kind === 'book_depth'
      || kind === 'bs' || kind === 'sz' || kind === 'tt' || kind === 'ls'
      || kind === 'token_leaderboard') {
    return 'Exchange';
  }
  // Flows — on-chain token transfers + exchange-flow wrapper.
  if (kind === 'transfer' || kind === 'exchange_flow') return 'Flows';
  // Lending — AAVE V2/V3/V4, Morpho, Spark wrappers (sub-events live in
  // each wrapper's in-chart event picker), plus the per-protocol top-wallet
  // leaderboard tableviews.
  if (kind === 'aave_v2' || kind === 'aave_v3' || kind === 'aave_v4'
      || kind === 'morpho' || kind === 'spark'
      || kind === 'aave_v2_top_wallets' || kind === 'aave_v3_top_wallets' || kind === 'aave_v4_top_wallets') {
    return 'Lending';
  }
  // DeX — Uniswap V2/V3/V4, Aerodrome CL + Basic wrappers, plus per-protocol
  // top-wallet leaderboard tableviews.
  if (kind === 'uniswap_v2' || kind === 'uniswap_v3' || kind === 'uniswap_v4'
      || kind === 'aero_cl' || kind === 'aero_basic'
      || kind === 'uniswap_v2_top_wallets' || kind === 'uniswap_v3_top_wallets' || kind === 'uniswap_v4_top_wallets') {
    return 'DeX';
  }
  // Perp — GMX V2 + Hyperliquid family. smart_wallets_table is an HL-only
  // experimental tableview (no hl_ prefix so it stays out of the many
  // isHlKind() chart-control branches) — categorise it here explicitly.
  if (kind === 'gmx_v2' || isHlKind(kind) || kind === 'smart_wallets_table') return 'Perp';
  // Staking — Lido (and future Stader/Frax).
  if (kind === 'lido') return 'Staking';
  return null;
}

/** Sub-grouping inside a Dashboard category: collapses multiple wrapper
 *  kinds that share a provider into one expandable parent ("AAVE" instead
 *  of three siblings AAVE V2 / V3 / V4). Returns null for kinds that
 *  should stay flat in their category.
 *
 *  `variant` is the leaf label shown under the provider header. We pick a
 *  short form (V2/V3/V4 for AAVE/Uniswap, CL/Basic for Aerodrome) rather
 *  than reusing CHART_KIND_LABELS so the leaf row reads cleanly under the
 *  provider header instead of repeating the provider name. */
export function chartKindProvider(kind: ChartKind): { provider: string; variant: string } | null {
  if (kind === 'aave_v2') return { provider: 'AAVE',      variant: 'V2' };
  if (kind === 'aave_v3') return { provider: 'AAVE',      variant: 'V3' };
  if (kind === 'aave_v4') return { provider: 'AAVE',      variant: 'V4' };
  if (kind === 'aave_v2_top_wallets') return { provider: 'AAVE', variant: 'V2 Top Wallets' };
  if (kind === 'aave_v3_top_wallets') return { provider: 'AAVE', variant: 'V3 Top Wallets' };
  if (kind === 'aave_v4_top_wallets') return { provider: 'AAVE', variant: 'V4 Top Wallets' };
  if (kind === 'uniswap_v2') return { provider: 'Uniswap', variant: 'V2' };
  if (kind === 'uniswap_v3') return { provider: 'Uniswap', variant: 'V3' };
  if (kind === 'uniswap_v4') return { provider: 'Uniswap', variant: 'V4' };
  if (kind === 'uniswap_v2_top_wallets') return { provider: 'Uniswap', variant: 'V2 Top Wallets' };
  if (kind === 'uniswap_v3_top_wallets') return { provider: 'Uniswap', variant: 'V3 Top Wallets' };
  if (kind === 'uniswap_v4_top_wallets') return { provider: 'Uniswap', variant: 'V4 Top Wallets' };
  if (kind === 'aero_cl')    return { provider: 'Aerodrome', variant: 'CL' };
  if (kind === 'aero_basic') return { provider: 'Aerodrome', variant: 'Basic' };
  // Hyperliquid: every hl_* kind nests under one Hyperliquid sub-menu.
  // Variant = the CHART_KIND_LABELS entry minus the "HL " prefix so the
  // leaf reads as "Realized PnL" / "Unrealized PnL" / "Bridge Flows" / …
  // under the Hyperliquid header instead of repeating "HL" each time.
  if (isHlKind(kind)) {
    const full = CHART_KIND_LABELS[kind] ?? kind;
    const variant = full.startsWith('HL ') ? full.slice(3) : full;
    return { provider: 'Hyperliquid', variant };
  }
  return null;
}

/** Trim the protocol prefix off a chart-kind label so it reads naturally
 *  under a grouped parent in the Insert menu. E.g. "AAVE V3 Deposits" →
 *  "Deposits" when shown under the "AAVE V3" group header. */
export function chartKindShortLabel(kind: ChartKind): string {
  const full = CHART_KIND_LABELS[kind] ?? kind;
  const group = chartKindGroup(kind);
  if (group && full.startsWith(group + ' ')) {
    return full.slice(group.length + 1);
  }
  return full;
}

/** Stable order applied when rendering grouped chart kinds in the Insert
 *  menu — version-ascending within each protocol family (V2 → V3 → V4),
 *  protocols in adoption order overall. Pages can compose their
 *  `availableKinds` in any sequence and the menu still renders the same
 *  visual order. Unknown groups fall back to alphabetic. */
const _GROUP_ORDER: Record<string, number> = {
  'AAVE V2':    10,
  'AAVE V3':    11,
  'AAVE V4':    12,
  'Spark':      13,
  'Morpho':     14,
  'Lido':       20,
  'Uniswap V2': 30,
  'Uniswap V3': 31,
  'Uniswap V4': 32,
  'Aerodrome CL':    40,
  'Aerodrome Basic': 41,
  'GMX':             50,
  'Hyperliquid':     60
};
export function chartKindGroupOrder(group: string): number {
  return _GROUP_ORDER[group] ?? 99;
}

/** Lido chart kinds (Staking page default layout order). 3 mainnet events
 *  + Net Stake (deposits − claims) + 2 L2 events + Net L2 (bridge in/out). */
export const LIDO_CHART_KINDS: ChartKind[] = [
  'lido_deposit',
  'lido_withdrawal_request',
  'lido_withdrawal_claimed',
  'lido_net_stake',
  'lido_net_request_stake',
  'lido_request_pending',
  'lido_l2_deposit',
  'lido_l2_withdrawal_request',
  'lido_l2_net'
];

/** Single-event Lido kinds → DeFiStream-side event slug. */
export const LIDO_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  lido_deposit: 'deposit',
  lido_withdrawal_request: 'withdrawal_request',
  lido_withdrawal_claimed: 'withdrawal_claimed',
  lido_l2_deposit: 'l2_deposit',
  lido_l2_withdrawal_request: 'l2_withdrawal_request'
};

/** Net Lido kinds — two parallel fetches, client subtracts.
 *    Net Stake         = deposit − withdrawal_claimed  (net stETH actually
 *                        leaving the system: claims unstake; the request-
 *                        only stage is committed but not yet redeemable)
 *    Net Request Stake = deposit − withdrawal_request  (net stETH growth
 *                        if the queue eventually clears — useful for
 *                        forward-looking sentiment, since requests precede
 *                        claims by the validator unstake window)
 *    Net L2            = l2_deposit − l2_withdrawal_request  (net wstETH
 *                        bridged in to L2) */
export const LIDO_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  lido_net_stake: ['deposit', 'withdrawal_claimed'],
  lido_net_request_stake: ['deposit', 'withdrawal_request'],
  // Pending queue depth: requests that have been made but not yet
  // finalised. Positive value = unstake queue is growing (requests
  // arriving faster than claims clear). Goes negative when a batch of
  // claims settles old requests in a window with few new requests.
  lido_request_pending: ['withdrawal_request', 'withdrawal_claimed'],
  lido_l2_net: ['l2_deposit', 'l2_withdrawal_request']
};

/** Lido kinds that run on mainnet (ETH-only chain selector). */
export const LIDO_L1_KINDS = new Set<ChartKind>([
  'lido_deposit',
  'lido_withdrawal_request',
  'lido_withdrawal_claimed',
  'lido_net_stake',
  'lido_net_request_stake',
  'lido_request_pending'
]);

/** True for any Lido kind (single-event, net, or the general wrapper).
 *  The 'lido' wrapper delegates to a concrete lido_* subkind via
 *  instance.lidoSubkind; routing branches read through effective-kind. */
export function isLidoKind(kind: ChartKind): boolean {
  if (kind === 'lido') return true;
  return (
    LIDO_KIND_TO_EVENT[kind] !== undefined ||
    LIDO_NET_KIND_TO_EVENTS[kind] !== undefined
  );
}

/** Uniswap V3 pool identity carried on each Uniswap chart instance. The
 *  three fields together uniquely identify a pool within a chain — the
 *  chain itself is stored separately in `ChartInstance.chain`. */
export type UniPool = {
  symbol0: string;
  symbol1: string;
  fee: number;
};

/** Format a pool for menus/headers: "WETH/USDC 0.05%". */
export function fmtUniPool(p: UniPool): string {
  return `${p.symbol0}/${p.symbol1} ${(p.fee / 10000).toFixed(2)}%`;
}

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
  // _in  = at least one of the listed categories present on that side (hasAny)
  // _ex  = none of the listed categories present on that side
  // _all_in = every listed category present on that side (hasAll) — used to
  //          intersect umbrellas like 'Deposit' with 'CEX' so perp bridges
  //          (Deposit + Perp, no CEX) get excluded from CeX-flow templates.
  sender_in?: string[];
  sender_ex?: string[];
  sender_all_in?: string[];
  receiver_in?: string[];
  receiver_ex?: string[];
  receiver_all_in?: string[];
  involving_in?: string[];
  involving_ex?: string[];
  involving_all_in?: string[];
  // Wallet *entity* filters (single nullable string per wallet, e.g. "Binance").
  sender_entity_in?: string[];
  sender_entity_ex?: string[];
  receiver_entity_in?: string[];
  receiver_entity_ex?: string[];
  involving_entity_in?: string[];
  involving_entity_ex?: string[];
  // Exact *address* filters. Case-insensitive for EVM (server lowercases
  // any 0x-prefixed address before matching), case-sensitive for BTC / TRON.
  sender_addr_in?: string[];
  sender_addr_ex?: string[];
  receiver_addr_in?: string[];
  receiver_addr_ex?: string[];
  involving_addr_in?: string[];
  involving_addr_ex?: string[];
};

// ── smart_wallets_table (experimental smart-wallet finder) ─────────────────
// One bespoke tableview kind whose internal "metric" selector swaps the extra
// (right-most) column and the server-side ranking. Backed by
// /api/hyperliquid/smart_wallet_metrics. Designed to grow: add a metric here +
// a backend branch and the toolbar/table pick it up.
export type SmartWalletMetric = 'sharpe';
export type SmartWalletLookback = 1 | 7 | 30 | 90;
export const SMART_WALLET_LOOKBACKS: ReadonlyArray<SmartWalletLookback> = [1, 7, 30, 90];

export type SmartWalletMetricDef = {
  key: SmartWalletMetric;
  /** Short selector + column-header label. */
  label: string;
  /** Column-header tooltip / longer description. */
  desc: string;
  /** How to render the value: 'ratio' = signed 2-dp number; 'usd' = $; */
  format: 'ratio' | 'usd';
};

export const SMART_WALLET_METRICS: ReadonlyArray<SmartWalletMetricDef> = [
  {
    key: 'sharpe',
    label: 'Sharpe',
    desc: 'mean(daily total PnL) / std(daily total PnL) over active days in the window. '
      + 'Daily total PnL = Δrealized + Δunrealized. Not annualized, not capital-normalized.',
    format: 'ratio'
  }
];

export function smartWalletMetricDef(key: SmartWalletMetric | undefined): SmartWalletMetricDef {
  return SMART_WALLET_METRICS.find((m) => m.key === key) ?? SMART_WALLET_METRICS[0];
}

// Width + height are grid-column / grid-row spans. Both axes accept 1–4
// so the user can drag any chart to anything from 1×1 (compact) up to
// 4×4 (full-width tall). The CSS grid has 4 columns at lg breakpoint,
// so 4 spans the entire row; at narrower breakpoints CSS clips to the
// available column count.
export type ChartWidth = 1 | 2 | 3 | 4;
export type ChartHeight = 1 | 2 | 3 | 4;

export type ChartInstance = {
  id: string;
  kind: ChartKind;
  width: ChartWidth;
  height: ChartHeight;
  token: string;
  interval: Interval;
  showPoint: boolean;
  /** When true, the chart overlays narrow dotted vertical lines at the
   *  start of each Saturday and Monday (UTC) inside the visible window.
   *  Helps line up weekly cycles across charts. Off by default. */
  showWeekLines?: boolean;
  /** When true, this chart is EXCLUDED from the shared zoom/pan sync — it
   *  zooms/pans independently and neither follows nor drives the other charts.
   *  Only matters when the global zoom-sync is on. Off by default. */
  noSync?: boolean;
  mas: MAConfig[]; // length MAX_MAS, each slot independently enabled
  /** When true, the chart plots a running cumulative sum of the same
   *  source the MAs operate on, on a secondary axis. Useful for reading
   *  "total deposits over the visible window" / "TVL increase" off
   *  event-driven kinds. Off by default. Only honoured on kinds where
   *  per-bucket values are summable (transfer / AAVE / Morpho / Spark /
   *  Lido / Uniswap-USD / Aerodrome); ignored elsewhere. */
  showSum?: boolean;
  /** Rolling sum window in buckets. 0 (or missing) = strict running total
   *  from the first loaded row (legacy default). Positive N = sliding
   *  window over the last N buckets only. Honoured by every canSum kind. */
  sumWindow?: number;
  // sz only
  under?: number;
  over?: number;
  underInput?: string;
  overInput?: string;
  // ohlcv only
  pin?: boolean;
  /** ohlcv only: which exchange's candle table to read. Defaults to
   *  'binance'. 'hl' routes to tradernick.hl_ohlcv_1m so the same chart
   *  kind serves both data sources. */
  exchange?: 'binance' | 'hl';
  /** ohlcv only: how the volume sub-pane is denominated. 'token' (default)
   *  plots raw asset units; 'usd' plots sum(per-1m volume × per-1m close),
   *  which is comparable across assets and matches what most traders mean
   *  by "volume". The toggle re-skins the existing volume bars — no refetch. */
  volumeUnit?: 'token' | 'usd';
  /** fr only: how to display the funding rate. 'rate8h' (default) normalizes
   *  HL's per-hour rate × 8 so both exchanges show comparable per-8h bps —
   *  matches the Coinglass convention. 'apr' annualizes to percent-per-year. */
  frDisplay?: 'rate8h' | 'apr';
  /** book_depth only: which visualization to render. 'totals' (default) plots
   *  bid vs ask USD; 'per_level_imbalance' draws one (bid - ask) / (bid + ask)
   *  signed-percentage line per percentage band (6 bands → 6 series); 'imbalance' plots the
   *  whole-book (bid - ask) / (bid + ask) as a single signed bar; 'stacked' is a
   *  stacked-band chart of every level. 'asks_share' / 'bids_share' /
   *  'total_share' are 100%-stacked views: each band as a % of all asks, all
   *  bids, or the whole book (bid+ask per band over total) respectively.
   *  'asks_bids_share' stacks the asks-share (0–100%) on top of the bids-share
   *  (0–100%) in one column — axis 0–200%, mid at 100%. All modes share the
   *  same `/book_depth` response — the chart pivots client-side. */
  bookDepthMode?:
    | 'totals'
    | 'per_level_imbalance'
    | 'imbalance'
    | 'stacked'
    | 'asks_share'
    | 'bids_share'
    | 'total_share'
    | 'asks_bids_share';
  /** book_depth 'per_level_imbalance' mode only: which bands are visible, by
   *  their bid-side suffix ('m020','m100','m200','m300','m400','m500').
   *  undefined ⇒ all six on (default). Lets the settings panel select /
   *  deselect individual band series without a refetch. */
  bookDepthBands?: string[];
  /** For event-driven chart kinds (AAVE / Lido) that emit both a USD value
   *  AND a raw token amount per row: which one to plot. Default 'usd'. The
   *  toggle is hidden for Uniswap kinds because the amount field mixes
   *  token0+token1 (or amount_sold across t0/t1) and has no clean unit. */
  valueMode?: 'usd' | 'amount';
  // pc (Price Comparison) only — list of tokens to compare alongside
  // `instance.token`. Each one is fetched via /api/ohlcv and added as a
  // rebased % line. The primary `instance.token` is itself one of the
  // lines, anchored at the leftmost data point of its own series.
  overlayTokens?: string[];
  // transfer / aave / uniswap — every event-stream kind that selects a chain
  chain?: string;
  // uniswap_* only: the pool (symbol0/symbol1/fee) on the selected chain
  uniPool?: UniPool;
  // uniswap_v4_* only: V4 adds tick_spacing + hooks to the pool tuple
  uniV4Pool?: UniV4Pool;
  // aero_* only: Aerodrome concentrated-pool tuple (BASE chain implied)
  aeroPool?: AeroPool;
  // aero_basic_* only: Aerodrome basic-pool tuple — (sym0, sym1, stable)
  aeroBasicPool?: AeroBasicPool;
  /** gmx_* only: human-readable market_name (e.g. "BTC/USD [WBTC-USDC]").
   *  Empty string = "all markets summed". The dashboard populates the
   *  per-chart selector from /api/gmx/streams. */
  gmxMarket?: string;
  /** hl_* only: optional EVM wallet address filter. Lowercased before the
   *  server-side dictionary lookup. Empty = no wallet filter (sum across
   *  every trader). */
  hlWallet?: string;
  /** hl_* only: optional wallet-label filter (e.g. 'CEX', 'Smart-Money',
   *  'Bridge'). When set, only rows whose wallet is tagged with that
   *  category in the tradernick.wallet_labels dictionary are aggregated.
   *  Mutually exclusive with hlWallet (the wallet filter takes precedence
   *  on the server). */
  hlWalletCategory?: string;
  /** hl_top_positions only: which wallet from the top-N list is currently
   *  being viewed. Empty = default to the rank-1 wallet. Persists across
   *  page reloads via the layout sanitize step. */
  hlSelectedWallet?: string;
  /** exchange_flow only: which exchange the in/out filters target.
   *  - 'binance' | 'coinbase' | 'okx' | 'bybit': CeX-style filters
   *    (deposit umbrella receiver / hot-wallet sender), per-chain.
   *  - 'hyperliquid': Perp-style filters; chain is forced to ARB.
   *  - 'combined': sum every exchange's flow for the selected token/chain;
   *    exchanges that don't support that token/chain contribute 0. */
  exchangeFlowExchange?: 'binance' | 'coinbase' | 'okx' | 'bybit' | 'hyperliquid' | 'combined';
  /** exchange_flow only: which series to plot.
   *  - 'inflow' / 'outflow': single line of that direction.
   *  - 'netflow': single line, computed client-side = inflow - outflow.
   *  - 'in_out': two lines (inflow green + outflow red), no net.
   *  - 'all': three lines (inflow green, outflow red, netflow cyan). */
  exchangeFlowType?: 'inflow' | 'outflow' | 'netflow' | 'in_out' | 'all';
  /** hl_top_vaults only: ranking metric for the leaderboard. */
  hlVaultSortBy?: 'net' | 'deposits' | 'withdrawals' | 'commission';
  /** hl_vault_detail only: which vault from the top-N list is currently
   *  being viewed. Empty = default to the rank-1 vault. */
  hlSelectedVault?: string;
  /** oi chart only, HL exchange only: which side(s) of OI to render —
   *  'total' (default, matches the Binance behavior), 'long', 'short',
   *  'long_short' (two lines: long + short, no total), 'long_to_short'
   *  (one line: long / short ratio), 'net_pct' (unitless skew), or
   *  'net' (long - short in the same unit as oiUnit). Ignored when
   *  exchange='binance' (the long/short split isn't available there). */
  oiHlDisplay?: 'total' | 'long' | 'short' | 'long_short' | 'long_to_short' | 'net_pct' | 'net';
  /** OI unit: 'usd' = dollar notional (default, matches Binance OI panel
   *  conventions), 'token' = the underlying coin amount (e.g. BTC count).
   *  The Long/Short ratio mode ignores this — it's mathematically the same
   *  in either unit, since longs and shorts mark at the same price. */
  oiUnit?: 'usd' | 'token';
  /** bs (Taker Buyer vs Seller) only: what to render — 'stacked' (default, the
   *  buyer+seller $ stacked bars), 'ratio' (a single Buyer/Seller line, ~1 =
   *  balanced), 'both' (bars + the ratio line on a secondary axis), or 'pct'
   *  (two lines: % Buyer and % Seller of total taker volume). */
  bsDisplay?: 'stacked' | 'ratio' | 'both' | 'pct';
  /** hl_smart_oi only: ids of the saved wallet filters this chart draws —
   *  one OI series group per filter. Filters live in the filters store
   *  (localStorage) and are inline-expanded into the `filter=` param at
   *  fetch time. See $lib/components/charts/filters. */
  filterIds?: string[];
  /** @deprecated hl_smart_oi legacy inline selector. Charts now reference
   *  saved filters via `filterIds`; this is read once on load and migrated
   *  into an auto-created saved filter, then dropped. */
  smartSelector?: import('./smartSelector').SmartSelectorState;
  /** hl_smart_oi only: when true, overlay a secondary-axis line showing
   *  the number of wallets that passed the selector each day. Lets the
   *  user spot over-filtering (counts hitting 0 or hovering far below
   *  the top_n cap) without leaving the chart. */
  smartShowWalletCount?: boolean;
  /** ls / tt charts only: which ratio series to display. 'all' (or unset) =
   *  every series (the default); otherwise the single series key to show
   *  (LS: 'all_ct' | 'taker_vol'; TT: 'top_ct' | 'top_vol' | 'top_avg_vol').
   *  Moving-average overlays are filtered to match the selected series. */
  seriesFilter?: string;
  /** Optional wallet-category filter applied to the transfer chart's main
   *  series. When set, the chart replaces its unfiltered sum with the filtered
   *  one (MAs computed from the filtered values too). */
  filter?: TransferFilters;
  /** Two filter sets fetched in parallel and subtracted on the client:
   *  `positive - negative` per bucket. Used by netflow-style templates
   *  (e.g. CeX Netflow = CeX Inflow − CeX Outflow). Mutually exclusive
   *  with `filter` — templates set one or the other, never both. */
  netFilter?: { positive: TransferFilters; negative: TransferFilters };
  /** General-Morpho wrapper only (kind === 'morpho'): which concrete
   *  Morpho event behavior the chart currently shows. The user picks via
   *  the in-chart selector; switching busts the cache (subkind is folded
   *  into the load key) and re-fires the morpho_aggregate fetch with the
   *  new event. Ignored for any other chart kind. */
  morphoSubkind?: ChartKind;
  /** General-Spark wrapper only (kind === 'spark'): which concrete Spark
   *  event behavior the chart currently shows. Same mechanism as
   *  morphoSubkind. Ignored for any other chart kind. */
  sparkSubkind?: ChartKind;
  /** General-AAVE-V3 wrapper only (kind === 'aave_v3'): which concrete
   *  aave_v3_* event behavior the chart currently shows. Same mechanism
   *  as morphoSubkind. Ignored for any other chart kind. */
  aaveV3Subkind?: ChartKind;
  /** General-AAVE-V2 wrapper only (kind === 'aave_v2'): which concrete
   *  aave_v2_* event the chart currently shows. */
  aaveV2Subkind?: ChartKind;
  /** General-AAVE-V4 wrapper only (kind === 'aave_v4'): which concrete
   *  aave_v4_* event the chart currently shows. */
  aaveV4Subkind?: ChartKind;
  /** General-Uniswap-V3 wrapper only (kind === 'uniswap_v3'): which concrete
   *  uniswap_v3_* event behavior the chart currently shows. */
  uniswapV3Subkind?: ChartKind;
  /** General-Uniswap-V2 wrapper only (kind === 'uniswap_v2'): which concrete
   *  uniswap_v2_* event the chart currently shows. */
  uniswapV2Subkind?: ChartKind;
  /** General-Uniswap-V4 wrapper only (kind === 'uniswap_v4'): which concrete
   *  uniswap_v4_* event the chart currently shows. */
  uniswapV4Subkind?: ChartKind;
  /** General-Aerodrome-CL wrapper only (kind === 'aero_cl'): which concrete
   *  aero_cl_* event the chart currently shows. */
  aeroClSubkind?: ChartKind;
  /** General-Aerodrome-Basic wrapper only (kind === 'aero_basic'): which
   *  concrete aero_basic_* event the chart currently shows. */
  aeroBasicSubkind?: ChartKind;
  /** General-Lido wrapper only (kind === 'lido'): which concrete lido_*
   *  event the chart currently shows. Switching between an L1 and an L2
   *  subkind also flips the chain selector — see ChartInstance.svelte. */
  lidoSubkind?: ChartKind;
  /** General-GMX-V2 wrapper only (kind === 'gmx_v2'): which concrete
   *  gmx_v2_* event the chart currently shows. */
  gmxV2Subkind?: ChartKind;
  /** GMX V2 position / liquidation kinds: which side(s) to render —
   *  'long' / 'short' / 'total' (Long + Short) / 'net' (Long − Short)
   *  / 'all' (the four lines together). Default 'total' so the chart
   *  matches the original single-line behavior. */
  gmxLongShortDisplay?: 'long' | 'short' | 'total' | 'net' | 'all';
  /** HL Realized PnL — which side(s) of realized PnL to plot.
   *  'total' (default) keeps the original single net line sourced from
   *  hl_trade_history. The other modes switch to /hyperliquid/realized_pnl_split
   *  which sums hl_fills.closed_pnl bucketed by the position direction. */
  hlPnlSide?: 'total' | 'long' | 'short' | 'both';
  /** Top-wallets leaderboard kinds (aave_v2_top_wallets / aave_v3_top_wallets
   *  / aave_v4_top_wallets): which metric column ranks the rows. The table
   *  always renders every metric column — this only changes the server-side
   *  ORDER BY (so the *set* of top-N wallets shifts when you flip it). */
  leaderboardMetric?: LeaderboardMetric;
  /** Top-wallets leaderboard kinds: how many rows to return. Default 10. */
  leaderboardTopN?: number;
  /** smart_wallets_table only: which ranking metric is the extra column AND
   *  the server-side sort that defines the top-N candidate set. Only 'sharpe'
   *  for now (simple mean/std of daily total PnL — see SMART_WALLET_METRICS). */
  swMetric?: SmartWalletMetric;
  /** smart_wallets_table only: window length in days ending at the snapshot. */
  swLookback?: SmartWalletLookback;
  /** smart_wallets_table only: token to scope every column to. null/undefined
   *  or '' = global (all tokens), which reads the fast wallet-daily rollups. */
  swToken?: string | null;
  /** smart_wallets_table only: ISO date (YYYY-MM-DD) ending the window. The
   *  day slider sets this; default = start of the current UTC day. */
  swSnapshot?: string;
  /** smart_wallets_table only: noise guard — minimum active (trade) days in
   *  the window for a wallet to enter the ranking. Configurable; default 3. */
  swMinDays?: number;
  /** smart_wallets_table only: noise guard — minimum window volume (USD).
   *  Configurable; default 100000. */
  swMinVolume?: number;
  /** smart_wallets_table only: minimum window realized PnL (USD) for a wallet
   *  to enter the ranking. Configurable; default 0 (profitable only). */
  swMinRealized?: number;
  /** smart_wallets_table only: minimum open interest (USD, as of the snapshot)
   *  for a wallet to enter the ranking. Configurable; default 0. */
  swMinOi?: number;
  /** If set, this chart was inserted from a template. The filter is treated as
   *  locked (no Apply/Clear UI), and the panel title uses this name instead of
   *  the generic kind label. Token / chain / interval / MAs remain editable. */
  templateName?: string;
  /** Compound-chart overlays — extra series from other chart kinds layered
   *  on the primary axis. Each overlay carries its own kind + config (token,
   *  chain, pool, …) and a chosen `seriesKey`. The overlay's data is fetched
   *  at the primary chart's interval, range-matched to the primary's visible
   *  Y range, and drawn as one additional line. Empty / undefined = no
   *  overlays (the chart renders unchanged). */
  overlays?: ChartOverlay[];
};

/** One overlay series layered onto a host ChartInstance. The fields mirror
 *  the host ChartInstance config — only the dimensions the overlay's kind
 *  actually uses get populated. Persisted alongside the host in localStorage. */
export type ChartOverlay = {
  id: string;
  /** The other chart kind whose data we want to overlay. Must be in
   *  `overlayableKinds()` — i.e. produces a time series (not a table) and
   *  isn't `pc` (PC is itself an overlay-on-OHLCV view). */
  kind: ChartKind;
  /** Which named series (line) from the overlay's data to plot. For kinds
   *  that emit one inherent series this is the canonical key (e.g.
   *  'sum_value_usd'); for multi-series kinds (BS, SZ, exchange_flow,
   *  hl_transfers, …) the user picks it from a sub-dropdown. See
   *  OVERLAY_KIND_SERIES for the per-kind list. */
  seriesKey: string;
  /** Auto-assigned from OVERLAY_COLORS. */
  color: string;
  /** When set, the overlay's values are passed through maArray() before
   *  drawing — only the MA line shows. To see raw + MA together the user
   *  adds the overlay twice (one raw, one MA). Mutually exclusive with
   *  `sum` — sanitizeOverlay drops the loser if both are present. */
  ma?: { type: MAType; length: number };
  /** When set, the overlay's values are replaced by a windowed running
   *  sum before drawing — only the Σ line shows. `length` is the sliding
   *  window in buckets (must be ≥ 2). Mutually exclusive with `ma`. */
  sum?: { length: number };
  /** True = chip stays in the header but the line is not drawn. Toggled
   *  by clicking the coloured dot inside the chip. Persisted, so the
   *  hide-then-reload-then-show pattern works as expected. */
  hidden?: boolean;
  /** Per-kind config fields, mirroring the host ChartInstance shape but
   *  carrying only the dimensions the chosen `kind` reads. Validated
   *  through `sanitizeOverlay()`. */
  token?: string;
  /** Server-side compound-token group name (e.g. "USDC+USDT", "Stables").
   *  Mutually exclusive with `token` — when set, overlay-fetch sends
   *  `token_group=` instead of `token=` so the server expands the bundle. */
  tokenGroup?: string;
  /** Second token used as the denominator on price_ratio overlays only.
   *  Defaults to the host chart's token (see initDefaultsForKind). Ignored
   *  for every other overlay kind. */
  tokenDenominator?: string;
  chain?: string;
  /** Server-side compound-chain group name (e.g. "EVM"). Mutually exclusive
   *  with `chain` — when set, overlay-fetch sends `chain_group=` instead of
   *  `chain=` so the server expands the bundle. */
  chainGroup?: string;
  exchange?: 'binance' | 'hl';
  frDisplay?: 'rate8h' | 'apr';
  valueMode?: 'usd' | 'amount';
  under?: number;
  over?: number;
  uniPool?: UniPool;
  uniV4Pool?: UniV4Pool;
  aeroPool?: AeroPool;
  aeroBasicPool?: AeroBasicPool;
  gmxMarket?: string;
  hlWallet?: string;
  hlWalletCategory?: string;
  exchangeFlowExchange?: 'binance' | 'coinbase' | 'okx' | 'bybit' | 'hyperliquid';
  /** hl_smart_oi overlay only: full wallet-selection state, carried
   *  per-overlay so each overlay can pick its own leaderboard shape
   *  (e.g. a chart can overlay a 7-day Top 50 PnL% leaderboard AND a
   *  30-day Top 10 Sharpe leaderboard simultaneously). Mirrors the
   *  `selector` JSON the backend expects. */
  smartSelector?: import('./smartSelector').SmartSelectorState;
};

/** One addable series exposed by a chart kind. Single-series kinds list one
 *  entry; multi-series kinds enumerate each user-pickable line. `key` is
 *  what the overlay-fetch helper reads off each row to project the value. */
export type OverlaySeriesDef = {
  key: string;
  label: string;
};

/** Per-kind catalogue of addable overlay series. Empty `[]` means the kind
 *  cannot be overlaid (TableViews + PC). */
export const OVERLAY_KIND_SERIES: Partial<Record<ChartKind, OverlaySeriesDef[]>> = {
  // Exchange — OHLCV exposes each candle field; the rest are single-line
  // overlays except for the multi-line HL OI / L/S / TT / BS / SZ kinds.
  ohlcv: [
    { key: 'close',  label: 'Close' },
    { key: 'open',   label: 'Open' },
    { key: 'high',   label: 'High' },
    { key: 'low',    label: 'Low' },
    { key: 'volume', label: 'Volume' }
  ],
  // Price — the one-click "just the close line" overlay. OHLCV with
  // close also works but most users reach for "Price" by name. Single-
  // entry catalogue so the dialog hides the series sub-picker.
  price: [
    { key: 'close', label: 'Close' }
  ],
  // Price Ratio — numerator_close / denominator_close per bucket. Same
  // exchange selector as Price. Numerator defaults to the host chart's
  // token (matching the Price overlay's defaulting rule); the dialog
  // adds a second token picker for the denominator, which defaults to
  // the host's token. Single-entry catalogue → no series sub-picker.
  price_ratio: [
    { key: 'ratio', label: 'Ratio' }
  ],
  oi: [
    // The user picks long/short/total + unit (USD/token) at add-time. For
    // binance OI only the 'total' series is meaningful; the overlay-fetch
    // helper falls back to the total series when long/short slots are
    // unavailable. `long_to_short_oi` is HL-only and unitless (the dialog
    // locks the exchange to HL on pick).
    { key: 'total_oi_value',   label: 'Total OI ($)' },
    { key: 'long_oi_value',    label: 'Long OI ($)' },
    { key: 'short_oi_value',   label: 'Short OI ($)' },
    { key: 'net_oi_value',     label: 'Net OI ($)' },
    { key: 'total_oi',         label: 'Total OI (token)' },
    { key: 'long_oi',          label: 'Long OI (token)' },
    { key: 'short_oi',         label: 'Short OI (token)' },
    { key: 'net_oi',           label: 'Net OI (token)' },
    { key: 'net_oi_pct',       label: 'Net OI %' },
    { key: 'long_to_short_oi', label: 'Long / Short OI' }
  ],
  // Volume / OI turnover ratio overlay. Side selector picks which leg of
  // OI sits in the denominator (total / long / short / net), with both
  // Volume and OI in USD — the ratio is unitless but USD is the
  // canonical numerator/denominator (cross-token comparable). Reads
  // directly as "fraction of OI that turned over in this bucket":
  // 0.5 = half of OI traded, 2.0 = traded twice. HL-only side selections
  // (long/short/net) lock the exchange to HL — Binance has no L/S split.
  vol_oi: [
    { key: 'total_oi_value',   label: 'Vol / Total OI' },
    { key: 'long_oi_value',    label: 'Vol / Long OI' },
    { key: 'short_oi_value',   label: 'Vol / Short OI' },
    { key: 'net_oi_value',     label: 'Vol / Net OI' }
  ],
  // hl_smart_oi response shape is identical to /oi_split (same long/short/
  // total in token + USD), so the overlay-fetch projection reuses the
  // same field-key mapping. The leaderboard knobs ride as overlay-level
  // config fields (smartPnlLookbackDays etc.) so each overlay can carry
  // its own leaderboard shape independent of the host chart.
  hl_smart_oi: [
    { key: 'total_oi_value',   label: 'Smart Total OI ($)' },
    { key: 'long_oi_value',    label: 'Smart Long OI ($)' },
    { key: 'short_oi_value',   label: 'Smart Short OI ($)' },
    { key: 'net_oi_value',     label: 'Smart Net OI ($)' },
    { key: 'total_oi',         label: 'Smart Total OI (token)' },
    { key: 'long_oi',          label: 'Smart Long OI (token)' },
    { key: 'short_oi',         label: 'Smart Short OI (token)' },
    { key: 'net_oi',           label: 'Smart Net OI (token)' },
    { key: 'net_oi_pct',       label: 'Smart Net OI %' },
    { key: 'long_to_short_oi', label: 'Smart Long / Short OI' }
  ],
  // Traded volume per bucket from {exchange}_ohlcv_1m, projected straight off
  // the candle's volume_usd / volume fields (same /api/ohlcv overlay path as
  // ohlcv/price). Exchange + token pickers come from the dialog like OHLCV.
  volume: [
    { key: 'volume_usd', label: 'Volume ($)' },
    { key: 'volume',     label: 'Volume (token)' }
  ],
  fr: [ { key: 'rate_bps', label: 'Funding Rate' } ],
  bs: [
    { key: 'buyer_taker_usd',  label: 'Buyer Taker $' },
    { key: 'seller_taker_usd', label: 'Seller Taker $' }
  ],
  sz: [
    { key: 'small_usd', label: 'Small Trades $' },
    { key: 'mid_usd',   label: 'Mid Trades $' },
    { key: 'large_usd', label: 'Large Trades $' }
  ],
  tt: [
    { key: 'top_trader_count_ratio', label: 'Top Trader Count L/S' },
    { key: 'top_trader_vol_ratio',   label: 'Top Trader Volume L/S' }
  ],
  ls: [
    { key: 'long_short_count_ratio',     label: 'All Count L/S' },
    { key: 'taker_long_short_vol_ratio', label: 'Taker Volume L/S' }
  ],
  // Flows
  transfer: [
    { key: 'sum_value_usd', label: 'Value (USD)' },
    { key: 'sum_amount',    label: 'Amount (token)' }
  ],
  exchange_flow: [
    { key: 'inflow',  label: 'Inflow' },
    { key: 'outflow', label: 'Outflow' },
    { key: 'netflow', label: 'Net flow' }
  ],
  // HL multi-line specials
  hl_transfers: [
    { key: 'deposit',    label: 'Bridge Deposit' },
    { key: 'withdrawal', label: 'Bridge Withdraw' },
    { key: 'net',        label: 'Net Bridge Flow' }
  ],
  hl_vault_net: [
    { key: 'deposit',  label: 'Vault Deposit' },
    { key: 'withdraw', label: 'Vault Withdraw' },
    { key: 'net',      label: 'Net Vault Flow' }
  ],
  hl_unrealized_pnl: [
    { key: 'long_pnl',  label: 'Long PnL' },
    { key: 'short_pnl', label: 'Short PnL' },
    { key: 'net_pnl',   label: 'Net PnL' }
  ],
  // PC is excluded from the picker — empty list signals "not overlayable".
  pc: []
};

/** Default single-series entry for every event-driven kind. Filled at module
 *  init so we don't have to keep this in sync with the ChartKind union by
 *  hand. */
function _populateDefaultOverlaySeries() {
  const VALUE_LABEL = 'Value';
  const valueOnly: OverlaySeriesDef[] = [{ key: 'sum_value_usd', label: VALUE_LABEL }];
  const valueOrAmount: OverlaySeriesDef[] = [
    { key: 'sum_value_usd', label: 'Value (USD)' },
    { key: 'sum_amount',    label: 'Amount (token)' }
  ];
  const concreteValueOrAmount: ChartKind[] = [
    // AAVE V2/V3/V4 concrete
    ...AAVE_V2_CHART_KINDS, ...AAVE_V3_CHART_KINDS, ...AAVE_V4_CHART_KINDS,
    // Morpho / Spark
    ...MORPHO_CHART_KINDS, ...SPARK_CHART_KINDS,
    // GMX (sum_amount is USD-denominated for position kinds; pretend
    // it's "value" for the overlay picker)
    ...GMX_V2_CHART_KINDS,
    // Lido
    ...LIDO_CHART_KINDS,
    // Uniswap V2/V3/V4 + Aerodrome
    ...UNISWAP_V2_CHART_KINDS, ...UNISWAP_V3_CHART_KINDS, ...UNISWAP_V4_CHART_KINDS,
    ...AERO_CL_CHART_KINDS, ...AERO_BASIC_CHART_KINDS,
    // HL realized PnL
    'hl_pnl'
  ];
  for (const k of concreteValueOrAmount) {
    if (!OVERLAY_KIND_SERIES[k]) OVERLAY_KIND_SERIES[k] = valueOrAmount;
  }
  // Wrapper kinds are not picker entries (we expose concrete kinds instead).
  // Table kinds are explicitly empty so they get filtered out.
  const tabular: ChartKind[] = [
    'hl_top_vaults','hl_top_vault_lps','hl_vault_detail',
    'hl_top_traders','hl_top_positions','token_leaderboard','smart_wallets_table'
  ];
  for (const k of tabular) OVERLAY_KIND_SERIES[k] = [];
  void valueOnly;
}
_populateDefaultOverlaySeries();

/** Every kind that can be added as a compound-chart overlay (non-empty
 *  series list). Used as the picker dialog's source list. */
export function overlayableKinds(): ChartKind[] {
  const out: ChartKind[] = [];
  for (const k of Object.keys(OVERLAY_KIND_SERIES) as ChartKind[]) {
    const arr = OVERLAY_KIND_SERIES[k];
    if (arr && arr.length > 0) out.push(k);
  }
  return out;
}

/** Auto-assign palette for overlay lines. Curated for hue separation
 *  from the host chart's typical primary palette (greens / reds / cyans /
 *  ambers / purples used by OI, buyer/seller, OHLCV, etc.). nextOverlayColor
 *  picks by perceptual distance, not list order, so adding a colour close
 *  to a host primary doesn't strand future overlays in that hue. */
export const OVERLAY_COLORS = [
  '#f472b6',  // hot pink
  '#fde047',  // bright yellow
  '#38bdf8',  // sky blue
  '#fb7185',  // rose
  '#a3e635',  // lime
  '#c084fc',  // light purple
  '#fb923c',  // orange
  '#5eead4',  // mint
];

/** Convert "#rrggbb" → [h, s, l] in [0,360), [0,1], [0,1]. Tolerant of
 *  short "#rgb" and bad input (returns mid-grey). */
function hexToHsl(hex: string): [number, number, number] {
  if (typeof hex !== 'string') return [0, 0, 0.5];
  let h = hex.trim();
  if (h.startsWith('#')) h = h.slice(1);
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  if (h.length !== 6 || /[^0-9a-fA-F]/.test(h)) return [0, 0, 0.5];
  const r = parseInt(h.slice(0, 2), 16) / 255;
  const g = parseInt(h.slice(2, 4), 16) / 255;
  const b = parseInt(h.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let hh = 0;
  if (max === r) hh = ((g - b) / d + (g < b ? 6 : 0));
  else if (max === g) hh = (b - r) / d + 2;
  else hh = (r - g) / d + 4;
  return [hh * 60, s, l];
}

/** Perceptual-ish distance between two hex colours. Weighted hue
 *  (cyclic), saturation, lightness. Greys are treated as far from
 *  saturated colours via the saturation term. Returns ≥ 0. */
function colorDistance(a: string, b: string): number {
  const [ah, as_, al] = hexToHsl(a);
  const [bh, bs, bl] = hexToHsl(b);
  let dh = Math.abs(ah - bh);
  if (dh > 180) dh = 360 - dh;
  // When either colour is near-grey, hue is meaningless — fall back to
  // lightness + saturation distance only so we don't think grey is
  // "close to red" just because grey's hue defaults to 0.
  const minSat = Math.min(as_, bs);
  const hueWeight = minSat;
  return Math.sqrt(
    (dh / 180) * (dh / 180) * 3 * hueWeight
    + (as_ - bs) * (as_ - bs)
    + (al - bl) * (al - bl) * 2
  );
}

/** Pick the OVERLAY_COLORS entry with the largest minimum distance to
 *  the set of colours already in use on this chart. `used` is the existing
 *  overlay palette; `avoid` is the host chart's primary palette (so a new
 *  overlay won't blend into the line the user is comparing against).
 *  Both lists default to empty for backward compat with callers that
 *  don't yet supply them. Falls back to round-robin only when every
 *  palette entry exactly matches something to avoid. */
export function nextOverlayColor(used: string[], avoid: string[] = []): string {
  const blocked = new Set<string>();
  for (const c of [...used, ...avoid]) {
    if (typeof c === 'string' && c.length > 0) blocked.add(c.toLowerCase());
  }
  // Fast path: prefer untaken palette entries with maximum min-distance
  // to all blocked colours.
  const candidates = OVERLAY_COLORS.filter((c) => !blocked.has(c.toLowerCase()));
  const pool = candidates.length > 0 ? candidates : OVERLAY_COLORS.slice();
  if (blocked.size === 0) return pool[0];
  let best = pool[0];
  let bestScore = -1;
  for (const c of pool) {
    let minD = Infinity;
    for (const b of blocked) {
      const d = colorDistance(c, b);
      if (d < minD) minD = d;
    }
    if (minD > bestScore) {
      bestScore = minD;
      best = c;
    }
  }
  return best;
}

/** Validate one overlay against its kind's expectations. Returns a cleaned
 *  copy or null if the input is malformed beyond rescue. Always called from
 *  the layout sanitize() so corrupt persistence can't strand a chart. */
export function sanitizeOverlay(raw: unknown): ChartOverlay | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== 'string' || r.id.length === 0) return null;
  const kind = r.kind;
  if (typeof kind !== 'string') return null;
  if (!OVERLAY_KIND_SERIES[kind as ChartKind] || OVERLAY_KIND_SERIES[kind as ChartKind]!.length === 0) return null;
  const seriesList = OVERLAY_KIND_SERIES[kind as ChartKind]!;
  const seriesKey = typeof r.seriesKey === 'string' && seriesList.some(s => s.key === r.seriesKey)
    ? r.seriesKey
    : seriesList[0].key;
  const color = typeof r.color === 'string' && r.color.length > 0 ? r.color : OVERLAY_COLORS[0];
  const o: ChartOverlay = { id: r.id, kind: kind as ChartKind, seriesKey, color };
  if (r.hidden === true) o.hidden = true;
  // MA — drop unless { type, length } are both valid.
  if (r.ma && typeof r.ma === 'object') {
    const m = r.ma as Record<string, unknown>;
    const t = m.type;
    const len = m.length;
    if ((t === 'sma' || t === 'ema' || t === 'wma') && typeof len === 'number' && len >= 2 && len <= 500) {
      o.ma = { type: t, length: Math.floor(len) };
    }
  }
  // SUM — windowed running sum; mutually exclusive with `ma`. If both
  // got persisted somehow, prefer the MA (older feature) and drop sum.
  if (r.sum && typeof r.sum === 'object' && !o.ma) {
    const s = r.sum as Record<string, unknown>;
    const len = s.length;
    if (typeof len === 'number' && len >= 2 && len <= 5000) {
      o.sum = { length: Math.floor(len) };
    }
  }
  // Per-kind config fields. Copy through whatever the kind actually reads.
  if (typeof r.token === 'string') o.token = r.token;
  if (typeof r.tokenGroup === 'string' && r.tokenGroup.length > 0) o.tokenGroup = r.tokenGroup;
  if (typeof r.tokenDenominator === 'string' && r.tokenDenominator.length > 0) o.tokenDenominator = r.tokenDenominator;
  if (typeof r.chain === 'string') o.chain = r.chain;
  if (typeof r.chainGroup === 'string' && r.chainGroup.length > 0) o.chainGroup = r.chainGroup;
  if (r.exchange === 'binance' || r.exchange === 'hl') o.exchange = r.exchange;
  if (r.frDisplay === 'rate8h' || r.frDisplay === 'apr') o.frDisplay = r.frDisplay;
  if (r.valueMode === 'usd' || r.valueMode === 'amount') o.valueMode = r.valueMode;
  if (typeof r.under === 'number') o.under = r.under;
  if (typeof r.over === 'number') o.over = r.over;
  if (typeof r.gmxMarket === 'string') o.gmxMarket = r.gmxMarket;
  if (typeof r.hlWallet === 'string') o.hlWallet = r.hlWallet;
  if (typeof r.hlWalletCategory === 'string') o.hlWalletCategory = r.hlWalletCategory;
  if (typeof r.exchangeFlowExchange === 'string'
      && ['binance','coinbase','okx','bybit','hyperliquid'].includes(r.exchangeFlowExchange)) {
    o.exchangeFlowExchange = r.exchangeFlowExchange as ChartOverlay['exchangeFlowExchange'];
  }
  // Smart-wallet selector — legacy smartPnl* fields are dropped on load
  // (hard cut). sanitizeSmartSelectorState handles missing / invalid
  // shapes by substituting defaults. (Overlays keep their own inline
  // selector; chart instances use saved-filter references — see
  // DynamicChartLayout's per-instance hydration.)
  if (r.smartSelector !== undefined) {
    o.smartSelector = sanitizeSmartSelectorState(r.smartSelector);
  }
  const rp = r.uniPool as Record<string, unknown> | undefined;
  if (rp && typeof rp.symbol0 === 'string' && typeof rp.symbol1 === 'string' && typeof rp.fee === 'number') {
    o.uniPool = { symbol0: rp.symbol0.toUpperCase(), symbol1: rp.symbol1.toUpperCase(), fee: rp.fee };
  }
  const rp4 = r.uniV4Pool as Record<string, unknown> | undefined;
  if (rp4 && typeof rp4.symbol0 === 'string' && typeof rp4.symbol1 === 'string'
      && typeof rp4.fee === 'number' && typeof rp4.tick_spacing === 'number'
      && typeof rp4.hooks === 'string') {
    o.uniV4Pool = {
      symbol0: rp4.symbol0.toUpperCase(),
      symbol1: rp4.symbol1.toUpperCase(),
      fee: rp4.fee,
      tick_spacing: rp4.tick_spacing,
      hooks: rp4.hooks
    };
  }
  const rpa = r.aeroPool as Record<string, unknown> | undefined;
  if (rpa && typeof rpa.symbol0 === 'string' && typeof rpa.symbol1 === 'string'
      && typeof rpa.tick_spacing === 'number') {
    o.aeroPool = {
      symbol0: rpa.symbol0.toUpperCase(),
      symbol1: rpa.symbol1.toUpperCase(),
      tick_spacing: rpa.tick_spacing
    };
  }
  const rpab = r.aeroBasicPool as Record<string, unknown> | undefined;
  if (rpab && typeof rpab.symbol0 === 'string' && typeof rpab.symbol1 === 'string'
      && typeof rpab.stable === 'boolean') {
    o.aeroBasicPool = {
      symbol0: rpab.symbol0.toUpperCase(),
      symbol1: rpab.symbol1.toUpperCase(),
      stable: rpab.stable
    };
  }
  return o;
}

/** Short human-readable label for a chip in the chart header. Keeps the
 *  pieces the user picked: kind label + (where applicable) token + chain
 *  + pool + series. Trims to ~50 chars to fit. */
/** Short user-facing label for an exchange selector. Returned for kinds
 *  where the value materially changes which dataset is being read (the
 *  binance/HL toggle on OHLCV/OI/FR/BS/SZ/LS, and the CeX/HL exchange
 *  picker on exchange_flow). */
function overlayExchangeLabel(o: ChartOverlay): string | null {
  if (o.kind === 'exchange_flow') {
    const ex = o.exchangeFlowExchange;
    if (!ex) return null;
    return ex === 'hyperliquid' ? 'HL' : ex.charAt(0).toUpperCase() + ex.slice(1);
  }
  if (o.kind === 'ohlcv' || o.kind === 'price' || o.kind === 'price_ratio' || o.kind === 'oi' || o.kind === 'fr'
      || o.kind === 'bs' || o.kind === 'sz' || o.kind === 'ls') {
    const ex = o.exchange;
    if (!ex) return null;
    return ex === 'hl' ? 'HL' : 'Binance';
  }
  return null;
}

export function overlayChipLabel(o: ChartOverlay): string {
  const parts: string[] = [];
  parts.push(CHART_KIND_LABELS[o.kind] ?? o.kind);
  const ex = overlayExchangeLabel(o);
  if (ex) parts.push(ex);
  if (o.kind === 'price_ratio' && o.token && o.tokenDenominator) {
    // Two-token ratio chip: render as "BTC / USDC" so both legs are visible.
    parts.push(`${o.token} / ${o.tokenDenominator}`);
  } else if (o.tokenGroup) parts.push(`Σ ${o.tokenGroup}`);
  else if (o.token) parts.push(o.token);
  if (o.chainGroup) parts.push(`Σ ${o.chainGroup}`);
  else if (o.chain && o.chain !== 'HL') parts.push(o.chain);
  if (o.gmxMarket) parts.push(o.gmxMarket);
  if (o.uniPool) parts.push(fmtUniPool(o.uniPool));
  else if (o.aeroPool) parts.push(`${o.aeroPool.symbol0}/${o.aeroPool.symbol1} ts${o.aeroPool.tick_spacing}`);
  else if (o.aeroBasicPool) parts.push(`${o.aeroBasicPool.symbol0}/${o.aeroBasicPool.symbol1} ${o.aeroBasicPool.stable ? 'stable' : 'vAMM'}`);
  else if (o.uniV4Pool) parts.push(`${o.uniV4Pool.symbol0}/${o.uniV4Pool.symbol1} ${(o.uniV4Pool.fee/10000).toFixed(2)}%`);
  // Append the series suffix when the kind has multiple series.
  const list = OVERLAY_KIND_SERIES[o.kind] ?? [];
  if (list.length > 1) {
    const s = list.find(x => x.key === o.seriesKey);
    if (s) parts.push(s.label);
  }
  if (o.ma) parts.push(`${o.ma.type.toUpperCase()}${o.ma.length}`);
  else if (o.sum) parts.push(`Σ${o.sum.length}`);
  let label = parts.join(' · ');
  if (label.length > 64) label = label.slice(0, 61) + '…';
  return label;
}

/** Category bucket for an overlay-pickable kind. Wrapper kinds bucket via
 *  their own `chartKindCategory`; concrete kinds bucket via their group. */
export function overlayKindCategory(kind: ChartKind): ChartCategory | null {
  // Flat (non-grouped) kinds — same buckets as chartKindCategory().
  const direct = chartKindCategory(kind);
  if (direct) return direct;
  const grp = chartKindGroup(kind);
  if (!grp) return null;
  if (grp === 'AAVE V2' || grp === 'AAVE V3' || grp === 'AAVE V4' || grp === 'Morpho' || grp === 'Spark') return 'Lending';
  if (grp === 'Uniswap V2' || grp === 'Uniswap V3' || grp === 'Uniswap V4'
      || grp === 'Aerodrome CL' || grp === 'Aerodrome Basic') return 'DeX';
  if (grp === 'GMX' || grp === 'Hyperliquid') return 'Perp';
  if (grp === 'Lido') return 'Staking';
  return null;
}

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
    height: 1,
    token: defaults.token,
    interval: '4h',
    showPoint: true,
    mas: defaultMAs()
  };
  if (kind === 'sz') {
    base.under = 10000;
    base.over = 100000;
    base.underInput = '10000';
    base.overInput = '100000';
    base.exchange = 'binance';
  }
  if (kind === 'bs') {
    base.exchange = 'binance';
    base.bsDisplay = 'stacked';
  }
  if (kind === 'oi') {
    base.exchange = 'binance';
    base.oiHlDisplay = 'total';
    base.oiUnit = 'usd';
  }
  if (kind === 'volume') {
    // Traded volume per bucket from {exchange}_ohlcv_1m (volume + volume_usd).
    // USD notional by default; the toolbar flips to token amount via volumeUnit.
    base.exchange = 'binance';
    base.volumeUnit = 'usd';
  }
  if (kind === 'hl_smart_oi') {
    base.exchange = 'hl';
    base.oiHlDisplay = 'total';
    base.oiUnit = 'usd';
    // No filters by default — the user picks saved filters in chart settings
    // (or creates one on the Filters page). One OI series renders per filter.
    base.filterIds = [];
  }
  if (kind === 'ls') {
    base.exchange = 'binance';
  }
  if (kind === 'ohlcv') {
    base.pin = false;
    base.volumeUnit = 'token';
  }
  if (kind === 'fr') {
    base.frDisplay = 'rate8h';
  }
  if (kind === 'book_depth') {
    base.exchange = 'binance';
    base.bookDepthMode = 'totals';
  }
  if (kind === 'pc') {
    // Relative-price chart: the chart token is shown relative to each base
    // token (one price-ratio series per base). overlayTokens holds the base
    // tokens; default a single BTC base.
    base.overlayTokens = ['BTC'];
    base.exchange = 'binance';
  }
  if (isLeaderboardKind(kind)) {
    // Top-wallets leaderboards. Defaults vary by paramShape — AAVE keys by
    // (chain, token), Uniswap keys by pool. Each kind's config carries its
    // own default-metric so swap-first protocols (Uniswap) don't default
    // to AAVE's deposit ranking.
    const cfg = LEADERBOARD_KIND_CONFIG[kind]!;
    base.chain = defaults.chain ?? 'ETH';
    base.token = defaults.token ?? 'USDC';
    base.valueMode = 'usd';
    base.leaderboardMetric = cfg.defaultMetric;
    base.leaderboardTopN = 10;
    base.height = 3;
    base.width = 3;
    // Pool defaults for the Uniswap leaderboards — mirror the per-protocol
    // chart-kind defaults (USDC/WETH on ETH). V4 also needs fee/tick_spacing
    // /hooks; placeholder values get replaced by /uniswap_v4/streams once
    // available, same flow as the V4 chart kinds.
    if (cfg.paramShape === 'uniswap_v2') {
      base.uniPool = { symbol0: 'USDC', symbol1: 'WETH', fee: 0 };
    } else if (cfg.paramShape === 'uniswap_v3') {
      base.uniPool = { symbol0: 'USDC', symbol1: 'WETH', fee: 500 };
    } else if (cfg.paramShape === 'uniswap_v4') {
      base.uniV4Pool = {
        symbol0: 'USDC', symbol1: 'WETH', fee: 500, tick_spacing: 10,
        hooks: '0x0000000000000000000000000000000000000000'
      };
    }
  }
  if (isAaveV3Kind(kind)) {
    // AAVE V3 charts (single-event + net) behave like transfer charts —
    // keyed by (chain, token) — so we surface the same selectors.
    // Default eth_market is empty, which the data_server treats as
    // "all markets".
    base.chain = defaults.chain ?? 'ETH';
    base.valueMode = 'usd';
    if (kind === 'aave_v3') {
      // General wrapper — default to Deposits; the user flips via the
      // in-chart sub-kind selector. Subkind must be a concrete aave_v3_*
      // kind so AAVE_V3_KIND_TO_EVENT / AAVE_V3_NET_KIND_TO_EVENTS lookups
      // resolve through it.
      base.aaveV3Subkind = 'aave_v3_deposit';
    }
  }
  if (isAaveV2Kind(kind)) {
    // V2 only has two configured chains (ETH + POLYGON) — defaults to ETH.
    base.chain = defaults.chain ?? 'ETH';
    base.valueMode = 'usd';
    if (kind === 'aave_v2') {
      base.aaveV2Subkind = 'aave_v2_deposit';
    }
  }
  if (isAaveV4Kind(kind)) {
    // V4 is mainnet-only for now; default ETH.
    base.chain = 'ETH';
    base.valueMode = 'usd';
    if (kind === 'aave_v4') {
      base.aaveV4Subkind = 'aave_v4_deposit';
    }
  }
  if (isMorphoKind(kind)) {
    // Morpho is ETH + BASE.
    base.chain = defaults.chain ?? 'ETH';
    base.valueMode = 'usd';
    if (kind === 'morpho') {
      // General wrapper — default to Supplies; the user flips via the
      // in-chart sub-kind selector. Subkind must be a concrete morpho_*
      // kind so MORPHO_KIND_TO_EVENT / MORPHO_NET_KIND_TO_EVENTS lookups
      // resolve through it.
      base.morphoSubkind = 'morpho_supply';
    }
  }
  if (isSparkKind(kind)) {
    // Spark is ETH-only.
    base.chain = 'ETH';
    base.valueMode = 'usd';
    if (kind === 'spark') {
      // General wrapper — default to Deposits; the user flips via the
      // in-chart sub-kind selector. Subkind must be a concrete spark_*
      // kind so SPARK_KIND_TO_EVENT / SPARK_NET_KIND_TO_EVENTS lookups
      // resolve through it.
      base.sparkSubkind = 'spark_deposit';
    }
  }
  if (isHlKind(kind)) {
    // Hyperliquid: token roster matches binance INGEST_TOKENS. Static "HL"
    // chip in the selector (no chain dimension). Default to BTC; empty
    // wallet filter ("All wallets"). valueMode is overridden per-kind via
    // HL_PRIMARY_FIELD on the read side.
    base.chain = 'HL';
    base.token = defaults.token === 'USDC' ? 'BTC' : defaults.token;
    base.hlWallet = '';
    base.hlWalletCategory = '';
    base.valueMode = 'usd';
    if (kind === 'hl_top_positions') {
      // Default to "All tokens" — the chart's title-bar token select for
      // this kind has an "All" option mapped to empty string.
      base.token = '';
      base.hlSelectedWallet = '';
    }
    if (kind === 'hl_top_vaults') {
      base.hlVaultSortBy = 'net';
    }
    if (kind === 'hl_vault_detail') {
      base.hlSelectedVault = '';
    }
  }
  if (isGmxV2Kind(kind)) {
    // GMX V2 is ARB-only (server-side AVAX is "not configured" in 2.14).
    // Default market = canonical BTC/USD pool; the chart's selector lists
    // every market /api/gmx/streams returns. valueMode is overridden per
    // chart kind via GMX_V2_PRIMARY_FIELD — the Sum-/MA-style fetch picks
    // sum_amount or sum_value_usd directly off the response shape.
    base.chain = 'ARB';
    base.gmxMarket = 'BTC/USD [WBTC-USDC]';
    base.valueMode = 'usd';
    if (kind === 'gmx_v2') {
      // General wrapper — default to Position Open; the in-chart sub-kind
      // selector flips between the 8 concrete gmx_v2_* event behaviors.
      base.gmxV2Subkind = 'gmx_v2_position_increase';
    }
  }
  if (kind === 'transfer') {
    base.chain = defaults.chain ?? 'ETH';
    base.filter = {};
    base.valueMode = 'usd';
  }
  if (kind === 'exchange_flow') {
    base.chain = defaults.chain ?? 'ETH';
    base.token = defaults.token ?? 'USDC';
    base.valueMode = 'usd';
    base.exchangeFlowExchange = 'binance';
    base.exchangeFlowType = 'netflow';
  }
  if (isUniswapV3Kind(kind)) {
    base.chain = defaults.chain ?? 'ETH';
    // Conservative default: canonical USDC/WETH 0.05%. The page-level loader
    // will replace this with the first available pool from /uniswap/streams.
    base.uniPool = { symbol0: 'USDC', symbol1: 'WETH', fee: 500 };
    // Default USD for the headline series. Amount mode is per-chart and
    // not meaningful for net_swap_flow (see ChartInstance for the gate).
    base.valueMode = 'usd';
    if (kind === 'uniswap_v3') {
      // General wrapper — default to Swaps; the in-chart sub-kind selector
      // flips between Swaps / Deposits / Withdrawals / Collects / Net
      // Liquidity / Net Swap Flow. Subkind must be a concrete uniswap_v3_*
      // kind so UNISWAP_V3_KIND_TO_EVENT / UNISWAP_V3_NET_KIND_TO_EVENTS
      // lookups resolve through it.
      base.uniswapV3Subkind = 'uniswap_v3_swap';
    }
  }
  if (isUniswapV2Kind(kind)) {
    base.chain = defaults.chain ?? 'ETH';
    // V2 has no fee tier — reuse the uniPool shape with fee=0 as a sentinel.
    // ChartInstance treats fee=0 as "V2" when issuing requests so the
    // selector + fetch paths don't need a parallel shape.
    base.uniPool = { symbol0: 'USDC', symbol1: 'WETH', fee: 0 };
    base.valueMode = 'usd';
    if (kind === 'uniswap_v2') {
      base.uniswapV2Subkind = 'uniswap_v2_swap';
    }
  }
  if (isUniswapV4Kind(kind)) {
    base.chain = defaults.chain ?? 'ETH';
    // Canonical V4 pool: USDC/WETH 0.05% fee, tick_spacing=10, no hooks.
    base.uniV4Pool = {
      symbol0: 'USDC', symbol1: 'WETH', fee: 500, tick_spacing: 10,
      hooks: '0x0000000000000000000000000000000000000000'
    };
    base.valueMode = 'usd';
    if (kind === 'uniswap_v4') {
      base.uniswapV4Subkind = 'uniswap_v4_swap';
    }
  }
  if (isAeroClKind(kind)) {
    base.chain = 'BASE';
    // Default to USDC/WETH ts=100 (top Aero CL pool by volume).
    base.aeroPool = { symbol0: 'USDC', symbol1: 'WETH', tick_spacing: 100 };
    base.valueMode = 'usd';
    if (kind === 'aero_cl') {
      // General wrapper — default to Swaps; the in-chart sub-kind selector
      // flips between the 5 concrete aero_cl_* event behaviors.
      base.aeroClSubkind = 'aero_cl_swap';
    }
  }
  if (isAeroBasicKind(kind)) {
    base.chain = 'BASE';
    // Default to USDC/WETH vAMM (top basic pool by volume).
    base.aeroBasicPool = { symbol0: 'USDC', symbol1: 'WETH', stable: false };
    base.valueMode = 'usd';
    if (kind === 'aero_basic') {
      base.aeroBasicSubkind = 'aero_basic_swap';
    }
  }
  if (isLidoKind(kind)) {
    // Lido charts are chain-only (no token / pool axis). L1 kinds are
    // pinned to ETH by construction; L2 kinds default to ARB (highest
    // wstETH bridge volume), the user can flip via the chain dropdown.
    // The general 'lido' wrapper defaults to the lido_deposit (L1) subkind,
    // so its initial chain is ETH; switching to an L2 subkind via the
    // in-chart selector unpins the chain dropdown (see ChartInstance).
    const effectiveDefaultKind: ChartKind = kind === 'lido' ? 'lido_deposit' : kind;
    base.chain = LIDO_L1_KINDS.has(effectiveDefaultKind) ? 'ETH' : (defaults.chain ?? 'ARB');
    base.valueMode = 'usd';
    if (kind === 'lido') {
      base.lidoSubkind = 'lido_deposit';
    }
  }
  if (kind === 'smart_wallets_table') {
    // Experimental smart-wallet finder. Wide + tall so the table breathes.
    base.width = 3;
    base.height = 3;
    base.swMetric = 'sharpe';
    base.swLookback = 7;
    base.swToken = null;          // global (all tokens) by default
    base.swSnapshot = undefined;  // resolved to start-of-today at fetch time
    base.swMinDays = 3;
    base.swMinVolume = 100000;
    base.swMinRealized = 0;
    base.swMinOi = 0;
  }
  return base;
}
