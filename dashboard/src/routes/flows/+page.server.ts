import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import {
  INTERVALS,
  type Candle,
  type Interval,
  type TransferBucket,
  type TransferStream
} from '$lib/api';
import type { PageServerLoad } from './$types';

const LOOKBACK_DAYS = 30;
const OHLCV_LOOKBACK_DAYS: Record<Interval, number> = {
  '1m': 1,
  '5m': 3,
  '15m': 7,
  '30m': 14,
  '1h': 14,
  '4h': 30,
  '1d': 30
};

export const load: PageServerLoad = async ({ url, fetch }) => {
  const intervalParam = url.searchParams.get('interval') ?? '1h';
  const interval: Interval = (INTERVALS as readonly string[]).includes(intervalParam)
    ? (intervalParam as Interval)
    : '1h';

  const [streamsRes, tokensRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`)
  ]);
  const streams: TransferStream[] = streamsRes.ok ? (await streamsRes.json()).streams : [];
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];

  const chain = url.searchParams.get('chain') ?? 'ETH';
  const token = url.searchParams.get('token') ?? 'LINK';
  const kindParam = url.searchParams.get('kind');
  const matched = streams.find((s) => s.chain === chain && s.token === token);
  const kind = kindParam ?? matched?.kind ?? 'erc20';

  const now = new Date();
  const until = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
  const since = new Date(until.getTime() - LOOKBACK_DAYS * 24 * 60 * 60 * 1000);

  const aggQS = new URLSearchParams({
    chain,
    kind,
    token,
    interval,
    since: since.toISOString(),
    until: until.toISOString(),
    limit: '10000'
  });

  const ohlcvToken = url.searchParams.get('ohlcv_token') ?? token;
  const ohlcvInterval: Interval = (INTERVALS as readonly string[]).includes(
    url.searchParams.get('ohlcv_interval') ?? ''
  )
    ? (url.searchParams.get('ohlcv_interval') as Interval)
    : interval;
  const ohlcvLookback = OHLCV_LOOKBACK_DAYS[ohlcvInterval];
  const ohlcvUntil = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
  const ohlcvSince = new Date(ohlcvUntil.getTime() - ohlcvLookback * 24 * 60 * 60 * 1000);
  const ohlcvQS = new URLSearchParams({
    token: ohlcvToken,
    interval: ohlcvInterval,
    since: ohlcvSince.toISOString(),
    until: ohlcvUntil.toISOString(),
    limit: '5000'
  });

  const [aggRes, ohlcvRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/aggregate?${aggQS}`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/ohlcv?${ohlcvQS}`)
  ]);
  const buckets: TransferBucket[] = aggRes.ok ? (await aggRes.json()).series : [];
  const candles: Candle[] = ohlcvRes.ok ? (await ohlcvRes.json()).candles : [];

  return {
    streams,
    tokens: tokens.length ? tokens : [ohlcvToken],
    chain,
    kind,
    token,
    interval,
    buckets,
    since: since.toISOString(),
    until: until.toISOString(),
    ohlcvToken,
    ohlcvInterval,
    candles,
    ohlcvSince: ohlcvSince.toISOString(),
    ohlcvUntil: ohlcvUntil.toISOString()
  };
};
