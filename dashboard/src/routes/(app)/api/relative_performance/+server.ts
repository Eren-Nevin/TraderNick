import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Proxy for /relative_performance — every token's performance vs a base, as a time series.
const PASSTHROUGH = ['base', 'since', 'until', 'lookback', 'interval', 'min_volume', 'exchange'];

export const GET: RequestHandler = async ({ url, fetch }) => {
  const params = new URLSearchParams();
  for (const key of PASSTHROUGH) {
    const v = url.searchParams.get(key);
    if (v !== null) params.set(key, v);
  }
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/relative_performance?${params}`);
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), { status: 200, headers: { 'content-type': 'application/json' } });
};
