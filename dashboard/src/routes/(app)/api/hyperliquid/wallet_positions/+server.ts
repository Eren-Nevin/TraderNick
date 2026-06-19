import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Proxy for /hyperliquid/wallet_positions — a wallet's stored positions as of
// a past day (historical companion to /live_positions). Powers the wallet
// detail page's positions table when the date slider is in the past.
const PASSTHROUGH = ['wallet', 'day'];

export const GET: RequestHandler = async ({ url, fetch }) => {
  const params = new URLSearchParams();
  for (const k of PASSTHROUGH) {
    const v = url.searchParams.get(k);
    if (v !== null) params.set(k, v);
  }
  const res = await fetch(
    `${INTERNAL_DATA_SERVER_URL}/hyperliquid/wallet_positions?${params}`
  );
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
