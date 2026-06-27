import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Proxy for the granular wallet-pin writes: pin / unpin / group / group_delete.
// Each is a single-row CH write, so a pin never disturbs another wallet's rows.
const ACTIONS = new Set(['pin', 'unpin', 'group', 'group_delete']);

export const POST: RequestHandler = async ({ params, request, fetch }) => {
  if (!ACTIONS.has(params.action)) throw error(404, 'unknown action');
  const body = await request.text();
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/wallet_pins/${params.action}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
