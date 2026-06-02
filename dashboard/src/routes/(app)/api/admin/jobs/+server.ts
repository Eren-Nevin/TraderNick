import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ fetch, url }) => {
  const limit = url.searchParams.get('limit') ?? '100';
  const res = await fetch(`${INTERNAL_INGESTION_URL}/jobs?limit=${limit}`, {
    headers: { Authorization: ingestionAuthHeader() }
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
