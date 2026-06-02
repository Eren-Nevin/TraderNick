import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

const ALLOWED = new Set(['start', 'stop', 'restart']);

export const POST: RequestHandler = async ({ fetch, params }) => {
  const { name, action } = params;
  if (!ALLOWED.has(action)) throw error(400, `unknown action ${action}`);
  const res = await fetch(`${INTERNAL_INGESTION_URL}/streams/${encodeURIComponent(name)}/${action}`, {
    method: 'POST',
    headers: { Authorization: ingestionAuthHeader() }
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
