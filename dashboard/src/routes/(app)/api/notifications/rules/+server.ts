import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// User (widget) notification rules — proxied to data_server, which writes the
// shared notification_rules + notification_topics ClickHouse tables the monitor
// cron reads. The rule must live server-side (a cron can't read localStorage).
export const GET: RequestHandler = async ({ fetch }) => {
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/notifications/rules`);
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};

// Create / replace a widget rule + its 1:1 topic. Body carries the rule_id
// (the widget instance UUID → globally unique topic).
export const PUT: RequestHandler = async ({ fetch, request }) => {
  const body = await request.text();
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/notifications/rules`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
