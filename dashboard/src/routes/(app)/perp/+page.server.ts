import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { PageServerLoad } from './$types';

export type GmxMarketRow = {
  event: string;
  chain: string;
  market: string;
  rows: number;
};

// Single Perp page hosts both GMX V2 and Hyperliquid wrappers. Hyperliquid
// charts read straight from tokens; GMX needs the per-market stream list.
export const load: PageServerLoad = async ({ fetch }) => {
  const [tokensRes, streamsRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/gmx/streams`)
  ]);
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const gmxMarkets: GmxMarketRow[] = streamsRes.ok ? (await streamsRes.json()).streams : [];
  return { tokens, gmxMarkets };
};
