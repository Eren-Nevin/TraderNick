import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import {
  INTERVALS,
  type Candle,
  type FundingRateRow,
  type Interval,
  type LongShortRow,
  type OpenInterestRow,
  type VolumeBucket
} from '$lib/api';
import type { PageServerLoad } from './$types';

// Match the rest of the dashboard: show every row we have (60-day TTL).
const DEFAULT_LOOKBACK_DAYS: Record<Interval, number> = {
  '1m': 60,
  '5m': 60,
  '15m': 60,
  '30m': 60,
  '1h': 60,
  '4h': 60,
  '1d': 60
};

export const load: PageServerLoad = async ({ url, fetch }) => {
  const token = url.searchParams.get('token') ?? 'BTC';
  const intervalParam = url.searchParams.get('interval') ?? '4h';
  const interval: Interval = (INTERVALS as readonly string[]).includes(intervalParam)
    ? (intervalParam as Interval)
    : '4h';
  const under = Number(url.searchParams.get('under') ?? '10000');
  const over = Number(url.searchParams.get('over') ?? '100000');

  const now = new Date();
  const until = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
  const lookback = DEFAULT_LOOKBACK_DAYS[interval];
  const since = new Date(until.getTime() - lookback * 24 * 60 * 60 * 1000);

  const ohlcvQS = new URLSearchParams({
    token,
    interval,
    since: since.toISOString(),
    until: until.toISOString(),
    limit: '5000'
  });
  const tvQS = new URLSearchParams({
    ...Object.fromEntries(ohlcvQS),
    under: String(under),
    over: String(over)
  });

  const derivQS = new URLSearchParams({
    token,
    interval,
    since: since.toISOString(),
    until: until.toISOString(),
    limit: '5000'
  });

  const [ohlcvRes, tokensRes, tvRes, oiRes, lsRes, frRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/ohlcv?${ohlcvQS}`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/trade_volume?${tvQS}`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/open_interest?${derivQS}`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/long_short_ratios?${derivQS}`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/funding_rate?${derivQS}`)
  ]);

  const candles: Candle[] = ohlcvRes.ok ? (await ohlcvRes.json()).candles : [];
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const buckets: VolumeBucket[] = tvRes.ok ? (await tvRes.json()).buckets : [];
  const openInterest: OpenInterestRow[] = oiRes.ok ? (await oiRes.json()).series : [];
  const longShort: LongShortRow[] = lsRes.ok ? (await lsRes.json()).series : [];
  const fundingRate: FundingRateRow[] = frRes.ok ? (await frRes.json()).series : [];

  return {
    token,
    interval,
    under,
    over,
    candles,
    buckets,
    openInterest,
    longShort,
    fundingRate,
    tokens: tokens.length ? tokens : [token],
    since: since.toISOString(),
    until: until.toISOString()
  };
};
