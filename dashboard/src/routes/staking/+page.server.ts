import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
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
  const [tokensRes, lidoRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/lido/streams`)
  ]);
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const lidoChains: LidoStream[] = lidoRes.ok ? (await lidoRes.json()).streams : [];
  return { tokens, lidoChains };
};
