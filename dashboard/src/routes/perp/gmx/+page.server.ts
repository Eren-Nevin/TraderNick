import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { PageServerLoad } from './$types';

export type GmxMarketRow = {
  event: string;
  chain: string;
  market: string;
  rows: number;
};

export const load: PageServerLoad = async ({ fetch }) => {
  const [streamsRes, tokensRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/gmx/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`)
  ]);
  const gmxMarkets: GmxMarketRow[] = streamsRes.ok ? (await streamsRes.json()).streams : [];
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  return { gmxMarkets, tokens };
};
