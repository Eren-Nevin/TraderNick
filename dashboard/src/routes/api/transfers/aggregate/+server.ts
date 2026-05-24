import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

const PASSTHROUGH = [
  // single-stream selection
  'chain',
  'kind',
  'token',
  // group axes — either or both may be set; resolution happens server-side
  'chain_group',
  'token_group',
  'interval',
  'since',
  'until',
  'limit',
  // wallet category filter passthrough
  'sender_in',
  'sender_ex',
  'receiver_in',
  'receiver_ex',
  'involving_in',
  'involving_ex',
  // wallet entity filter passthrough
  'sender_entity_in',
  'sender_entity_ex',
  'receiver_entity_in',
  'receiver_entity_ex',
  'involving_entity_in',
  'involving_entity_ex',
  // new extras JSON
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
