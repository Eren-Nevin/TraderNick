import { INTERNAL_DATA_SERVER_URL } from '$lib/server/env';
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ fetch }) => {
  const res = await fetch(`${INTERNAL_DATA_SERVER_URL}/aero/streams`);
  if (!res.ok) throw error(res.status, await res.text());
  return new Response(await res.text(), { status: 200, headers: { 'content-type': 'application/json' } });
};
