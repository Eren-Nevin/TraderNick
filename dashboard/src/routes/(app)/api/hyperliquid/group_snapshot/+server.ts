import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Proxy for /hyperliquid/group_snapshot — a wallet group's positions combined per token.
const PASSTHROUGH = ['group', 'staleness', 'as_of', 'token', 'price_lb'];

export const GET: RequestHandler = async ({ url, fetch }) => {
  const params = new URLSearchParams();
  for (const k of PASSTHROUGH) {
    const v = url.searchParams.get(k);
    if (v !== null) params.set(k, v);
  }
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/hyperliquid/group_snapshot?${params}`);
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), { status: 200, headers: { 'content-type': 'application/json' } });
};
