import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// GET: list all SmartSelector presets.
// POST: upsert a preset { name, config }.

export const GET: RequestHandler = async ({ fetch }) => {
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/hyperliquid/smart_selector_presets`);
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), { status: 200, headers: { 'content-type': 'application/json' } });
};

export const POST: RequestHandler = async ({ request, fetch }) => {
  const body = await request.text();
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/hyperliquid/smart_selector_presets`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body,
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), { status: 200, headers: { 'content-type': 'application/json' } });
};
