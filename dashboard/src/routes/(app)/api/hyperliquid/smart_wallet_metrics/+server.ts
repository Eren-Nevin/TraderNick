import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

const PASSTHROUGH = [
  'token', 'lookback', 'snapshot', 'metric', 'order_by', 'limit', 'cutoff', 'lookbacks', 'group', 'combine',
  'min_days', 'min_volume', 'min_realized', 'min_oi',
  'min_avg_trade_size', 'min_taker_pct', 'max_fee_pct', 'max_funding_pct',
  'min_account_duration', 'min_tokens', 'min_win_rate',
  'min_trades_per_day', 'max_trades_per_day',
  'min_annualized_sharpe',
  'min_avg_oi_share', 'max_avg_oi_share', 'min_volume_share', 'max_volume_share'
];

export const GET: RequestHandler = async ({ url, fetch }) => {
  const params = new URLSearchParams();
  for (const k of PASSTHROUGH) {
    const v = url.searchParams.get(k);
    if (v !== null) params.set(k, v);
  }
  const res = await fetch(
    `${INTERNAL_DATA_SERVER_URL}/hyperliquid/smart_wallet_metrics?${params}`
  );
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
