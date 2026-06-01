import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ fetch, params }) => {
  const res = await fetch(`${INTERNAL_INGESTION_URL}/jobs/${encodeURIComponent(params.id)}`, {
    headers: { Authorization: ingestionAuthHeader() }
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};

export const DELETE: RequestHandler = async ({ fetch, params }) => {
  const res = await fetch(`${INTERNAL_INGESTION_URL}/jobs/${encodeURIComponent(params.id)}`, {
    method: 'DELETE',
    headers: { Authorization: ingestionAuthHeader() }
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
