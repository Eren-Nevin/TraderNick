import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { ChainGroup } from '$lib/api';
import type { PageServerLoad } from './$types';

export type LidoStream = {
  event: string;
  chain: string;
  rows: number;
};

// Single Staking page hosts every staking-protocol wrapper kind. Lido is
// the only one for now; future Stader / Frax etc. can be added to
// AVAILABLE_KINDS on the page without touching this loader.
export const load: PageServerLoad = async ({ fetch }) => {
  const [tokensRes, lidoRes, chainGroupsRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/lido/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/chain-groups`)
  ]);
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const lidoChains: LidoStream[] = lidoRes.ok ? (await lidoRes.json()).streams : [];
  const chainGroups: ChainGroup[] = chainGroupsRes.ok ? (await chainGroupsRes.json()).groups : [];
  return { tokens, lidoChains, chainGroups };
};
