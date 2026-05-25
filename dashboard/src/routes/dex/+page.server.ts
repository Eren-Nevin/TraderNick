import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { ChainGroup, TokenGroup, UniswapStream } from '$lib/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
  const [tokensRes, uniRes, tokenGroupsRes, chainGroupsRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/uniswap/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/token-groups`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/chain-groups`)
  ]);
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const uniPools: UniswapStream[] = uniRes.ok ? (await uniRes.json()).streams : [];
  const tokenGroups: TokenGroup[] = tokenGroupsRes.ok ? (await tokenGroupsRes.json()).groups : [];
  const chainGroups: ChainGroup[] = chainGroupsRes.ok ? (await chainGroupsRes.json()).groups : [];
  return { tokens, uniPools, tokenGroups, chainGroups };
};
