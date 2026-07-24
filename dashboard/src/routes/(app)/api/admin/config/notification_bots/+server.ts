import { INTERNAL_INGESTION_URL, ingestionAuthHeader } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Proxy for the notification service's bot registry (user + admin Telegram bot
// tokens). Tokens live server-side / in ClickHouse; this proxy only forwards,
// injecting basic auth so the browser never holds admin credentials.
export const GET: RequestHandler = async ({ fetch }) => {
  const res = await fetch(`${INTERNAL_INGESTION_URL}/config/notification_bots`, {
    headers: { Authorization: ingestionAuthHeader() }
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};

// Set a bot token. Body: {bot: 'user'|'admin', token}. Effective within the
// config cache TTL across the monitor + bot processes — no restart.
export const PUT: RequestHandler = async ({ fetch, request }) => {
  const body = await request.text();
  const res = await fetch(`${INTERNAL_INGESTION_URL}/config/notification_bots`, {
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
