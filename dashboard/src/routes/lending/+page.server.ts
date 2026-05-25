import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { ChainGroup, TokenGroup } from '$lib/api';
import type { PageServerLoad } from './$types';

/** Distinct (event, chain, token) tuples that have any AAVE data, plus
 *  which eth_markets each row appears in. Used by the Lending page's
 *  default layout + per-chart token selector. */
export type AaveStream = {
  event: string;
  chain: string;
  token: string;
  eth_markets: string[];
  rows: number;
};

export const load: PageServerLoad = async ({ fetch }) => {
  const [tokensRes, streamsRes, tokenGroupsRes, chainGroupsRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/aave/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/token-groups`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/chain-groups`)
  ]);
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const aaveStreams: AaveStream[] = streamsRes.ok
    ? (await streamsRes.json()).streams
    : [];
  const tokenGroups: TokenGroup[] = tokenGroupsRes.ok
    ? (await tokenGroupsRes.json()).groups
    : [];
  const chainGroups: ChainGroup[] = chainGroupsRes.ok
    ? (await chainGroupsRes.json()).groups
    : [];
  return { tokens, aaveStreams, tokenGroups, chainGroups };
};
