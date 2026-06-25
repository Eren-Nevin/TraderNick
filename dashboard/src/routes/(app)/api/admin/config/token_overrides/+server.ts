import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Proxy for token overrides (deprecated + renamed tokens). Deprecated tokens
// are dropped from the live roster (kept for backfill); renamed tokens are
// swapped old→new for live and kept-plus-added for backfill. Managed on the
// /admin/batches page. Edits take effect across ingestion within ~30s.
export const GET: RequestHandler = async ({ fetch }) => {
  const res = await fetch(`${INTERNAL_INGESTION_URL}/config/token_overrides`, {
    headers: { Authorization: ingestionAuthHeader() }
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};

// Create / replace an override. Body: {kind, token, new_token?}.
export const PUT: RequestHandler = async ({ fetch, request }) => {
  const body = await request.text();
  const res = await fetch(`${INTERNAL_INGESTION_URL}/config/token_overrides`, {
    method: 'PUT',
    headers: { Authorization: ingestionAuthHeader(), 'content-type': 'application/json' },
    body
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
