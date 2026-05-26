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
  aave_deposit: 'AAVE Deposits',
  aave_withdraw: 'AAVE Withdrawals',
  aave_net_deposit: 'AAVE Net Deposit',
  aave_borrow: 'AAVE Borrows',
  aave_repay: 'AAVE Repays',
  aave_net_borrow: 'AAVE Net Borrow',
  aave_flashloan: 'AAVE Flash Loans',
  aave_liquidation: 'AAVE Liquidations',
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

/** Lido chart kinds (Staking page default layout order). 3 mainnet events
 *  + Net Stake (deposits − claims) + 2 L2 events + Net L2 (bridge in/out). */
export const LIDO_CHART_KINDS: ChartKind[] = [
  'lido_deposit',
  'lido_withdrawal_request',
  'lido_withdrawal_claimed',
  'lido_net_stake',
  'lido_net_request_stake',
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
  lido_l2_net: ['l2_deposit', 'l2_withdrawal_request']
};

/** Lido kinds that run on mainnet (ETH-only chain selector). */
export const LIDO_L1_KINDS = new Set<ChartKind>([
  'lido_deposit',
  'lido_withdrawal_request',
  'lido_withdrawal_claimed',
  'lido_net_stake',
  'lido_net_request_stake'
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
  // sz only
  under?: number;
  over?: number;
  underInput?: string;
  overInput?: string;
  // ohlcv only
  pin?: boolean;
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
  }
  if (kind === 'ohlcv') {
    base.pin = false;
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
  if (isLidoKind(kind)) {
    // Lido charts are chain-only (no token / pool axis). L1 kinds are
    // pinned to ETH by construction; L2 kinds default to ARB (highest
    // wstETH bridge volume), the user can flip via the chain dropdown.
    base.chain = LIDO_L1_KINDS.has(kind) ? 'ETH' : (defaults.chain ?? 'ARB');
    base.valueMode = 'usd';
  }
  return base;
}
