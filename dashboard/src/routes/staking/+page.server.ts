import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { ChainGroup } from '$lib/api';
import type { PageServerLoad } from './$types';

/** One row per (event, chain) that has Lido data, with row counts. Drives
 *  the per-chart chain selector on the Staking page so the dropdown only
 *  surfaces L2s that DeFiStream is actually delivering for. */
export type LidoStream = {
  event: string;
  chain: string;
  rows: number;
};

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
