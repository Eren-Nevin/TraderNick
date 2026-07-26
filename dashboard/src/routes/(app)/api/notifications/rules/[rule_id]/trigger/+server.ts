import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Debug "trigger now": queue an immediate fire of this notification rule,
// bypassing its cadence. The monitor's trigger-poller picks it up within seconds.
export const POST: RequestHandler = async ({ fetch, params }) => {
  const id = encodeURIComponent(params.rule_id ?? '');
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/notifications/rules/${id}/trigger`, {
    method: 'POST'
  });
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
