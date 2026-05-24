import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { ChainGroup, TokenGroup, TransferStream } from '$lib/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
  const [streamsRes, tokensRes, tokenGroupsRes, chainGroupsRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/token-groups`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/chain-groups`)
  ]);
  const streams: TransferStream[] = streamsRes.ok ? (await streamsRes.json()).streams : [];
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const tokenGroups: TokenGroup[] = tokenGroupsRes.ok
    ? (await tokenGroupsRes.json()).groups
    : [];
  const chainGroups: ChainGroup[] = chainGroupsRes.ok
    ? (await chainGroupsRes.json()).groups
    : [];
  return { streams, tokens, tokenGroups, chainGroups };
};
