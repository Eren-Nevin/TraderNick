import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Proxy for /hyperliquid/early_movers — detect price moves + rank wallets that
// predicted them.
const PASSTHROUGH = ['token', 'interval', 'since', 'until', 'long_thr', 'short_thr',
  'max_len', 'lead', 'mode', 'min_size', 'min_avg_size', 'n', 'moves_only', 'skip_intra',
  'min_correct_long', 'min_correct_short', 'min_correct_long_pct', 'min_correct_short_pct',
  'min_realized_pnl'];

export const GET: RequestHandler = async ({ url, fetch }) => {
  const params = new URLSearchParams();
  for (const k of PASSTHROUGH) {
    const v = url.searchParams.get(k);
    if (v !== null) params.set(k, v);
  }
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/hyperliquid/early_movers?${params}`);
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), { status: 200, headers: { 'content-type': 'application/json' } });
};
