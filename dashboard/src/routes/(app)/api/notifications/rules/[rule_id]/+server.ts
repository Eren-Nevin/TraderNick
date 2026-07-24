import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Soft-delete a widget's rule + topic (called when the NotificationWidget is
// removed from a page).
export const DELETE: RequestHandler = async ({ fetch, params }) => {
  const id = encodeURIComponent(params.rule_id ?? '');
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/notifications/rules/${id}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
