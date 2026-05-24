import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

const PASSTHROUGH = [
  'chain',
  'kind',
  'token',
  'interval',
  'since',
  'until',
  'limit',
  // wallet filter passthrough — both the legacy single-filter form and the new extras JSON
  'sender_in',
  'sender_ex',
  'receiver_in',
  'receiver_ex',
  'involving_in',
  'involving_ex',
  'extras'
];

export const GET: RequestHandler = async ({ url, fetch }) => {
  const params = new URLSearchParams();
  for (const key of PASSTHROUGH) {
    const v = url.searchParams.get(key);
    if (v !== null) params.set(key, v);
  }
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/transfers/aggregate?${params}`);
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
