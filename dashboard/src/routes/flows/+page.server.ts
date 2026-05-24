import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import type { TransferCompound, TransferStream } from '$lib/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ fetch }) => {
  const [streamsRes, tokensRes, compoundsRes] = await Promise.all([
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/streams`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/tokens`),
    fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/compounds`)
  ]);
  const streams: TransferStream[] = streamsRes.ok ? (await streamsRes.json()).streams : [];
  const tokens: string[] = tokensRes.ok ? (await tokensRes.json()).tokens : [];
  const compounds: TransferCompound[] = compoundsRes.ok
    ? (await compoundsRes.json()).compounds
    : [];
  return { streams, tokens, compounds };
};
