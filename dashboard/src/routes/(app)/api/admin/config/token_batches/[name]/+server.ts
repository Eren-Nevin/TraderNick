import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Remove a token batch (soft-delete in the ingestion store). Takes effect
// across all ingestion processes within the token-batch cache TTL — no restart.
export const DELETE: RequestHandler = async ({ fetch, params }) => {
  const res = await fetch(
    `${INTERNAL_INGESTION_URL}/config/token_batches/${encodeURIComponent(params.name)}`,
    { method: 'DELETE', headers: { Authorization: ingestionAuthHeader() } }
  );
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
