// Provider list + slug helpers + backfill-form mapping. Single source of
// truth for the admin route tree — the layout sidebar, the live/[provider]
// page, and the backfill/[provider] page all read from here.
//
// Kept in lockstep with services/ingestion/src/streams/__init__.py
// (StreamSpec.group field) and dashboard/src/lib/admin/backfill_forms.ts.

import { BACKFILL_FORMS, type BackfillFormSpec } from './backfill_forms';

export const PROVIDERS = [
  'Hyperliquid', 'Binance', 'Transfers',
  'AAVE V3', 'AAVE V2', 'AAVE V4',
  'Uniswap V3', 'Uniswap V2', 'Uniswap V4',
  'Aerodrome', 'Aerodrome Basic',
  'Lido', 'Morpho', 'Spark', 'GMX',
] as const;

export type Provider = (typeof PROVIDERS)[number];

export function providerSlug(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
}

const _SLUG_TO_NAME: Record<string, Provider> = Object.fromEntries(
  PROVIDERS.map((p) => [providerSlug(p), p])
);

export function providerFromSlug(slug: string): Provider | null {
  return _SLUG_TO_NAME[slug] ?? null;
}

// form_type (without `backfill_` prefix) -> provider name. Mirrors what the
// admin Overview groups visually today.
export const FORM_TYPE_TO_PROVIDER: Record<string, Provider> = {
  hyperliquid_events: 'Hyperliquid',

  binance_ohlcv: 'Binance',
  binance_raw_trades: 'Binance',
  binance_open_interest: 'Binance',
  binance_long_short_ratios: 'Binance',
  binance_funding_rate: 'Binance',

  evm_erc20_transfers: 'Transfers',
  evm_native_transfers: 'Transfers',
  btc_transfers: 'Transfers',
  tron_native_transfers: 'Transfers',
  tron_trc20_transfers: 'Transfers',

  aave_v3_events: 'AAVE V3',
  aave_v2_events: 'AAVE V2',
  aave_v4_events: 'AAVE V4',

  uniswap_v3_events: 'Uniswap V3',
  uniswap_v2_events: 'Uniswap V2',
  uniswap_v4_events: 'Uniswap V4',

  aero_concentrated_events: 'Aerodrome',
  aero_basic_events: 'Aerodrome Basic',

  lido_events: 'Lido',
  morpho_events: 'Morpho',
  spark_events: 'Spark',
  gmx_v2_events: 'GMX',
};

export function formsForProvider(p: Provider): BackfillFormSpec[] {
  return BACKFILL_FORMS.filter((f) => FORM_TYPE_TO_PROVIDER[f.type] === p);
}

// `j.job_type` arrives prefixed with "backfill_". This strips and looks up.
export function jobProvider(jobType: string): Provider | null {
  const key = jobType.replace(/^backfill_/, '');
  return FORM_TYPE_TO_PROVIDER[key] ?? null;
}
