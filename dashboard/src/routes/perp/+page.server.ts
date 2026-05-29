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
  // `tokens` feeds the fallback token <select> on the title bar for kinds
  // that don't have a dedicated selector branch (OHLCV, the binance-side
  // kinds OI/FR/BS/SZ/TT/LS, PC). Without it those charts can't be
  // re-pointed to a different token from the Perp page.
  const [streamsRes, tokensRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/gmx/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`)
  ]);
  const gmxMarkets: GmxMarketRow[] = streamsRes.ok ? (await streamsRes.json()).streams : [];
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  return { gmxMarkets, tokens };
};
