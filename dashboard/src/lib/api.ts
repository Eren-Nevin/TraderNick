export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  buyer_taker_volume: number;
  seller_taker_volume: number;
  trade_count: number;
};

export type OhlcvResponse = {
  token: string;
  interval: string;
  candles: Candle[];
};

export const INTERVALS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d'] as const;
export type Interval = (typeof INTERVALS)[number];

export type VolumeBucket = {
  time: number;
  buyer_taker_usd: number;
  seller_taker_usd: number;
  small_usd: number;
  mid_usd: number;
  large_usd: number;
  small_count: number;
  mid_count: number;
  large_count: number;
  buyer_count: number;
  seller_count: number;
};

export type TradeVolumeResponse = {
  token: string;
  interval: string;
  under: number;
  over: number;
  buckets: VolumeBucket[];
};

export type OpenInterestRow = {
  time: number;
  open_interest: number;
  open_interest_value: number;
};

export type LongShortRow = {
  time: number;
  top_trader_count_ratio: number;
  top_trader_vol_ratio: number;
  long_short_count_ratio: number;
  taker_long_short_vol_ratio: number;
};

export type FundingRateRow = {
  time: number;
  rate: number;
};

export type TransferBucket = {
  time: number;
  sum_amount: number;
  sum_value_usd: number;
  count: number;
};

export type TransferStream = {
  kind: string;
  chain: string;
  token: string;
};

/** A server-defined token group — a named bundle of token symbols
 *  (e.g. "Stables" = USDC + USDT + DAI + USDE). At query time the
 *  backend cross-products this with whatever chain selection is in
 *  play and intersects against the streams catalogue, so unmatched
 *  tokens silently contribute zero. */
export type TokenGroup = {
  name: string;
  label: string;
  description: string;
  tokens: string[];
};

/** A server-defined chain group — a named bundle of chain names
 *  (e.g. "EVM" = ETH + ARB + BASE + BSC + POLYGON, or the dynamic
 *  "All" which expands to every ingested chain). */
export type ChainGroup = {
  name: string;
  label: string;
  description: string;
  chains: string[];
};

export type WalletCategory = {
  name: string;
  count: number;
};

export type TransferFilters = {
  sender_in?: string[];
  sender_ex?: string[];
  receiver_in?: string[];
  receiver_ex?: string[];
  involving_in?: string[];
  involving_ex?: string[];
};
