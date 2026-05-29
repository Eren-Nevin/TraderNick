import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { PageServerLoad } from './$types';

/** GMX V2 market dimension exposed by /gmx/streams — one row per
 *  (event, chain, market_name). The per-chart selector ranks by row count
 *  to surface the most-active perp first. */
export type GmxMarketRow = {
  event: string;
  chain: string;
  market: string;
  rows: number;
};

export const load: PageServerLoad = async ({ fetch }) => {
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/gmx/streams`);
  const gmxMarkets: GmxMarketRow[] = res.ok ? (await res.json()).streams : [];
  return { gmxMarkets };
};
