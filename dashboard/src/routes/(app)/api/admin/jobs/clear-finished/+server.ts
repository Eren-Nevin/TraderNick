import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ fetch, request }) => {
  const body = await request.text();
  const res = await fetch(`${INTERNAL_INGESTION_URL}/jobs/clear-finished`, {
    method: 'POST',
    headers: {
      Authorization: ingestionAuthHeader(),
      'content-type': 'application/json'
    },
    body: body || '{}'
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
