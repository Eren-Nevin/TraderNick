import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { PageServerLoad } from './$types';

export type HlStreamRow = {
  event: string;
  token: string;
  rows: number;
};

export const load: PageServerLoad = async ({ fetch }) => {
  const [tokensRes, streamsRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/hyperliquid/streams`)
  ]);
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const hlStreams: HlStreamRow[] = streamsRes.ok ? (await streamsRes.json()).streams : [];
  return { tokens, hlStreams };
};
