import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import {
  INTERVALS,
  type Interval,
  type TransferBucket,
  type TransferStream
} from '$lib/api';
import type { PageServerLoad } from './$types';

const LOOKBACK_DAYS = 30;

export const load: PageServerLoad = async ({ url, fetch }) => {
  const intervalParam = url.searchParams.get('interval') ?? '1h';
  const interval: Interval = (INTERVALS as readonly string[]).includes(intervalParam)
    ? (intervalParam as Interval)
    : '1h';

  const streamsRes = await fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/streams`);
  const streams: TransferStream[] = streamsRes.ok ? (await streamsRes.json()).streams : [];

  const chain = url.searchParams.get('chain') ?? 'ETH';
  const token = url.searchParams.get('token') ?? 'LINK';
  const kindParam = url.searchParams.get('kind');
  // resolve kind from the stream list if the user didn't pin one
  const matched = streams.find((s) => s.chain === chain && s.token === token);
  const kind = kindParam ?? matched?.kind ?? 'erc20';

  const now = new Date();
  const until = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
  const since = new Date(until.getTime() - LOOKBACK_DAYS * 24 * 60 * 60 * 1000);

  const qs = new URLSearchParams({
    chain,
    kind,
    token,
    interval,
    since: since.toISOString(),
    until: until.toISOString(),
    limit: '10000'
  });
  const aggRes = await fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/aggregate?${qs}`);
  const buckets: TransferBucket[] = aggRes.ok ? (await aggRes.json()).series : [];

  return {
    streams,
    chain,
    kind,
    token,
    interval,
    buckets,
    since: since.toISOString(),
    until: until.toISOString()
  };
};
