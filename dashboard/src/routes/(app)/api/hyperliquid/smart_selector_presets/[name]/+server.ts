import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// DELETE a preset by name.
export const DELETE: RequestHandler = async ({ params, fetch }) => {
  const name = encodeURIComponent(params.name ?? '');
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/hyperliquid/smart_selector_presets/${name}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), { status: 200, headers: { 'content-type': 'application/json' } });
};
