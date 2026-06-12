import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

const PASSTHROUGH = [
  'token', 'interval', 'since', 'until', 'limit',
  // Wallet selection — one JSON-encoded blob defined by smart_selector.py.
  // The legacy per-knob params (pnl_lookback_days, pnl_floor_usd, top_n,
  // leaderboard_scope, pnl_filter) were dropped along with the route's
  // backward-compat layer. `filter` is the composable (nested-refs) form;
  // `selector` is the flat legacy form — the backend accepts either.
  'selector', 'filter'
];

export const GET: RequestHandler = async ({ url, fetch }) => {
  const params = new URLSearchParams();
  for (const k of PASSTHROUGH) {
    const v = url.searchParams.get(k);
    if (v !== null) params.set(k, v);
  }
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/hyperliquid/smart_oi?${params}`);
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), { status: 200, headers: { 'content-type': 'application/json' } });
};
