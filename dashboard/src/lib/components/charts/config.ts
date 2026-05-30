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

export type ChartKind =
  | 'ohlcv'
  | 'oi'
  | 'fr'
  | 'bs'
  | 'sz'
  | 'tt'
  | 'ls'
  | 'transfer'
  | 'pc'
  | 'aave_deposit'
  | 'aave_withdraw'
  | 'aave_net_deposit'
  | 'aave_borrow'
  | 'aave_repay'
  | 'aave_net_borrow'
  | 'aave_flashloan'
  | 'aave_liquidation'
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
  | 'gmx_position_increase'
  | 'gmx_position_decrease'
  | 'gmx_net_position'
  | 'gmx_liquidation'
  | 'gmx_swap'
  | 'gmx_deposit'
  | 'gmx_withdraw'
  | 'gmx_net_lp'
  | 'hl_pnl'
  | 'hl_transfers'
  | 'hl_vault_net'
  | 'hl_top_traders'
  | 'uniswap_v2_swap'
  | 'uniswap_v2_deposit'
  | 'uniswap_v2_withdraw'
  | 'uniswap_v2_net_liquidity'
  | 'uniswap_v4_swap'
  | 'uniswap_v4_deposit'
  | 'uniswap_v4_withdraw'
  | 'uniswap_v4_initialize'
  | 'uniswap_v4_net_liquidity'
  | 'aero_swap'
  | 'aero_deposit'
  | 'aero_withdraw'
  | 'aero_collect'
  | 'aero_net_liquidity'
  | 'aero_basic_swap'
  | 'aero_basic_deposit'
  | 'aero_basic_withdraw'
  | 'aero_basic_claim'
  | 'aero_basic_net_liquidity'
  | 'uniswap_swap'
  | 'uniswap_deposit'
  | 'uniswap_withdraw'
  | 'uniswap_collect'
  | 'uniswap_net_liquidity'
  | 'uniswap_net_swap_flow'
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
  oi: 'Open Interest',
  fr: 'Funding Rate',
  bs: 'Buyer vs Seller',
  sz: 'Volume by Size',
  tt: 'Top Traders L/S',
  ls: 'Long/Short',
  transfer: 'Token Flow',
  pc: 'Price Comparison',
  aave_deposit: 'AAVE V3 Deposits',
  aave_withdraw: 'AAVE V3 Withdrawals',
  aave_net_deposit: 'AAVE V3 Net Deposit',
  aave_borrow: 'AAVE V3 Borrows',
  aave_repay: 'AAVE V3 Repays',
  aave_net_borrow: 'AAVE V3 Net Borrow',
  aave_flashloan: 'AAVE V3 Flash Loans',
  aave_liquidation: 'AAVE V3 Liquidations',
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
  spark_deposit: 'Spark Deposits',
  spark_withdraw: 'Spark Withdrawals',
  spark_net_deposit: 'Spark Net Deposit',
  spark_borrow: 'Spark Borrows',
  spark_repay: 'Spark Repays',
  spark_net_borrow: 'Spark Net Borrow',
  spark_flashloan: 'Spark Flash Loans',
  hl_pnl: 'HL Realized PnL',
  hl_transfers: 'HL Bridge Flows',
  hl_vault_net: 'HL Vault Net Flow',
  hl_top_traders: 'HL Top Traders',
  gmx_position_increase: 'GMX Position Open',
  gmx_position_decrease: 'GMX Position Close',
  gmx_net_position: 'GMX Net Position Flow',
  gmx_liquidation: 'GMX Liquidations',
  gmx_swap: 'GMX Swaps',
  gmx_deposit: 'GMX LP Deposits',
  gmx_withdraw: 'GMX LP Withdrawals',
  gmx_net_lp: 'GMX Net LP Flow',
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
  aero_swap: 'Aerodrome CL Swaps',
  aero_deposit: 'Aerodrome CL Deposits',
  aero_withdraw: 'Aerodrome CL Withdrawals',
  aero_collect: 'Aerodrome CL Collects',
  aero_net_liquidity: 'Aerodrome CL Net Liquidity',
  aero_basic_swap: 'Aerodrome Basic Swaps',
  aero_basic_deposit: 'Aerodrome Basic Deposits',
  aero_basic_withdraw: 'Aerodrome Basic Withdrawals',
  aero_basic_claim: 'Aerodrome Basic Claims',
  aero_basic_net_liquidity: 'Aerodrome Basic Net Liquidity',
  uniswap_swap: 'Uniswap V3 Swaps',
  uniswap_deposit: 'Uniswap V3 Deposits',
  uniswap_withdraw: 'Uniswap V3 Withdrawals',
  uniswap_collect: 'Uniswap V3 Collects',
  uniswap_net_liquidity: 'Uniswap V3 Net Liquidity',
  uniswap_net_swap_flow: 'Uniswap V3 Net Swap Flow',
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

/** AAVE chart kinds collected for convenience (loop over them on the
 *  lending page + share helpers). Order matters — used as the default
 *  layout order on the Lending page. */
export const AAVE_CHART_KINDS: ChartKind[] = [
  'aave_deposit',
  'aave_withdraw',
  'aave_net_deposit',
  'aave_borrow',
  'aave_repay',
  'aave_net_borrow',
  'aave_flashloan',
  'aave_liquidation'
];

/** Map from a single-event ChartKind to the AAVE event slug. */
export const AAVE_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  aave_deposit: 'deposit',
  aave_withdraw: 'withdraw',
  aave_borrow: 'borrow',
  aave_repay: 'repay',
  aave_flashloan: 'flashloan',
  aave_liquidation: 'liquidation'
};

/** Net AAVE kinds — each fetches two regular event aggregates in parallel
 *  and plots positive − negative per bucket. positive[0] is added,
 *  positive[1] is subtracted. */
export const AAVE_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  aave_net_deposit: ['deposit', 'withdraw'],
  aave_net_borrow: ['borrow', 'repay']
};

/** True for any AAVE kind (single-event or net). */
export function isAaveKind(kind: ChartKind): boolean {
  return (
    AAVE_KIND_TO_EVENT[kind] !== undefined ||
    AAVE_NET_KIND_TO_EVENTS[kind] !== undefined
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
  return SPARK_KIND_TO_EVENT[kind] !== undefined || SPARK_NET_KIND_TO_EVENTS[kind] !== undefined;
}

/** GMX V2 chart kinds (perp DEX, ARB-only). Filter dimension is per-market
 *  (`market_name` like "BTC/USD [WBTC-USDC]") — same selector model as
 *  Uniswap pools. Per-event value field is picked deliberately because the
 *  server returns `swap.amount_in` and `withdrawals.value_usd` in raw
 *  uint256 units (decoder bug, similar to the Morpho case before its fix). */
export const GMX_CHART_KINDS: ChartKind[] = [
  'gmx_position_increase',
  'gmx_position_decrease',
  'gmx_net_position',
  'gmx_liquidation',
  'gmx_swap',
  'gmx_deposit',
  'gmx_withdraw',
  'gmx_net_lp'
];
export const GMX_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  gmx_position_increase: 'position_increase',
  gmx_position_decrease: 'position_decrease',
  gmx_liquidation: 'liquidation',
  gmx_swap: 'swap',
  gmx_deposit: 'deposit',
  gmx_withdraw: 'withdraw'
};
export const GMX_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  gmx_net_position: ['position_increase', 'position_decrease'],
  gmx_net_lp: ['deposit', 'withdraw']
};
export function isGmxKind(kind: ChartKind): boolean {
  return GMX_KIND_TO_EVENT[kind] !== undefined || GMX_NET_KIND_TO_EVENTS[kind] !== undefined;
}
/** Per-kind value-field picker. Each GMX kind defaults to either sum_amount
 *  (size_delta_usd / token-units) or sum_value_usd — chosen for whichever is
 *  the cleanest unit for that event class on the V1 dashboard. The choice
 *  takes precedence over the instance.valueMode toggle. */
export const GMX_PRIMARY_FIELD: Partial<Record<ChartKind, 'sum_amount' | 'sum_value_usd'>> = {
  // size_delta_usd (USD notional) — the real "position size" number
  gmx_position_increase: 'sum_amount',
  gmx_position_decrease: 'sum_amount',
  gmx_net_position: 'sum_amount',
  gmx_liquidation: 'sum_amount',
  // value_usd is correct here; amount_in is broken upstream
  gmx_swap: 'sum_value_usd',
  // long+short token-units — symmetric across deposit/withdraw so net_lp
  // subtracts apples-to-apples (deposit.value_usd is fine but
  // withdraw.value_usd is broken upstream, so we use token-units everywhere
  // in this family)
  gmx_deposit: 'sum_amount',
  gmx_withdraw: 'sum_amount',
  gmx_net_lp: 'sum_amount'
};

/** Hyperliquid chart kinds (perp DEX, on-chain — every event carries a
 *  wallet identity, enabling per-trader and whale-tracking analyses).
 *  Per-token filter via the same token selector as binance. Per-wallet
 *  filter exposed on every kind via a wallet input + a category dropdown
 *  sourced from the tradernick.wallet_labels CH dictionary. */
export const HL_CHART_KINDS: ChartKind[] = [
  // hl_ohlcv removed — superseded by the generic `ohlcv` kind with
  // exchange='hl'. Same goes for hl_funding_paid → `fr` + exchange='hl'.
  // hl_position_long_size / short_size / net_size removed — the
  // position_history endpoint is deferred (see HL_EVENTS comment).
  'hl_pnl',
  'hl_transfers',
  'hl_vault_net',
  'hl_top_traders'
];
/** Single-event HL kinds → server-side event slug. */
export const HL_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  // hl_ohlcv intentionally absent — handled via the generic `ohlcv` kind.
  // hl_funding_paid intentionally absent — handled via `fr` + exchange='hl'.
  // hl_position_* intentionally absent — position_history endpoint deferred.
  hl_pnl: 'trade_history',
  hl_transfers: 'transfers',
  hl_vault_net: 'vaults'
  // hl_top_traders has no single event — uses the leaderboard endpoint
};
export function isHlKind(kind: ChartKind): boolean {
  return kind.startsWith('hl_');
}
/** Per-kind value-field picker. value_usd for events where the server
 *  computes one; sum_amount otherwise. */
export const HL_PRIMARY_FIELD: Partial<Record<ChartKind, 'sum_amount' | 'sum_value_usd'>> = {
  hl_pnl: 'sum_value_usd',          // realized PnL in USD
  hl_transfers: 'sum_amount',       // USDC amount
  hl_vault_net: 'sum_amount'        // amount
};

/** Uniswap chart kinds collected for the DeX page (default layout order). */
export const UNISWAP_CHART_KINDS: ChartKind[] = [
  'uniswap_swap',
  'uniswap_deposit',
  'uniswap_withdraw',
  'uniswap_collect',
  'uniswap_net_liquidity',
  'uniswap_net_swap_flow'
];

/** Map from a single-event Uniswap kind → the data_server event slug. */
export const UNISWAP_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  uniswap_swap: 'swap',
  uniswap_deposit: 'deposit',
  uniswap_withdraw: 'withdraw',
  uniswap_collect: 'collect'
};

/** Net Uniswap kinds — net_liquidity fetches two endpoints (deposit +
 *  withdraw); net_swap_flow uses the swap endpoint's directional split
 *  via sum_value_usd_t0t1 − sum_value_usd_t1t0 (no second fetch). */
export const UNISWAP_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  uniswap_net_liquidity: ['deposit', 'withdraw']
};

/** True for any Uniswap kind (single-event, net-liquidity, net-swap-flow). */
export function isUniswapKind(kind: ChartKind): boolean {
  return (
    UNISWAP_KIND_TO_EVENT[kind] !== undefined ||
    UNISWAP_NET_KIND_TO_EVENTS[kind] !== undefined ||
    kind === 'uniswap_net_swap_flow'
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
  return (
    UNISWAP_V2_KIND_TO_EVENT[kind] !== undefined ||
    UNISWAP_V2_NET_KIND_TO_EVENTS[kind] !== undefined
  );
}

/** Uniswap V4 chart kinds. V4 LP events lack amount0/amount1 — only
 *  liquidity_delta — so Amount mode on deposit/withdraw isn't meaningful
 *  (the data_server returns 0 for sum_amount0/1 on those events). The
 *  initialize kind is a pool-creation counter. */
export const UNISWAP_V4_CHART_KINDS: ChartKind[] = [
  'uniswap_v4_swap',
  'uniswap_v4_deposit',
  'uniswap_v4_withdraw',
  'uniswap_v4_net_liquidity',
  'uniswap_v4_initialize'
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

/** Aerodrome (concentrated-pool only) chart kinds. BASE chain only. */
export const AERO_CHART_KINDS: ChartKind[] = [
  'aero_swap',
  'aero_deposit',
  'aero_withdraw',
  'aero_collect',
  'aero_net_liquidity'
];
export const AERO_KIND_TO_EVENT: Partial<Record<ChartKind, string>> = {
  aero_swap: 'swap',
  aero_deposit: 'deposit',
  aero_withdraw: 'withdraw',
  aero_collect: 'collect'
};
export const AERO_NET_KIND_TO_EVENTS: Partial<Record<ChartKind, [string, string]>> = {
  aero_net_liquidity: ['deposit', 'withdraw']
};
export function isAeroKind(kind: ChartKind): boolean {
  return (
    AERO_KIND_TO_EVENT[kind] !== undefined ||
    AERO_NET_KIND_TO_EVENTS[kind] !== undefined
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
export function isAeroBasicKind(kind: ChartKind): boolean {
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
  if (kind.startsWith('aave_v4_')) return 'AAVE V4';
  if (kind.startsWith('aave_')) return 'AAVE V3';
  if (kind.startsWith('morpho_')) return 'Morpho';
  if (kind.startsWith('spark_')) return 'Spark';
  if (kind.startsWith('uniswap_v2_')) return 'Uniswap V2';
  if (kind.startsWith('uniswap_v4_')) return 'Uniswap V4';
  if (kind.startsWith('uniswap_')) return 'Uniswap V3';
  if (kind.startsWith('lido_')) return 'Lido';
  if (kind.startsWith('aero_basic_')) return 'Aerodrome Basic';
  if (kind.startsWith('aero_')) return 'Aerodrome CL';
  if (kind.startsWith('gmx_')) return 'GMX V2';
  if (kind.startsWith('hl_')) return 'Hyperliquid';
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
  'GMX V2':          50,
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

/** True for any Lido kind (single-event or net). */
export function isLidoKind(kind: ChartKind): boolean {
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
  /** When true, the chart overlays narrow dotted vertical lines at the
   *  start of each Saturday and Monday (UTC) inside the visible window.
   *  Helps line up weekly cycles across charts. Off by default. */
  showWeekLines?: boolean;
  mas: MAConfig[]; // length MAX_MAS, each slot independently enabled
  /** When true, the chart plots a running cumulative sum of the same
   *  source the MAs operate on, on a secondary axis. Useful for reading
   *  "total deposits over the visible window" / "TVL increase" off
   *  event-driven kinds. Off by default. Only honoured on kinds where
   *  per-bucket values are summable (transfer / AAVE / Morpho / Spark /
   *  Lido / Uniswap-USD / Aerodrome); ignored elsewhere. */
  showSum?: boolean;
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
  }
  if (kind === 'ohlcv') {
    base.pin = false;
    base.volumeUnit = 'token';
  }
  if (kind === 'fr') {
    base.frDisplay = 'rate8h';
  }
  if (kind === 'pc') {
    base.overlayTokens = [];
  }
  if (isAaveKind(kind)) {
    // AAVE charts (single-event + net) behave like transfer charts —
    // keyed by (chain, token) — so we surface the same selectors.
    // Default eth_market is empty, which the data_server treats as
    // "all markets".
    base.chain = defaults.chain ?? 'ETH';
    base.valueMode = 'usd';
  }
  if (isAaveV2Kind(kind)) {
    // V2 only has two configured chains (ETH + POLYGON) — defaults to ETH.
    base.chain = defaults.chain ?? 'ETH';
    base.valueMode = 'usd';
  }
  if (isAaveV4Kind(kind)) {
    // V4 is mainnet-only for now; default ETH.
    base.chain = 'ETH';
    base.valueMode = 'usd';
  }
  if (isMorphoKind(kind)) {
    // Morpho is ETH + BASE.
    base.chain = defaults.chain ?? 'ETH';
    base.valueMode = 'usd';
  }
  if (isSparkKind(kind)) {
    // Spark is ETH-only.
    base.chain = 'ETH';
    base.valueMode = 'usd';
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
  }
  if (isGmxKind(kind)) {
    // GMX V2 is ARB-only (server-side AVAX is "not configured" in 2.14).
    // Default market = canonical BTC/USD pool; the chart's selector lists
    // every market /api/gmx/streams returns. valueMode is overridden per
    // chart kind via GMX_PRIMARY_FIELD — the Sum-/MA-style fetch picks
    // sum_amount or sum_value_usd directly off the response shape.
    base.chain = 'ARB';
    base.gmxMarket = 'BTC/USD [WBTC-USDC]';
    base.valueMode = 'usd';
  }
  if (kind === 'transfer') {
    base.chain = defaults.chain ?? 'ETH';
    base.filter = {};
    base.valueMode = 'usd';
  }
  if (isUniswapKind(kind)) {
    base.chain = defaults.chain ?? 'ETH';
    // Conservative default: canonical USDC/WETH 0.05%. The page-level loader
    // will replace this with the first available pool from /uniswap/streams.
    base.uniPool = { symbol0: 'USDC', symbol1: 'WETH', fee: 500 };
    // Default USD for the headline series. Amount mode is per-chart and
    // not meaningful for net_swap_flow (see ChartInstance for the gate).
    base.valueMode = 'usd';
  }
  if (isUniswapV2Kind(kind)) {
    base.chain = defaults.chain ?? 'ETH';
    // V2 has no fee tier — reuse the uniPool shape with fee=0 as a sentinel.
    // ChartInstance treats fee=0 as "V2" when issuing requests so the
    // selector + fetch paths don't need a parallel shape.
    base.uniPool = { symbol0: 'USDC', symbol1: 'WETH', fee: 0 };
    base.valueMode = 'usd';
  }
  if (isUniswapV4Kind(kind)) {
    base.chain = defaults.chain ?? 'ETH';
    // Canonical V4 pool: USDC/WETH 0.05% fee, tick_spacing=10, no hooks.
    base.uniV4Pool = {
      symbol0: 'USDC', symbol1: 'WETH', fee: 500, tick_spacing: 10,
      hooks: '0x0000000000000000000000000000000000000000'
    };
    base.valueMode = 'usd';
  }
  if (isAeroKind(kind)) {
    base.chain = 'BASE';
    // Default to USDC/WETH ts=100 (top Aero CL pool by volume).
    base.aeroPool = { symbol0: 'USDC', symbol1: 'WETH', tick_spacing: 100 };
    base.valueMode = 'usd';
  }
  if (isAeroBasicKind(kind)) {
    base.chain = 'BASE';
    // Default to USDC/WETH vAMM (top basic pool by volume).
    base.aeroBasicPool = { symbol0: 'USDC', symbol1: 'WETH', stable: false };
    base.valueMode = 'usd';
  }
  if (isLidoKind(kind)) {
    // Lido charts are chain-only (no token / pool axis). L1 kinds are
    // pinned to ETH by construction; L2 kinds default to ARB (highest
    // wstETH bridge volume), the user can flip via the chain dropdown.
    base.chain = LIDO_L1_KINDS.has(kind) ? 'ETH' : (defaults.chain ?? 'ARB');
    base.valueMode = 'usd';
  }
  return base;
}
