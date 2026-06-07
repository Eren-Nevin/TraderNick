import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Proxy for the per-event coverage calendar. The dashboard calls this
// once per fill board on the backfill page; admin_server routes each
// call to the owning provider's backfill service.
export const GET: RequestHandler = async ({ fetch, url }) => {
  const qs = url.search; // includes leading '?'
  const upstream = `${INTERNAL_INGESTION_URL}/gaps/calendar${qs}`;
  const res = await fetch(upstream, {
    headers: { Authorization: ingestionAuthHeader() }
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
