import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Remove a token override (soft-delete). Takes effect within the cache TTL.
export const DELETE: RequestHandler = async ({ fetch, params }) => {
  const res = await fetch(
    `${INTERNAL_INGESTION_URL}/config/token_overrides/${encodeURIComponent(params.kind)}/${encodeURIComponent(params.token)}`,
    { method: 'DELETE', headers: { Authorization: ingestionAuthHeader() } }
  );
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
