import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { ChainGroup, TokenGroup } from '$lib/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
  const [tokensRes, tokenGroupsRes, chainGroupsRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/token-groups`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/chain-groups`)
  ]);
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const tokenGroups: TokenGroup[] = tokenGroupsRes.ok ? (await tokenGroupsRes.json()).groups : [];
  const chainGroups: ChainGroup[] = chainGroupsRes.ok ? (await chainGroupsRes.json()).groups : [];
  return { tokens, tokenGroups, chainGroups };
};
