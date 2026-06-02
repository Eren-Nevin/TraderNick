import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { ChainGroup, TokenGroup } from '$lib/api';
import type { PageServerLoad } from './$types';

// Single Lending page hosts every lending-protocol wrapper kind
// (AAVE V2/V3/V4, Morpho, Spark). Same upstream data shape — none of
// the wrappers needs protocol-specific stream metadata beyond the
// tokens / token-groups / chain-groups they all share.
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
