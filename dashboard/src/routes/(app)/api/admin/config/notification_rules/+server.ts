import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Proxy for the notification service's admin-scope rules (the two built-in
// monitors: job failures + stale data) and the static admin topics.
export const GET: RequestHandler = async ({ fetch }) => {
  const res = await fetch(`${INTERNAL_INGESTION_URL}/config/notification_rules`, {
    headers: { Authorization: ingestionAuthHeader() }
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};

// Update an admin rule. Body: {rule_id, enabled?, cadence_s?, cooldown_s?, params?}.
export const PUT: RequestHandler = async ({ fetch, request }) => {
  const body = await request.text();
  const res = await fetch(`${INTERNAL_INGESTION_URL}/config/notification_rules`, {
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
