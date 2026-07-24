import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Whether the user Telegram bot is configured — drives the widget's "subscribe
// in the bot" hint. Never returns a token.
export const GET: RequestHandler = async ({ fetch }) => {
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/notifications/bots`);
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  });
};
